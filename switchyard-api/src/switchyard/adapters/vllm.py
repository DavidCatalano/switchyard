"""vLLM backend adapter for Switchyard.

Implements ``BackendAdapter`` for the vLLM inference engine. Handles
container lifecycle (start/stop/health) via the Docker Python SDK and
translates ``VLLMRuntimeConfig`` fields into vLLM CLI arguments.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
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


# Keys that start() force-applies over user container_options, so the
# preview should not show user-set values that would be silently discarded.
_CONTAINER_OWNED_KEYS = frozenset(
    {"labels", "name", "volumes", "ports", "command", "environment", "device_requests"},
)


@dataclass
class _CommonLaunchFields:
    """Fields shared by build_launch_spec and build_preview_spec.

    Extracted so a single change updates both paths, mitigating PRD Risk 4
    (preview drift).
    """

    runtime: VLLMRuntimeConfig
    internal_port: int
    command: list[str]
    environment: dict[str, str]
    labels: dict[str, str]
    container_options: dict[str, Any]
    served_model_name: str
    device_type: str


def _compute_common_launch_fields(resolved: ResolvedDeployment) -> _CommonLaunchFields:
    """Compute fields shared by both launch and preview spec builders.

    Validates runtime args, builds CLI args + command prefix, assembles
    environment (with hf_token and CPU defaults), merges labels, and
    normalizes container options.

    Args:
        resolved: Fully resolved deployment configuration.

    Returns:
        A ``_CommonLaunchFields`` namedtuple.
    """
    runtime = VLLMRuntimeConfig.model_validate(resolved.runtime_args)
    internal_port = resolved.internal_port or _INTERNAL_PORT
    device_type = runtime.device or "cuda"

    # CLI command: --host/--port prefix + adapter-built args
    command = (
        ["--host", "0.0.0.0", "--port", str(internal_port)]
        + VLLMAdapter._build_cli_args(runtime)
    )

    # Container environment
    environment: dict[str, str] = dict(resolved.container_environment)
    # hf_token renders to BOTH CLI and env. Intentional: vLLM reads from both.
    # See SEP-004-05-CONTEXT-runtime-field-audit.md, Observation #2.
    if runtime.hf_token:
        environment["HF_TOKEN"] = runtime.hf_token
    if device_type == "cpu":
        environment.setdefault("VLLM_CPU_KVCACHE_SPACE", "4")
        environment.setdefault("VLLM_CPU_NUM_OF_RESERVED_CPU", "1")

    # Container ownership labels
    _switchyard_labels: dict[str, str] = {
        "switchyard.managed": "true",
        "switchyard.deployment": resolved.deployment_name,
        "switchyard.model": resolved.model_name,
        "switchyard.runtime": resolved.runtime_name,
        "switchyard.host": resolved.host_name,
    }

    # Normalize container options
    container_options = _normalize_container_options(resolved.container_options)

    # Extract user labels before stripping owned keys
    user_labels = container_options.pop("labels", {})

    # Strip keys that start() force-applies (would silently discard user values)
    for key in _CONTAINER_OWNED_KEYS:
        container_options.pop(key, None)

    # Merge user labels with Switchyard labels
    labels = {**user_labels, **_switchyard_labels}

    served_model_name = (
        runtime.served_model_name or runtime.model
        or runtime.repo or resolved.deployment_name
    )

    return _CommonLaunchFields(
        runtime=runtime,
        internal_port=internal_port,
        command=command,
        environment=environment,
        labels=labels,
        container_options=container_options,
        served_model_name=served_model_name,
        device_type=device_type,
    )


class LaunchSpec(TypedDict):
    """Structured container launch specification.

    Returned by ``VLLMAdapter.build_launch_spec()`` and consumed by
    ``start()`` (for Docker SDK ``containers.run()``). Contains Docker SDK
    types like ``DeviceRequest`` that are not JSON-serializable.

    For preview API responses, use ``build_preview_spec()`` instead.
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


