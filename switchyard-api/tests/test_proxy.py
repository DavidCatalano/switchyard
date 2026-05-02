"""Tests for OpenAI-compatible proxy passthrough routes.

Validates:
- /v1/chat/completions forwards to active deployment
- /v1/backends/{deployment}/{path:path} forwards to active deployment
- Proxy returns errors when deployment not found or not running
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


class TestChatCompletionsProxy:
    """Tests for POST /v1/chat/completions passthrough."""

    def test_no_active_deployment_returns_404(self) -> None:

        app = create_app()
        # Real manager's state is empty, so deployment not found -> 404

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent"},
        )
        assert resp.status_code == 404


class TestBackendsPassthrough:
    """Tests for /v1/backends/{deployment}/{path:path} proxy."""

    def test_unknown_deployment_returns_404(self) -> None:
        app = create_app()
        # Real manager's state is empty, so deployment not found -> 404

        client = TestClient(app)
        resp = client.post("/v1/backends/nonexistent/models", json={})
        assert resp.status_code == 404

    def test_deployment_not_running_returns_400(self) -> None:
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        # Use real manager's state (captured by route closures)
        stopped_info = DeploymentInfo(
            model_name="stopped-deployment",
            backend="vllm",
            port=9002,
            status="stopped",
            container_id="stopped-123",
        )
        app.state.manager.state.add(stopped_info)

        client = TestClient(app)
        resp = client.post("/v1/backends/stopped-deployment/models", json={})
        assert resp.status_code == 400
