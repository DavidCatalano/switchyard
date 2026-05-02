"""Error handling tests — T4.8 (spec §13 error codes).

Tests that API endpoints return correct HTTP status codes
for various error conditions (500, 503, 504).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ConnectError, TimeoutException

from switchyard.app import create_app
from switchyard.config.models import (
    GlobalConfig,
    RuntimeDefaults,
)
from switchyard.config.models import (
    LegacyConfig as Config,
)


def _mock_deployment(status: str = "running", port: int = 8000) -> SimpleNamespace:
    """Create a mock deployment info."""
    return SimpleNamespace(
        model_name="qwen-32b",
        backend="vllm",
        port=port,
        status=status,
        started_at=None,
        metadata={},
    )


@pytest.fixture
def app():
    """Create a fresh FastAPI app with mocked lifecycle manager."""
    manager = MagicMock()
    manager.state.get = MagicMock()
    manager.state.list_deployments = MagicMock(return_value=[])

    config = Config(
        global_config=GlobalConfig(
            log_level="debug",
            backend_host="localhost",
            backend_scheme="http",
        ),
        runtime_defaults=RuntimeDefaults(),
        models={},
    )

    with (
        patch("switchyard.app.ConfigLoader.load", return_value=config),
        patch("switchyard.app.LifecycleManager", return_value=manager),
    ):
        a = create_app()
        a.state.config = config
        a.state.manager = manager
    return a, manager  # type: ignore[return-value]


@pytest.fixture
def client(app):
    """TestClient backed by the app fixture."""
    a, _ = app
    return TestClient(a), app[1]  # type: ignore[name-defined]


# ---------------------------------------------------------------------------
# T4.8 — Error handling per spec §13
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Error code coverage for proxy endpoints."""

    def test_503_backend_unhealthy(self, client):
        """Backend returns 503 — proxy forwards it."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="running", port=8000)

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.json.return_value = {"error": "backend unhealthy"}

        with patch("switchyard.app.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client_cls.return_value.__exit__.return_value = False

            resp = tc.post(
                "/v1/chat/completions",
                json={"model": "qwen-32b", "messages": []},
            )
            assert resp.status_code == 503

    def test_500_container_crash(self, client):
        """Backend returns 500 — proxy forwards it."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="running", port=8000)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "internal server error"}

        with patch("switchyard.app.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client_cls.return_value.__exit__.return_value = False

            resp = tc.post(
                "/v1/chat/completions",
                json={"model": "qwen-32b", "messages": []},
            )
            assert resp.status_code == 500

    def test_504_request_timeout(self, client):
        """Connection timeout returns 504."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="running", port=8000)

        with patch("switchyard.app.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.side_effect = TimeoutException("connection timed out")
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client_cls.return_value.__exit__.return_value = False

            resp = tc.post(
                "/v1/chat/completions",
                json={"model": "qwen-32b", "messages": []},
            )
            assert resp.status_code == 504

    def test_503_connection_refused(self, client):
        """Connection refused returns 503."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="running", port=8000)

        with patch("switchyard.app.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.side_effect = ConnectError("connection refused")
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client_cls.return_value.__exit__.return_value = False

            resp = tc.post(
                "/v1/chat/completions",
                json={"model": "qwen-32b", "messages": []},
            )
            assert resp.status_code == 503

    def test_passthrough_500(self, client):
        """Passthrough forwards backend 500 errors."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="running", port=8000)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "upstream error"}

        with patch("switchyard.app.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client_cls.return_value.__exit__.return_value = False

            resp = tc.post(
                "/v1/backends/qwen-32b/v1/embeddings",
                json={"input": "test"},
            )
            assert resp.status_code == 500