class PreviewSpec(TypedDict):
    """Operator-facing container launch preview.

    Returned by ``VLLMAdapter.build_preview_spec()`` for the preview API.
    All fields are JSON-serializable and use Switchyard domain language
    (accelerator_ids, device_type) instead of Docker SDK types.

    Port semantics:
        ``host_port`` is the actual bound port when a container exists
        (loading / running / error) or ``None`` when stopped.
        ``port_range`` provides host port allocation context regardless
        of deployment status.

    Status semantics:
        ``deployment_status``, ``container_status``, and ``docker_error``
        are resolved at the API route level and forwarded to this spec.
    """

    image: str
    command: list[str]
    environment: dict[str, str]
    ports: dict[int, int]
    labels: dict[str, str]
    mounts: dict[str, dict[str, str]]
    network: str | None
    host_port: int | None
    port_range: list[int]
    container_options: dict[str, Any]
    accelerator_ids: list[str]
    device_type: str
    served_model_name: str
    deployment_status: str
    container_status: str
    docker_error: str | None


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
        Both ``start()`` consumes this dict for Docker SDK ``containers.run()``.
        For preview API responses, use ``build_preview_spec()`` instead
        (which returns a JSON-serializable dict without Docker SDK types).

        Args:
            resolved: Fully resolved deployment configuration.
            port: Host port to bind to container port.

        Returns:
            A ``LaunchSpec`` dict with all launch configuration fields.
        """
        common = _compute_common_launch_fields(resolved)

        # Device-specific configuration
        device_requests: list[DeviceRequest] | None = None
        if common.device_type == "cuda":
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

        return LaunchSpec(
            image=resolved.image,
            command=common.command,
            environment=common.environment,
            ports={common.internal_port: port},
            labels=common.labels,
            volumes=resolved.store_mounts,
            name=f"switchyard-{resolved.deployment_name}",
            network=resolved.docker_network or None,
            device_requests=device_requests,
            container_options=common.container_options,
            served_model_name=common.served_model_name,
        )

    def build_preview_spec(
        self,
        resolved: ResolvedDeployment,
        *,
        host_port: int | None = None,
        port_range: list[int] | None = None,
        deployment_status: str = "stopped",
        container_status: str = "missing",
        docker_error: str | None = None,
    ) -> PreviewSpec:
        """Build an operator-facing container launch preview.

        Unlike ``build_launch_spec()``, this returns a JSON-serializable
        dict using Switchyard domain language (accelerator_ids, device_type)
        instead of Docker SDK types (DeviceRequest).

        Args:
            resolved: Fully resolved deployment configuration.
            host_port: Actual host port if deployment has a running container,
                ``None`` when stopped.
            port_range: Host port allocation range for context.
            deployment_status: Current Switchyard status
                (loading / running / error / stopped).
            container_status: Docker container observation
                (running / exited / missing / unknown).
            docker_error: Error message if Docker was unreachable.

        Returns:
            A ``PreviewSpec`` dict with all preview configuration fields.
        """
        common = _compute_common_launch_fields(resolved)

        return PreviewSpec(
            image=resolved.image,
            command=common.command,
            environment=common.environment,
            ports={common.internal_port: host_port} if host_port is not None else {},
            labels=common.labels,
            mounts=resolved.store_mounts,
            network=resolved.docker_network or None,
            host_port=host_port,
            port_range=port_range or resolved.port_range,
            container_options=common.container_options,
            accelerator_ids=resolved.accelerator_ids,
            device_type=common.device_type,
            deployment_status=deployment_status,
            container_status=container_status,
            docker_error=docker_error,
            served_model_name=common.served_model_name,
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

        # Apply user container options first, then force-apply Switchyard-owned
        # fields on top. This prevents user options from overriding managed
        # fields like name, volumes, labels, and device_requests.
        kwargs: dict[str, Any] = {}

        # Layer 1: user container options (Docker SDK kwargs)
        for opt_key, opt_value in spec["container_options"].items():
            if opt_key in kwargs and isinstance(kwargs[opt_key], dict):
                kwargs[opt_key].update(opt_value)
            else:
                kwargs[opt_key] = opt_value

        # Layer 2: Switchyard-owned fields (always take precedence)
        kwargs["image"] = spec["image"]
        kwargs["command"] = spec["command"]
        kwargs["ports"] = spec["ports"]
        kwargs["labels"] = spec["labels"]
        kwargs["volumes"] = spec["volumes"]
        kwargs["name"] = spec["name"]
        kwargs["remove"] = False
        if spec["network"]:
            kwargs["network"] = spec["network"]
        if spec["environment"]:
            kwargs["environment"] = spec["environment"]
        if spec["device_requests"] is not None:
            kwargs["device_requests"] = spec["device_requests"]

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
