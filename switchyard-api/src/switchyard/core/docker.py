"""Docker client factory for Switchyard.

Centralizes Docker SDK client creation so that local and remote Docker
configurations are explicit and testable.
"""

from __future__ import annotations

import docker

from switchyard.config.loader import AppSettings


def get_docker_client() -> docker.DockerClient:
    """Create a Docker SDK client respecting environment configuration.

    Uses ``SWITCHYARD_DOCKER_HOST`` (via pydantic-settings ``AppSettings``)
    as ``base_url`` when set. Otherwise falls back to ``docker.from_env()``,
    which respects the native ``DOCKER_HOST`` and ``DOCKER_TLS_VERIFY``
    environment variables.

    Returns:
        A configured ``docker.DockerClient`` instance.
    """
    settings = AppSettings()
    if settings.docker_host is not None:
        return docker.DockerClient(base_url=settings.docker_host)
    return docker.from_env()
