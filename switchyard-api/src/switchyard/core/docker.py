"""Docker client factory for Switchyard.

Centralizes Docker SDK client creation so that local and remote Docker
configurations are explicit and testable. Provides a host-aware factory
for multi-host reconciliation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

import docker

from switchyard.config.models import AppSettings

DockerClientFactory = Callable[[str | None], docker.DockerClient]


class _DockerContainer(Protocol):
    """Minimal Docker container interface for reconciliation."""

    @property
    def labels(self) -> dict[str, Any]: ...

    @property
    def short_id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def attrs(self) -> dict[str, Any]: ...

    def remove(self, *, force: bool) -> None: ...


def get_docker_client(docker_host: str | None = None) -> docker.DockerClient:
    """Create a Docker SDK client for the given host.

    Args:
        docker_host: Optional host-specific Docker daemon address.
            If set, used as ``base_url``. Otherwise falls back to
            ``SWITCHYARD_DOCKER_HOST`` from ``AppSettings``, then to
            ``docker.from_env()``.

    Returns:
        A configured ``docker.DockerClient`` instance.
    """
    if docker_host is not None:
        return docker.DockerClient(base_url=docker_host)
    settings = AppSettings()
    if settings.docker_host is not None:
        return docker.DockerClient(base_url=settings.docker_host)
    return docker.from_env()


def create_default_factory() -> DockerClientFactory:
    """Create the default host-aware Docker client factory.

    Returns a callable that produces a ``docker.DockerClient`` for the
    given ``docker_host`` hint.
    """
    return get_docker_client


def find_container_by_labels(
    client: docker.DockerClient,
    deployment_name: str,
) -> _DockerContainer | None:
    """Find a Switchyard-managed container by deployment label.

    Looks for a container labelled with ``switchyard.managed=true`` and
    ``switchyard.deployment={deployment_name}``.

    Args:
        client: Docker SDK client.
        deployment_name: The deployment logical name.

    Returns:
        The container if found, ``None`` otherwise.
    """
    filters: dict[str, list[str]] = {
        "label": [
            "switchyard.managed=true",
            f"switchyard.deployment={deployment_name}",
        ],
    }
    containers = client.containers.list(all=True, filters=filters)  # type: ignore[arg-type]
    if not containers:
        return None
    return cast(_DockerContainer, containers[0])


def get_container_status(container: _DockerContainer) -> str:
    """Extract the normalized Docker container status.

    Returns one of: ``running``, ``exited``, ``dead``, ``created``,
    ``paused``, ``restarting``.
    """
    attrs = container.attrs
    return cast(str, attrs["State"]["Status"])


def get_container_host_port(
    container: _DockerContainer, internal_port: int,
) -> int | None:
    """Extract the host port bound to a given internal port.

    Args:
        container: The Docker container.
        internal_port: The container's internal port (e.g. 8000).

    Returns:
        The host port if bound, ``None`` otherwise.
    """
    ports = container.attrs["NetworkSettings"]["Ports"]
    port_key = f"{internal_port}/tcp"
    bindings = ports.get(port_key)
    if bindings:
        return int(bindings[0]["HostPort"])
    return None
