"""Live smoke tests for Docker daemon connectivity."""

from __future__ import annotations


class TestDocker:
    """Smoke tests requiring a Docker daemon."""

    def test_docker_ping(self, live_docker: None) -> None:
        """Docker daemon is reachable and responds to ping."""
        from docker import from_env

        client = from_env()
        assert client.ping()
