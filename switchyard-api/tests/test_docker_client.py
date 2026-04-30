"""Tests for the Docker client factory.

Validates:
- Factory uses SWITCHYARD_DOCKER_HOST when set
- Factory falls back to docker.from_env() when unset
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


class TestDockerClientFactory:
    """Docker client factory tests."""

    @pytest.mark.no_isolate
    def test_uses_explicit_docker_host(self) -> None:
        """Factory uses SWITCHYARD_DOCKER_HOST when set."""
        env_path = os.environ
        os.environ = dict(env_path)  # type: ignore[assignment]
        os.environ["SWITCHYARD_DOCKER_HOST"] = "tcp://127.0.0.1:2375"
        try:
            with patch("switchyard.core.docker.docker") as mock_docker:
                mock_client = MagicMock()
                mock_docker.DockerClient.return_value = mock_client
                mock_docker.from_env.return_value = mock_client

                from switchyard.core.docker import get_docker_client

                client = get_docker_client()

                mock_docker.DockerClient.assert_called_once_with(
                    base_url="tcp://127.0.0.1:2375"
                )
                assert client is mock_client
        finally:
            os.environ = env_path

    def test_falls_back_to_from_env(self) -> None:
        """Factory falls back to docker.from_env() when env var is unset."""
        env_path = os.environ
        os.environ = dict(env_path)  # type: ignore[assignment]
        os.environ.pop("SWITCHYARD_DOCKER_HOST", None)
        try:
            with patch("switchyard.core.docker.docker") as mock_docker:
                mock_client = MagicMock()
                mock_docker.DockerClient.return_value = mock_client
                mock_docker.from_env.return_value = mock_client

                # Re-import to pick up fresh env
                from switchyard.core import docker as docker_module

                # Force module reload for clean state
                with patch.object(
                    docker_module, "AppSettings",
                    return_value=MagicMock(docker_host=None),
                ):
                    client = docker_module.get_docker_client()

                    mock_docker.from_env.assert_called_once_with()
                    assert client is mock_client
        finally:
            os.environ = env_path
