"""Tests for the API routes.

Validates:
- GET /health returns 200
- POST /deployments/load starts a deployment
- POST /deployments/unload stops a deployment
- GET /deployments lists deployments from state
- GET /deployments/{name}/status returns deployment status
- OpenAI-compatible passthrough routes are preserved
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.fixture
def app():
    """Create the FastAPI app with mocked lifecycle."""
    from switchyard.core.lifecycle import LifecycleManager
    from switchyard.core.state import DeploymentStateManager

    app = create_app()
    app.state.manager = MagicMock(spec=LifecycleManager)
    app.state.manager.state = MagicMock(spec=DeploymentStateManager)
    app.state.manager.state.list_deployments.return_value = []
    app.state.manager.state.get.side_effect = KeyError("not found")
    app.state.manager.load_model = AsyncMock()
    app.state.manager.unload_model = AsyncMock()
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestHealth:
    def test_health_endpoint(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200


class TestDeploymentRoutes:
    """Route tests for /deployments endpoints (T4.10)."""

    def test_list_deployments(self, client: TestClient) -> None:
        response = client.get("/deployments")
        assert response.status_code == 200
        assert response.json() == []

    def test_load_deployment_unknown(self, client: TestClient) -> None:
        """Loading an unknown deployment raises 404."""
        response = client.post(
            "/deployments/load",
            json={"deployment": "nonexistent"},
        )
        assert response.status_code == 404

    def test_load_deployment_success(self) -> None:
        """Loading a known deployment calls resolve_deployment + load_model."""
        app = create_app()

        client = TestClient(app)
        response = client.post(
            "/deployments/load",
            json={"deployment": "test-deployment"},
        )

        assert response.status_code == 202
        # Verify the manager's state was updated
        info = app.state.manager.state.get("test-deployment")
        assert info.status in ("loading", "running")

    def test_unload_deployment_unknown(self, client: TestClient) -> None:
        response = client.post(
            "/deployments/unload",
            json={"deployment": "nonexistent"},
        )
        assert response.status_code == 404

    def test_unload_deployment_success(self) -> None:
        """Unloading a deployment calls unload_model."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        # Pre-populate state so unload finds it
        mock_info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="running",
            container_id="abc123",
        )
        app.state.manager.state.add(mock_info)

        client = TestClient(app)
        response = client.post(
            "/deployments/unload",
            json={"deployment": "test-deployment"},
        )

        assert response.status_code == 200

    def test_status_unknown(self, client: TestClient) -> None:
        response = client.get("/deployments/nonexistent/status")
        assert response.status_code == 404


class TestOpenAIProxy:
    """OpenAI-compatible passthrough route tests (T4.11)."""

    def test_chat_completions_route_exists(self, client: TestClient) -> None:
        """POST /v1/chat/completions exists in route table."""
        route_names = [r.path for r in client.app.routes]
        assert "/v1/chat/completions" in route_names

    def test_chat_completions_no_active_deployment(self, client: TestClient) -> None:
        """Chat completions returns 404 when deployment not found."""
        response = client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent"},
        )
        assert response.status_code == 404

    def test_backends_route_exists(self, client: TestClient) -> None:
        """GET /v1/backends/{deployment}/{path:path} exists in route table."""
        route_names = [r.path for r in client.app.routes]
        assert any("backends" in r for r in route_names)

    def test_backends_passthrough_unknown_deployment(
        self, client: TestClient,
    ) -> None:
        """Backend proxy returns 404 for unknown deployment."""
        response = client.post(
            "/v1/backends/nonexistent/models",
            json={},
        )
        assert response.status_code == 404
