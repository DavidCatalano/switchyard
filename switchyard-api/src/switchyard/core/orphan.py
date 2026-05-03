"""Orphan detection for container recovery.

Scans Docker for containers matching the ``{model}-{backend}-{instance}``
naming convention. Running orphans are adopted into state; crashed
orphans are removed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from switchyard.config.models import Config
from switchyard.core.adapter import DeploymentInfo

logger = logging.getLogger(__name__)

# Pattern: model-backend-N (e.g., qwen-32b-vllm-1)
_CONTAINER_PATTERN = re.compile(r"^(.+)-(.+)-(\d+)$")


class _DockerContainer(Protocol):
    """Minimal Docker container interface for orphan detection."""

    @property
    def name(self) -> str: ...

    @property
    def short_id(self) -> str: ...

    @property
    def attrs(self) -> dict[str, Any]: ...

    def remove(self, *, force: bool) -> None: ...


class _DockerClient(Protocol):
    """Minimal Docker client interface for orphan detection."""

    @property
    def containers(self) -> Any: ...

    def ping(self) -> bool: ...


@dataclass
class OrphanResults:
    """Results from an orphan detection scan."""

    adopted: list[DeploymentInfo]
    removed: list[str]


class OrphanDetector:
    """Detects and handles orphan Docker containers.

    Args:
        docker_client: Docker SDK client instance.
        config: Application configuration.
    """

    def __init__(
        self, docker_client: _DockerClient, config: Config,
    ) -> None:
        self._docker = docker_client
        self._config = config

    def scan(self) -> OrphanResults:
        """Scan for orphan containers and handle them.

        Running orphans are adopted as active deployments.
        Crashed orphans are removed.

        Returns:
            ``OrphanResults`` with adopted deployments and removed IDs.
        """
        adopted: list[DeploymentInfo] = []
        removed: list[str] = []

        # Build a set of known deployment names for matching
        deployment_names = set(self._config.deployments.keys())

        containers = self._docker.containers.list(all=True)

        for container in containers:
            name = container.name
            match = _CONTAINER_PATTERN.match(name)
            if not match:
                continue

            deployment_name, backend, _instance = match.groups()

            # Only handle containers for configured deployments
            if deployment_name not in deployment_names:
                continue

            status = container.attrs["State"]["Status"]

            if status == "running":
                port = self._extract_port(container)
                if port is None:
                    logger.warning(
                        "orphan %s has no port binding, skipping", name,
                    )
                    continue
                info = DeploymentInfo(
                    model_name=deployment_name,
                    backend=backend,
                    port=port,
                    status="running",
                    container_id=container.short_id,
                )
                adopted.append(info)
                logger.info(
                    "adopted orphan %s (deployment=%s port=%d)",
                    name, deployment_name, port,
                )

            elif status in ("exited", "dead", "created"):
                container.remove(force=True)
                removed.append(name)
                logger.info("removed crashed orphan %s", name)

        return OrphanResults(adopted=adopted, removed=removed)

    def _extract_port(self, container: _DockerContainer) -> int | None:
        """Extract host port from container port bindings.

        Checks common vLLM internal ports (8000, 80) since the adapter
        may bind either.
        """
        ports = container.attrs["NetworkSettings"]["Ports"]
        for port_key in ("8000/tcp", "80/tcp"):
            bindings = ports.get(port_key)
            if bindings:
                return int(bindings[0]["HostPort"])
        return None
