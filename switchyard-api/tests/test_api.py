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

from typing import Any
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


class TestHealth:
    def test_health_endpoint(self) -> None:
        app = create_app()
        response = TestClient(app).get("/health")
        assert response.status_code == 200


class TestDeploymentRoutes:
    """Route tests for /deployments endpoints (T4.10)."""

    def test_list_deployments(self) -> None:
        """Empty state returns empty list."""
        app = create_app()
        response = TestClient(app).get("/deployments")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_deployments_with_entries(self) -> None:
        """State entries appear in deployment list."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="running",
            container_id="abc123",
        )
        app.state.manager.state.add(info)

        response = TestClient(app).get("/deployments")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["deployment_name"] == "test-deployment"

    def test_load_deployment_unknown(self) -> None:
        """Loading an unknown deployment raises 404."""
        app = create_app()
        response = TestClient(app).post(
            "/deployments/load",
            json={"deployment": "nonexistent"},
        )
        assert response.status_code == 404

    def test_load_deployment_success(self) -> None:
        """Loading a known deployment calls resolve_deployment + load_model."""
        app = create_app()

        response = TestClient(app).post(
            "/deployments/load",
            json={"deployment": "test-deployment"},
        )

        assert response.status_code == 202
        # Verify the manager's state was updated
        info = app.state.manager.state.get("test-deployment")
        assert info.status in ("loading", "running")

    def test_load_deployment_start_failure_returns_500(self) -> None:
        """T5.2: adapter.start() RuntimeError returns structured 500 JSON."""
        from switchyard.core.adapter import BackendAdapter

        class FailingAdapter(BackendAdapter):
            def __init__(self, **kwargs: Any) -> None:  # noqa: ANN001
                pass

            def start(
                self, resolved, port: int,  # noqa: ANN001
            ):
                raise RuntimeError("docker refused the container")

            def stop(self, deployment) -> None:  # noqa: ANN001
                pass

            def health(self, deployment) -> str:  # noqa: ANN001
                return "error"

            def endpoint(self, deployment) -> str:  # noqa: ANN001
                return ""

        app = create_app()
        registry = app.state.manager.registry
        registry.register("vllm", FailingAdapter)

        response = TestClient(app, raise_server_exceptions=False).post(
            "/deployments/load",
            json={"deployment": "test-deployment"},
        )

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "docker refused the container" in data["detail"]
        # Deployment should NOT be in state
        with pytest.raises(KeyError):
            app.state.manager.state.get("test-deployment")

    def test_load_deployment_non_runtime_error_surfaces(self) -> None:
        """T5.2 boundary: non-RuntimeError exceptions are not the structured 500.

        Programming mistakes, config errors, and unrelated bugs should surface
        as real server errors, not as the structured 'failed to start deployment'
        response.
        """
        from switchyard.core.adapter import BackendAdapter

        class BugAdapter(BackendAdapter):
            def __init__(self, **kwargs: Any) -> None:  # noqa: ANN001
                pass

            def start(
                self, resolved, port: int,  # noqa: ANN001
            ):
                raise TypeError("internal bug")  # not a RuntimeError

            def stop(self, deployment) -> None:  # noqa: ANN001
                pass

            def health(self, deployment) -> str:  # noqa: ANN001
                return "error"

            def endpoint(self, deployment) -> str:  # noqa: ANN001
                return ""

        app = create_app()
        registry = app.state.manager.registry
        registry.register("vllm", BugAdapter)

        # TypeError is not RuntimeError, so the route does NOT convert it
        # to the structured 500 "failed to start deployment" response.
        response = TestClient(app, raise_server_exceptions=False).post(
            "/deployments/load",
            json={"deployment": "test-deployment"},
        )

        # Still 500 (server error) but the structured startup message must NOT appear
        assert response.status_code == 500
        body = response.text
        assert "failed to start deployment" not in body

    def test_unload_deployment_unknown(self) -> None:
        """Unloading an unknown deployment raises 404."""
        app = create_app()
        response = TestClient(app).post(
            "/deployments/unload",
            json={"deployment": "nonexistent"},
        )
        assert response.status_code == 404

    def test_unload_deployment_success(self) -> None:
        """Unloading a deployment calls unload_model."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        # Pre-populate state so unload finds it
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="running",
            container_id="abc123",
        )
        app.state.manager.state.add(info)

        response = TestClient(app).post(
            "/deployments/unload",
            json={"deployment": "test-deployment"},
        )

        assert response.status_code == 200

    def test_status_unknown(self) -> None:
        """Status of unknown deployment returns 404."""
        app = create_app()
        response = TestClient(app).get("/deployments/nonexistent/status")
        assert response.status_code == 404

    def test_status_known(self) -> None:
        """Status of known deployment returns its status."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="running",
            container_id="abc123",
        )
        app.state.manager.state.add(info)

        response = TestClient(app).get("/deployments/test-deployment/status")
        assert response.status_code == 200
        assert response.json()["status"] == "running"


class TestOpenAIProxy:
    """OpenAI-compatible passthrough route tests (T4.11)."""

    def test_chat_completions_route_exists(self) -> None:
        """POST /v1/chat/completions exists in route table."""
        app = create_app()
        route_names = [r.path for r in app.routes]
        assert "/v1/chat/completions" in route_names

    def test_chat_completions_no_active_deployment(self) -> None:
        """Chat completions returns 404 when deployment not found."""
        app = create_app()
        response = TestClient(app).post(
            "/v1/chat/completions",
            json={"model": "nonexistent"},
        )
        assert response.status_code == 404

    def test_backends_route_exists(self) -> None:
        """GET /v1/backends/{deployment}/{path:path} exists in route table."""
        app = create_app()
        route_names = [r.path for r in app.routes]
        assert any("backends" in r for r in route_names)

    def test_backends_passthrough_unknown_deployment(self) -> None:
        """Backend proxy returns 404 for unknown deployment."""
        app = create_app()
        response = TestClient(app).post(
            "/v1/backends/nonexistent/models",
            json={},
        )
        assert response.status_code == 404


class TestOpenAIModels:
    """Tests for GET /v1/models (T5.5/T5.6)."""

    def test_models_returns_empty_list(self) -> None:
        """GET /v1/models returns empty list when no deployments are running."""
        app = create_app()
        response = TestClient(app).get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert data["data"] == []

    def test_models_includes_running_deployment(self) -> None:
        """GET /v1/models includes a running deployment with correct shape."""
        from datetime import UTC, datetime

        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        started = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="running",
            container_id="abc123",
            started_at=started,
            metadata={"served_model_name": "vllm-TestModel"},
        )
        app.state.manager.state.add(info)

        response = TestClient(app).get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 1
        model = data["data"][0]
        assert model["id"] == "test-deployment"
        assert model["object"] == "model"
        assert model["created"] == int(started.timestamp())
        assert model["owned_by"] == "switchyard"

    def test_models_excludes_non_running_deployments(self) -> None:
        """GET /v1/models excludes loading and error deployments."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="running-dep",
                backend="vllm",
                port=9000,
                status="running",
                container_id="a1",
            )
        )
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="loading-dep",
                backend="vllm",
                port=9001,
                status="loading",
                container_id="b2",
            )
        )
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="error-dep",
                backend="vllm",
                port=9002,
                status="error",
                container_id="c3",
            )
        )

        response = TestClient(app).get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "running-dep"

    def test_models_uses_deployment_name_not_served_model_name(self) -> None:
        """GET /v1/models uses deployment_name as id, not served_model_name."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        info = DeploymentInfo(
            model_name="my-deployment-id",
            backend="vllm",
            port=9001,
            status="running",
            container_id="abc123",
            metadata={"served_model_name": "completely-different-name"},
        )
        app.state.manager.state.add(info)

        response = TestClient(app).get("/v1/models")
        data = response.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "my-deployment-id"
        assert data["data"][0]["id"] != "completely-different-name"
