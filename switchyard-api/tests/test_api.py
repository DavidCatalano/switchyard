"""Tests for the API routes.

Validates:
- GET /health returns 200
- POST /api/deployments/{deployment}/load starts a deployment
- POST /api/deployments/{deployment}/unload stops a deployment
- GET /api/deployments lists deployments from config + state
- GET /api/deployments/{deployment}/status returns deployment status
- GET /api/deployments/{deployment} returns deployment detail
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


class TestApiDeploymentRoutes:
    """Route tests for new /api/deployments endpoints (T1.1-T1.6)."""

    def test_list_deployments_returns_all_configured(self) -> None:
        """GET /api/deployments returns all configured deployments with status."""
        app = create_app()
        response = TestClient(app).get("/api/deployments")
        assert response.status_code == 200
        data = response.json()
        # Should return all configured deployments, even with no lifecycle state
        assert len(data) == 1
        assert data[0]["deployment_name"] == "test-deployment"
        assert data[0]["status"] == "stopped"

    def test_list_deployments_includes_active_status(self) -> None:
        """Active deployments reflect their actual in-memory status."""
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

        response = TestClient(app).get("/api/deployments")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["deployment_name"] == "test-deployment"
        assert data[0]["status"] == "running"

    def test_detail_returns_config_and_status(self) -> None:
        """GET /api/deployments/{deployment} returns config detail + status summary."""
        app = create_app()
        response = TestClient(app).get("/api/deployments/test-deployment")
        assert response.status_code == 200
        data = response.json()
        assert data["deployment_name"] == "test-deployment"
        assert data["model"] == "test-model"
        assert data["runtime"] == "vllm"
        assert data["host"] == "test-host"
        assert data["status"] == "stopped"

    def test_detail_unknown_returns_404(self) -> None:
        """GET /api/deployments/{deployment} returns 404 for unknown deployment."""
        app = create_app()
        response = TestClient(app).get("/api/deployments/nonexistent")
        assert response.status_code == 404

    def test_detail_masks_sensitive_runtime_args(self) -> None:
        """Sensitive runtime args like hf_token are masked in detail response."""
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
                    "defaults": {"hf_token": "secret-token-123"},
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
            app = create_app()
        response = TestClient(app).get("/api/deployments/test-deployment")
        assert response.status_code == 200
        data = response.json()
        args = data["runtime_args"]
        # hf_token should be masked, not exposed
        assert args.get("hf_token") == "***redacted***"

    def test_detail_masks_sensitive_args_in_extra_args(self) -> None:
        """Nested extra_args with sensitive keys are also masked."""
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
                    "extra_args": {
                        "hf-token": "nested-secret-token",
                        "api_key": "nested-api-key",
                        "max_batch_size": 256,
                    },
                },
            },
        })
        with patch("switchyard.app.ConfigLoader.load", return_value=config):
            app = create_app()
        response = TestClient(app).get("/api/deployments/test-deployment")
        assert response.status_code == 200
        data = response.json()
        args = data["runtime_args"]
        # Nested extra_args sensitive keys should be masked
        nested = args.get("extra_args", {})
        assert nested.get("hf-token") == "***redacted***"
        assert nested.get("api_key") == "***redacted***"
        # Non-sensitive keys pass through unchanged
        assert nested.get("max_batch_size") == 256

    def test_status_known(self) -> None:
        """GET /api/deployments/{deployment}/status returns status (known)."""
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

        response = TestClient(app).get("/api/deployments/test-deployment/status")
        assert response.status_code == 200
        assert response.json()["status"] == "running"

    def test_status_unknown_returns_404(self) -> None:
        """GET /api/deployments/{deployment}/status returns 404 (unknown)."""
        app = create_app()
        response = TestClient(app).get("/api/deployments/nonexistent/status")
        assert response.status_code == 404

    def test_load_by_path_returns_202(self) -> None:
        """POST /api/deployments/{deployment}/load loads by path ID, returns 202."""
        app = create_app()
        response = TestClient(app).post("/api/deployments/test-deployment/load")
        assert response.status_code == 202
        info = app.state.manager.state.get("test-deployment")
        assert info.status in ("loading", "running")

    def test_load_unknown_returns_404(self) -> None:
        """POST /api/deployments/{deployment}/load returns 404 (unknown)."""
        app = create_app()
        response = TestClient(app).post("/api/deployments/nonexistent/load")
        assert response.status_code == 404

    def test_load_runtime_error_returns_structured_500(self) -> None:
        """RuntimeError from adapter.start() returns structured 500 JSON."""
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
            "/api/deployments/test-deployment/load",
        )

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "docker refused the container" in data["detail"]
        # Deployment should NOT be in state
        with pytest.raises(KeyError):
            app.state.manager.state.get("test-deployment")

    def test_load_non_runtime_error_surfaces(self) -> None:
        """Non-RuntimeError exceptions surface as real server errors."""
        from switchyard.core.adapter import BackendAdapter

        class BugAdapter(BackendAdapter):
            def __init__(self, **kwargs: Any) -> None:  # noqa: ANN001
                pass

            def start(
                self, resolved, port: int,  # noqa: ANN001
            ):
                raise TypeError("internal bug")

            def stop(self, deployment) -> None:  # noqa: ANN001
                pass

            def health(self, deployment) -> str:  # noqa: ANN001
                return "error"

            def endpoint(self, deployment) -> str:  # noqa: ANN001
                return ""

        app = create_app()
        registry = app.state.manager.registry
        registry.register("vllm", BugAdapter)

        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/deployments/test-deployment/load",
        )

        assert response.status_code == 500
        body = response.text
        assert "failed to start deployment" not in body

    def test_unload_by_path_returns_stopped(self) -> None:
        """POST /api/deployments/{deployment}/unload returns stopped."""
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

        response = TestClient(app).post("/api/deployments/test-deployment/unload")
        assert response.status_code == 200
        assert response.json()["status"] == "stopped"

    def test_unload_unknown_returns_404(self) -> None:
        """POST /api/deployments/{deployment}/unload returns 404 (unknown)."""
        app = create_app()
        response = TestClient(app).post("/api/deployments/nonexistent/unload")
        assert response.status_code == 404

    def test_proxy_unknown_returns_404(self) -> None:
        """POST /api/proxy/{deployment}/{path} returns 404 for unknown deployment."""
        app = create_app()
        response = TestClient(app).post("/api/proxy/nonexistent/models", json={})
        assert response.status_code == 404

    def test_proxy_stopped_returns_400(self) -> None:
        """POST /api/proxy/{deployment}/{path} returns 400 for stopped deployment."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        stopped_info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="stopped",
            container_id="stopped-123",
        )
        app.state.manager.state.add(stopped_info)

        response = TestClient(app).post("/api/proxy/test-deployment/models", json={})
        assert response.status_code == 400


