"""Tests for error handling on proxy and deployment routes.

Validates:
- Proxy returns appropriate errors when no active deployment
- Unknown deployment names return 404
- Various error conditions on passthrough
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from switchyard.app import create_app


@pytest.fixture(autouse=True)
def _mock_config_loader():
    from switchyard.config.models import Config
    config = Config.model_validate({
        "hosts": {
            "test-host": {
                "stores": {
                    "models": {
                        "host_path": "/data/models",
                        "container_path": "/models",
                    },
                },
                "port_range": [9000, 9100],
            },
        },
        "runtimes": {"vllm": {"backend": "vllm"}},
        "models": {
            "test-model": {
                "source": {"store": "models", "path": "test-model"},
            },
        },
        "deployments": {
            "test-deployment": {
                "model": "test-model",
                "runtime": "vllm",
                "host": "test-host",
            },
        },
    })
    with patch("switchyard.app.ConfigLoader.load", return_value=config):
        yield


@pytest.fixture(autouse=True)
def _mock_active_host():
    with patch.dict("os.environ", {"SWITCHYARD_ACTIVE_HOST": "test-host"}):
        yield


@pytest.fixture(autouse=True)
def _mock_docker():
    """Prevent Docker connections during API tests."""
    with patch("docker.from_env") as mock:
        mock.return_value = MagicMock()
        mock.return_value.ping.return_value = True
        yield mock


class TestProxyErrors:
    """Error handling on OpenAI passthrough routes."""

    def test_chat_completions_no_deployment(self) -> None:
        """Returns 404 when deployment not found."""
        app = create_app()
        # Real manager's state is empty -> 404
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent"},
        )
        assert resp.status_code == 404

    def test_backends_passthrough_unknown_deployment(self) -> None:
        """Returns 404 for deployment name not in state."""
        app = create_app()
        # Real manager's state is empty -> 404
        client = TestClient(app)
        resp = client.post("/v1/backends/nonexistent/models", json={})
        assert resp.status_code == 404

    def test_chat_completions_upstream_timeout(self) -> None:
        """Returns 504 when backend request times out."""
        from switchyard.core.adapter import DeploymentInfo

        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.TimeoutException("timeout")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        app = create_app()
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="running",
            container_id="abc123",
            metadata={
                "backend_host": "127.0.0.1",
                "backend_scheme": "http",
            },
        )
        app.state.manager.state.add(info)

        with patch("switchyard.app.httpx.Client", return_value=mock_client):
            client = TestClient(app)
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "test-deployment", "messages": []},
            )
        assert resp.status_code == 504

    def test_backends_passthrough_upstream_error(self) -> None:
        """Returns backend error code for backend passthrough."""
        from switchyard.core.adapter import DeploymentInfo

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.json.return_value = {"error": "rate limited"}
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        app = create_app()
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="running",
            container_id="abc123",
            metadata={
                "backend_host": "127.0.0.1",
                "backend_scheme": "http",
            },
        )
        app.state.manager.state.add(info)

        with patch("switchyard.app.httpx.Client", return_value=mock_client):
            client = TestClient(app)
            resp = client.post(
                "/v1/backends/test-deployment/models",
                json={},
            )
        assert resp.status_code == 429
        assert resp.json()["error"] == "rate limited"
