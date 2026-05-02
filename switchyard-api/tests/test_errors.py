"""Tests for error handling on proxy and deployment routes.

Validates:
- Proxy returns appropriate errors when no active deployment
- Unknown deployment names return 404
- Various error conditions on passthrough
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


class TestProxyErrors:
    """Error handling on OpenAI passthrough routes."""

    def test_chat_completions_no_deployment(self) -> None:
        """Returns 404 when deployment not found."""
        from switchyard.core.lifecycle import LifecycleManager
        from switchyard.core.state import DeploymentStateManager

        app = create_app()
        app.state.manager = MagicMock(spec=LifecycleManager)
        mock_state = MagicMock(spec=DeploymentStateManager)
        mock_state.get.side_effect = KeyError("not found")
        mock_state.list_deployments.return_value = []
        app.state.manager.state = mock_state

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent"},
        )
        assert resp.status_code == 404

    def test_backends_passthrough_unknown_deployment(self) -> None:
        """Returns 404 for deployment name not in state."""
        from switchyard.core.lifecycle import LifecycleManager
        from switchyard.core.state import DeploymentStateManager

        app = create_app()
        app.state.manager = MagicMock(spec=LifecycleManager)
        mock_state = MagicMock(spec=DeploymentStateManager)
        mock_state.get.side_effect = KeyError("not found")
        mock_state.list_deployments.return_value = []
        app.state.manager.state = mock_state

        client = TestClient(app)
        resp = client.post("/v1/backends/nonexistent/models", json={})
        assert resp.status_code == 404

    def test_chat_completions_upstream_unreachable(self) -> None:
        """Proxy returns 503 when backend is unreachable (connect error)."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        # Use the real manager's state (captured by route closures)
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="running",
            container_id="abc123",
        )
        app.state.manager.state.add(info)

        client = TestClient(app)
        # Proxy will try to connect to localhost:9001 which fails
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "test-deployment",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )
        # Connection refused to local backend returns 503
        assert resp.status_code == 503