class TestLegacyRoutesRemoved:
    """Tests verifying old routes no longer match (T1.1-T1.6)."""

    def test_old_list_returns_404(self) -> None:
        """Old GET /deployments no longer matches."""
        app = create_app()
        response = TestClient(app).get("/deployments")
        assert response.status_code == 404

    def test_old_load_returns_404(self) -> None:
        """Old POST /deployments/load no longer matches."""
        app = create_app()
        response = TestClient(app).post(
            "/deployments/load", json={"deployment": "test"},
        )
        assert response.status_code == 404

    def test_old_unload_returns_404(self) -> None:
        """Old POST /deployments/unload no longer matches."""
        app = create_app()
        response = TestClient(app).post(
            "/deployments/unload", json={"deployment": "test"},
        )
        assert response.status_code == 404

    def test_old_backends_returns_404(self) -> None:
        """Old POST /v1/backends/{deployment}/{path} no longer matches."""
        app = create_app()
        response = TestClient(app).post("/v1/backends/test/models", json={})
        assert response.status_code == 404


class TestOpenAIProxy:
    """OpenAI-compatible passthrough route tests."""

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

    def test_proxy_route_exists(self) -> None:
        """POST /api/proxy/{deployment}/{path:path} exists in route table."""
        app = create_app()
        route_names = [r.path for r in app.routes]
        assert any("proxy" in r for r in route_names)

    def test_proxy_passthrough_unknown_deployment(self) -> None:
        """Proxy returns 404 for unknown deployment."""
        app = create_app()
        response = TestClient(app).post(
            "/api/proxy/nonexistent/models",
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
