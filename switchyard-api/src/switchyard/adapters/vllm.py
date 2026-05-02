"""vLLM backend adapter for Switchyard.

Implements ``BackendAdapter`` for the vLLM inference engine. Handles
container lifecycle (start/stop/health) via the Docker Python SDK and
translates ``VLLMRuntimeConfig`` fields into vLLM CLI arguments.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import docker
import httpx
from docker import DockerClient

from switchyard.config.models import (
    LegacyModelConfig as ModelConfig,
)
from switchyard.config.models import (
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
        """Lazily create a Docker client if not injected."""
        if self._docker_client is not None:
            return self._docker_client
        from switchyard.core.docker import get_docker_client

        self._docker_client = get_docker_client()
        return self._docker_client

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

        # Iterate over all known named fields (excluding model/repo handled above,
        # and extra_args handled below)
        for field_name, value in runtime.model_dump().items():
            if field_name in ("model", "repo", "extra_args", "device"):
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

        # extra_args passthrough (verbatim key → --key value)
        for key, value in runtime.extra_args.items():
            flag = f"--{key}"
            if isinstance(value, bool):
                if value:
                    args.append(flag)
            else:
                args.extend([flag, str(value)])

        return args

    def start(self, model_config: ModelConfig, port: int) -> DeploymentInfo:
        """Start a vLLM container.

        Builds CLI flags from the runtime config, launches the container
        via Docker SDK with port binding and resource limits.

        Args:
            model_config: Full model configuration.
            port: Host port to bind to container port ``_INTERNAL_PORT``.

        Returns:
            ``DeploymentInfo`` describing the running container.

        Raises:
            RuntimeError: If the container fails to start.
        """
        cli_args = self._build_cli_args(model_config.runtime)

        command = (
            ["--host", "0.0.0.0", "--port", str(_INTERNAL_PORT)]
            + cli_args
        )

        # Environment variables
        environment: dict[str, str] = {}
        if model_config.runtime.hf_token:
            environment["HF_TOKEN"] = model_config.runtime.hf_token

        # Resource limits
        kwargs: dict[str, Any] = {
            "image": model_config.image,
            "ports": {_INTERNAL_PORT: port},
            "command": command,
            "remove": False,
        }
        if self._docker_network is not None:
            kwargs["network"] = self._docker_network
        if environment:
            kwargs["environment"] = environment

        # Device-specific configuration
        runtime_device = model_config.runtime.device or "cuda"
        if runtime_device == "cuda":
            # GPU: request all available NVIDIA GPUs
            kwargs["device_requests"] = [
                docker.types.DeviceRequest(
                    driver="nvidia",
                    count=-1,
                    capabilities=[["gpu"]],
                )
            ]
        else:
            # CPU: set vLLM CPU-specific environment variables
            environment.setdefault("VLLM_CPU_KVCACHE_SPACE", "4")
            environment.setdefault("VLLM_CPU_NUM_OF_RESERVED_CPU", "1")
            kwargs["environment"] = environment
        if model_config.resources.memory:
            kwargs["mem_limit"] = model_config.resources.memory

        logger.info(
            "starting vllm container image=%s port=%d args=%s",
            model_config.image,
            port,
            cli_args,
        )

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
