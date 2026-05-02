"""Tests for the Docker client factory.

Validates:
- Factory uses SWITCHYARD_DOCKER_HOST when set
- Factory falls back to docker.from_env() when unset
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestDockerClientFactory:
    """Docker client factory tests."""

    @pytest.mark.no_isolate
    def test_uses_explicit_docker_host(self) -> None:
        """Factory uses SWITCHYARD_DOCKER_HOST when set."""
        with (
            patch("switchyard.core.docker.docker") as mock_docker,
            patch("switchyard.core.docker.AppSettings") as mock_settings,
        ):
            mock_client = MagicMock()
            mock_docker.DockerClient.return_value = mock_client
            mock_settings.return_value.docker_host = "tcp://127.0.0.1:2375"

            from switchyard.core.docker import get_docker_client

            client = get_docker_client()

            mock_docker.DockerClient.assert_called_once_with(
                base_url="tcp://127.0.0.1:2375",
            )
            assert client is mock_client

    def test_falls_back_to_from_env(self) -> None:
        """Factory falls back to docker.from_env() when env var is unset."""
        with (
            patch("switchyard.core.docker.docker") as mock_docker,
            patch("switchyard.core.docker.AppSettings") as mock_settings,
        ):
            mock_client = MagicMock()
            mock_docker.from_env.return_value = mock_client
            mock_settings.return_value.docker_host = None

            from switchyard.core.docker import get_docker_client

            client = get_docker_client()

            mock_docker.from_env.assert_called_once_with()
            assert client is mock_client
