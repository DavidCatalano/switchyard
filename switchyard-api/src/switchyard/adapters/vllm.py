"""vLLM backend adapter for Switchyard.

Implements ``BackendAdapter`` for the vLLM inference engine. Handles
container lifecycle (start/stop/health) via the Docker Python SDK and
translates ``VLLMRuntimeConfig`` fields into vLLM CLI arguments.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

import docker
import httpx
from docker import DockerClient
from docker.types import DeviceRequest

from switchyard.config.models import (
    ResolvedDeployment,
    VLLMRuntimeConfig,
)
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.registry import AdapterRegistry

logger = logging.getLogger(__name__)

# Internal port that vLLM binds to inside the container.
_INTERNAL_PORT = 8000

# Mapping from Pydantic field name → vLLM CLI flag.
# Omitted fields use the default convention: snake_case → --kebab-case.
_FLAG_OVERRIDE: dict[str, str] = {}


def _field_to_flag(field_name: str) -> str:
    """Convert a Pydantic field name to a vLLM CLI flag.

    Uses explicit overrides from ``_FLAG_OVERRIDE`` where needed,
    otherwise converts ``snake_case`` to ``--kebab-case``.
    """
    if field_name in _FLAG_OVERRIDE:
        return _FLAG_OVERRIDE[field_name]
    return "--" + field_name.replace("_", "-")


def _normalize_container_options(options: dict[str, Any]) -> dict[str, Any]:
    """Translate config-shaped container options to Docker SDK kwargs."""
    normalized = dict(options)

    ipc = normalized.pop("ipc", None)
    if ipc is not None:
        normalized["ipc_mode"] = ipc

    ulimits = normalized.get("ulimits")
    if isinstance(ulimits, dict):
        normalized["ulimits"] = [
            docker.types.Ulimit(
                name=name,
                soft=limits.get("soft"),
                hard=limits.get("hard"),
            )
            for name, limits in ulimits.items()
        ]

    return normalized


class LaunchSpec(TypedDict):
    """Structured container launch specification.

    Returned by ``VLLMAdapter.build_launch_spec()`` and consumed by both
    ``start()`` (for Docker SDK ``containers.run()``) and the preview API
    (for structured JSON responses). This shared path prevents drift
    between what operators see in preview and what actually launches.
    """

    image: str
    command: list[str]
    environment: dict[str, str]
    ports: dict[int, int]
    labels: dict[str, str]
    volumes: dict[str, dict[str, str]]
    name: str
    network: str | None
    device_requests: list[DeviceRequest] | None
    container_options: dict[str, Any]
    served_model_name: str


class VLLMAdapter(BackendAdapter):
    """Concrete adapter for vLLM backend containers.

    Translates ``VLLMRuntimeConfig`` fields into vLLM ``serve`` CLI flags,
    manages Docker container lifecycle, and performs HTTP health checks.

    Args:
        docker_client: Docker SDK client instance.
        backend_host: Hostname or IP the control plane uses to reach
            published container ports (default ``"localhost"``).
        backend_scheme: URL scheme for backend HTTP calls (default ``"http"``).
    """

    def __init__(
        self,
        docker_client: DockerClient | None = None,
        backend_host: str = "localhost",
        backend_scheme: str = "http",
        docker_network: str | None = None,
    ) -> None:
        self._docker_client = docker_client
        self._backend_host = backend_host
        self._backend_scheme = backend_scheme
        self._docker_network = docker_network

    @property
    def _client(self) -> DockerClient:
        """Lazily create a Docker client if not injected.

        When creating a client from ``resolved.docker_host``, this is set
        via ``_set_docker_client()`` before ``_client`` is accessed.
        Falls back to ``core.docker.get_docker_client()`` (which reads
        ``.env``) if no explicit host was configured.
        """
        if self._docker_client is not None:
            return self._docker_client
        from switchyard.core.docker import get_docker_client

        self._docker_client = get_docker_client()
        return self._docker_client

    def _set_docker_client(self, base_url: str) -> None:
        """Create a Docker client with an explicit base_url.

        Used by ``start()`` to honour ``resolved.docker_host`` when no
        client was injected at construction time.
        """
        self._docker_client = docker.DockerClient(base_url=base_url)

    @staticmethod
    def _build_cli_args(runtime: VLLMRuntimeConfig) -> list[str]:
        """Build the vLLM CLI argument list from runtime config.

        Named Pydantic fields are translated to ``--flag value`` pairs.
        ``extra_args`` are passed through verbatim.
        Boolean fields appear as standalone flags only when ``True``.
        Dict fields are JSON-serialized.
        ``None`` values are omitted entirely.

        Args:
            runtime: The resolved runtime configuration for a model.

        Returns:
            A flat list of CLI arguments
        """
        args: list[str] = []

        # Determine model source for --model flag
        model_source = runtime.model or runtime.repo
        if model_source:
            args.extend(["--model", model_source])

        # Derive switchyard-only fields from Pydantic model metadata.
        # These fields shape container construction but must never reach CLI.
        _switchyard_only = {
            name
            for name, fi in VLLMRuntimeConfig.model_fields.items()
            if isinstance(fi.json_schema_extra, dict)
            and fi.json_schema_extra.get("switchyard_only")
        }

        # Iterate over all known named fields (excluding model/repo handled above,
        # extra_args handled below, and switchyard-only fields)
        for field_name, value in runtime.model_dump().items():
            if field_name in ("model", "repo", "extra_args"):
                continue
            if field_name in _switchyard_only:
                continue
            if value is None:
                continue

            flag = _field_to_flag(field_name)

            if isinstance(value, bool):
                if value:
                    args.append(flag)
                # False → omit
            elif isinstance(value, dict):
                # Dict fields (speculative_config, limit_mm_per_prompt) → JSON
                args.extend([flag, json.dumps(value)])
            else:
                args.extend([flag, str(value)])

        # extra_args passthrough: normalize keys through snake→kebab conversion
        # and JSON-serialize dict values (matching named dict fields).
        for key, value in runtime.extra_args.items():
            flag = _field_to_flag(key)
            if isinstance(value, bool):
                if value:
                    args.append(flag)
            elif isinstance(value, dict):
                args.extend([flag, json.dumps(value)])
            else:
                args.extend([flag, str(value)])

        return args

    def build_launch_spec(
        self, resolved: ResolvedDeployment, port: int,
    ) -> LaunchSpec:
        """Build the full structured container launch specification.

        This is the single authoritative source for what gets launched.
        Both ``start()`` and the preview API consume this dict to prevent
        drift between what operators see and what actually runs.

        Args:
            resolved: Fully resolved deployment configuration.
            port: Host port to bind to container port.

        Returns:
            A ``LaunchSpec`` dict with all launch configuration fields.
        """
        runtime = VLLMRuntimeConfig.model_validate(resolved.runtime_args)
        cli_args = self._build_cli_args(runtime)

        # Use resolved internal port (or fall back to default)
        internal_port = resolved.internal_port or _INTERNAL_PORT

        command = (
            ["--host", "0.0.0.0", "--port", str(internal_port)]
            + cli_args
        )

        # Container environment
        environment: dict[str, str] = dict(resolved.container_environment)
        # hf_token renders to BOTH CLI (--hf-token via _build_cli_args) and
        # container env (HF_TOKEN). This is intentional: vLLM reads from both
        # paths. Do not remove one path thinking it's dead code.
        # See SEP-004-05-CONTEXT-runtime-field-audit.md, Observation #2.
        if runtime.hf_token:
            environment["HF_TOKEN"] = runtime.hf_token

        # Container ownership labels — identify Switchyard-managed containers.
        # Defined here so they can be force-applied after user container_options merge.
        _switchyard_labels: dict[str, str] = {
            "switchyard.managed": "true",
            "switchyard.deployment": resolved.deployment_name,
            "switchyard.model": resolved.model_name,
            "switchyard.runtime": resolved.runtime_name,
            "switchyard.host": resolved.host_name,
        }

        # Merge container options after translating config keys to Docker SDK kwargs.
        container_options = _normalize_container_options(
            resolved.container_options
        )

        # Force-apply Switchyard ownership labels after user container_options merge.
        # User labels (if any) are preserved; Switchyard labels always take precedence.
        user_labels = container_options.pop("labels", {})
        labels = {**user_labels, **_switchyard_labels}

        # Device-specific configuration
        runtime_device = runtime.device or "cuda"
        device_requests: list[DeviceRequest] | None = None
        if runtime_device == "cuda":
            if resolved.accelerator_ids:
                device_requests = [
                    DeviceRequest(
                        driver="nvidia",
                        device_ids=resolved.accelerator_ids,
                        capabilities=[["gpu"]],
                    )
                ]
            else:
                device_requests = [
                    DeviceRequest(
                        driver="nvidia",
                        count=-1,
                        capabilities=[["gpu"]],
                    )
                ]
        else:
            # CPU: set vLLM CPU-specific environment variables
            environment.setdefault("VLLM_CPU_KVCACHE_SPACE", "4")
            environment.setdefault("VLLM_CPU_NUM_OF_RESERVED_CPU", "1")

        return LaunchSpec(
            image=resolved.image,
            command=command,
            environment=environment,
            ports={internal_port: port},
            labels=labels,
            volumes=resolved.store_mounts,
            name=f"switchyard-{resolved.deployment_name}",
            network=resolved.docker_network or None,
            device_requests=device_requests,
            container_options=container_options,
            served_model_name=runtime.served_model_name
            or runtime.model
            or runtime.repo
            or resolved.deployment_name,
        )

    def start(self, resolved: ResolvedDeployment, port: int) -> DeploymentInfo:
        """Start a vLLM container.

        Builds the launch spec via ``build_launch_spec()`` and passes it to
        Docker SDK via ``containers.run()``.

        Args:
            resolved: Fully resolved deployment configuration.
            port: Host port to bind to container port.

        Returns:
            ``DeploymentInfo`` describing the running container.

        Raises:
            RuntimeError: If the container fails to start.
        """
        spec = self.build_launch_spec(resolved, port)

        # Build Docker SDK kwargs from the launch spec.
        # ``remove: False`` is a Docker SDK lifecycle option (not auto-remove on
        # stop), not a launch configuration concern — stays in start() only.
        kwargs: dict[str, Any] = {
            "image": spec["image"],
            "command": spec["command"],
            "ports": spec["ports"],
            "labels": spec["labels"],
            "volumes": spec["volumes"],
            "name": spec["name"],
            "remove": False,
        }
        if spec["network"]:
            kwargs["network"] = spec["network"]
        if spec["environment"]:
            kwargs["environment"] = spec["environment"]
        if spec["device_requests"] is not None:
            kwargs["device_requests"] = spec["device_requests"]

        # Merge container options (Docker SDK kwargs) after launch spec.
        for opt_key, opt_value in spec["container_options"].items():
            if opt_key in kwargs and isinstance(kwargs[opt_key], dict):
                kwargs[opt_key].update(opt_value)
            else:
                kwargs[opt_key] = opt_value

        logger.info(
            "starting vllm container image=%s port=%d args=%s",
            spec["image"],
            port,
            spec["command"][4:],  # skip --host/--port prefix for log clarity
        )

        # Use resolved.docker_host for Docker client if not injected.
        # This ensures the canonical host docker_host from config.yaml
        # is honoured even without a duplicate .env setting.
        if self._docker_client is None and resolved.docker_host:
            self._set_docker_client(resolved.docker_host)

        try:
            container = self._client.containers.run(**kwargs, detach=True)
        except Exception as exc:
            raise RuntimeError(
                f"failed to start vLLM container: {exc}"
            ) from exc

        container_id = container.short_id
        logger.info(
            "vllm container started id=%s port=%d", container_id, port
        )

        return DeploymentInfo(
            model_name="",  # set by LifecycleManager
            backend="vllm",
            port=port,
            status="loading",
            container_id=container_id,
            metadata={
                "backend_host": self._backend_host,
                "backend_scheme": self._backend_scheme,
                "served_model_name": spec["served_model_name"],
            },
        )

    def stop(self, deployment: DeploymentInfo) -> None:
        """Stop and remove the vLLM container.

        Args:
            deployment: The deployment to stop.
        """
        try:
            container = self._client.containers.get(deployment.container_id)
            container.stop(timeout=30)
            container.remove()
            logger.info(
                "vllm container stopped id=%s", deployment.container_id
            )
        except Exception as exc:
            # docker.errors.NotFound is a subclass of Exception
            logger.warning(
                "vllm container %s not found or error during stop: %s",
                deployment.container_id,
                exc,
            )

    def health(self, deployment: DeploymentInfo) -> str:
        """Check vLLM container health via GET /health.

        Uses ``backend_host`` from deployment metadata (or falls back to
        the adapter's configured default).

        Args:
            deployment: The deployment to check.

        Returns:
            ``"running"`` if the health endpoint returns 200, ``"error"`` otherwise.
        """
        host = deployment.metadata.get("backend_host", self._backend_host)
        scheme = deployment.metadata.get("backend_scheme", self._backend_scheme)
        url = f"{scheme}://{host}:{deployment.port}/health"
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(url)
            return "running" if response.status_code == 200 else "error"
        except httpx.HTTPError:
            return "error"

    def endpoint(self, deployment: DeploymentInfo) -> str:
        """Return the HTTP endpoint URL for the vLLM container.

        Uses ``backend_host`` from deployment metadata (or falls back to
        the adapter's configured default).

        Args:
            deployment: The deployment to get the endpoint for.

        Returns:
            Base URL (e.g. ``"http://localhost:8001"``).
        """
        host = deployment.metadata.get("backend_host", self._backend_host)
        scheme = deployment.metadata.get("backend_scheme", self._backend_scheme)
        return f"{scheme}://{host}:{deployment.port}"


def register_vllm(registry: AdapterRegistry) -> None:
    """Register the vLLM adapter with the given registry.

    Args:
        registry: The adapter registry to register with.
    """
    registry.register("vllm", VLLMAdapter)
