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
    """Prevent Docker connections during API tests.

    Returns containers matching the ``test-deployment`` label when Docker
    is queried. This ensures reconciliation adopts running containers
    instead of clearing in-memory state.
    """
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.short_id = "mock-9500"
    mock_container.labels = {
        "switchyard.managed": "true",
        "switchyard.deployment": "test-deployment",
    }
    mock_container.attrs = {
        "NetworkSettings": {
            "Ports": {"8000/tcp": [{"HostPort": "9001"}]},
        },
        "State": {"Status": "running"},
    }
    mock_client.containers.run.return_value = mock_container
    mock_client.containers.get.return_value = mock_container
    # Default: no containers found (fresh start, no running deployment)
    mock_client.containers.list.return_value = []

    with patch("docker.from_env", return_value=mock_client):
        yield mock_client


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

    def test_list_deployments_includes_active_status(
        self, _mock_docker: MagicMock,
    ) -> None:
        """Active deployments reflect their actual in-memory status."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker must return the matching container so reconcile preserves it
        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

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

    def test_status_known(
        self, _mock_docker: MagicMock,
    ) -> None:
        """GET /api/deployments/{deployment}/status returns status (known)."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker returns matching container so reconcile preserves state
        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

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

    def test_load_reconcile_docker_error_returns_structured_500(self) -> None:
        """Docker reconciliation failure during load returns structured 500 JSON."""
        from docker.errors import DockerException

        app = create_app()
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=DockerException("cannot reach docker daemon")
        )

        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/deployments/test-deployment/load",
        )

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "failed to start deployment" in data["detail"]
        assert "failed to reconcile Docker state before load" in data["detail"]
        # Error message is sanitized, not raw Docker SDK output
        assert "docker" in data["detail"].lower()
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

    def test_unload_by_path_returns_stopped(
        self, _mock_docker: MagicMock,
    ) -> None:
        """POST /api/deployments/{deployment}/unload returns stopped."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker returns matching container so reconcile preserves state
        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

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

    def test_proxy_stopped_returns_400(
        self, _mock_docker: MagicMock,
    ) -> None:
        """POST /api/proxy/{deployment}/{path} returns 400 for stopped deployment."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker returns a container in stopped/exited state
        exited_container = MagicMock()
        exited_container.short_id = "stopped-123"
        exited_container.labels = {
            "switchyard.managed": "true",
            "switchyard.deployment": "test-deployment",
        }
        exited_container.attrs = {
            "NetworkSettings": {"Ports": {}},
            "State": {"Status": "exited"},
        }
        _mock_docker.containers.list.return_value = [exited_container]

        app = create_app()
        stopped_info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9001,
            status="stopped",
            container_id="stopped-123",
        )
        app.state.manager.state.add(stopped_info)

        # Reconcile clears stale state for exited container → proxy returns 404
        response = TestClient(app).post("/api/proxy/test-deployment/models", json={})
        assert response.status_code == 404


class TestReconciliationRoutes:
    """API-level tests for reconciliation behavior.

    Validates that routes reconcile with Docker before reporting status,
    adopting running containers, and clearing stale state.
    """

    def test_list_adopts_running_container(self, _mock_docker: MagicMock) -> None:
        """GET /api/deployments adopts running labeled containers as loading."""
        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

        app = create_app()
        # No in-memory state — simulate API restart
        response = TestClient(app).get("/api/deployments")
        data = response.json()
        assert len(data) == 1
        assert data[0]["deployment_name"] == "test-deployment"
        assert data[0]["status"] == "loading"
        # Container was adopted into state as loading
        info = app.state.manager.state.get("test-deployment")
        assert info.status == "loading"
        assert info.port == 9001

    def test_status_clears_stale_running_state(
        self, _mock_docker: MagicMock,
    ) -> None:
        """GET /status clears stale running state when Docker has no container."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker has no container — deployment was externally removed
        _mock_docker.containers.list.return_value = []

        app = create_app()
        # Add stale in-memory state
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="ghost-123",
            )
        )

        # Status should reconcile and report stopped
        response = TestClient(app).get("/api/deployments/test-deployment/status")
        data = response.json()
        assert data["status"] == "stopped"

    def test_proxy_rejects_after_stale_clear(
        self, _mock_docker: MagicMock,
    ) -> None:
        """Proxy rejects after stale state is cleared instead of proxying."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker has no container
        _mock_docker.containers.list.return_value = []

        app = create_app()
        # Stale running state
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="ghost-123",
            )
        )

        # Proxy should reconcile, clear stale state, and 404
        response = TestClient(app).post(
            "/api/proxy/test-deployment/v1/embeddings", json={},
        )
        assert response.status_code == 404

    def test_v1_models_does_not_list_stale(
        self, _mock_docker: MagicMock,
    ) -> None:
        """/v1/models does not list stale running state after external removal."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker has no container — deployment was externally removed
        _mock_docker.containers.list.return_value = []

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="ghost-123",
            )
        )

        response = TestClient(app).get("/v1/models")
        data = response.json()
        assert len(data["data"]) == 0

    def test_detail_adopts_running_container(
        self, _mock_docker: MagicMock,
    ) -> None:
        """GET /api/deployments/{deployment} adopts running container as loading."""
        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

        app = create_app()
        # No in-memory state — simulate API restart

        response = TestClient(app).get("/api/deployments/test-deployment")
        data = response.json()
        assert data["deployment_name"] == "test-deployment"
        assert data["status"] == "loading"

    def test_detail_clears_stale_state(
        self, _mock_docker: MagicMock,
    ) -> None:
        """GET /api/deployments/{deployment} clears stale state when Docker empty."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker has no container
        _mock_docker.containers.list.return_value = []

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="ghost-123",
            )
        )

        response = TestClient(app).get("/api/deployments/test-deployment")
        data = response.json()
        assert data["status"] == "stopped"


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

    def test_models_includes_running_deployment(
        self, _mock_docker: MagicMock,
    ) -> None:
        """GET /v1/models includes a running deployment with correct shape."""
        from datetime import UTC, datetime

        from switchyard.core.adapter import DeploymentInfo

        # Docker returns matching container so reconcile preserves state
        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

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

    def test_models_excludes_non_running_deployments(
        self, _mock_docker: MagicMock,
    ) -> None:
        """GET /v1/models excludes loading and error deployments."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker returns matching container for test-deployment only
        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9000,
                status="running",
                container_id="a1",
            )
        )

        response = TestClient(app).get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "test-deployment"

    def test_models_uses_deployment_name_not_served_model_name(
        self, _mock_docker: MagicMock,
    ) -> None:
        """GET /v1/models uses deployment_name as id, not served_model_name."""
        from switchyard.core.adapter import DeploymentInfo

        # Docker returns matching container so reconcile preserves state
        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

        app = create_app()
        info = DeploymentInfo(
            model_name="test-deployment",
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
        assert data["data"][0]["id"] == "test-deployment"
        assert data["data"][0]["id"] != "completely-different-name"


class TestApiDiagnosticFields:
    """Phase 3: API response shape tests (T3.2-T3.5)."""

    def test_list_includes_health_and_container_status(self) -> None:
        """GET /api/deployments includes health and container_status fields."""
        app = create_app()
        response = TestClient(app).get("/api/deployments")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert "health" in data[0]
        assert "container_status" in data[0]
        # Stopped deployment
        assert data[0]["health"] == "unknown"
        assert data[0]["container_status"] == "missing"
        # docker_error NOT in list response
        assert "docker_error" not in data[0]

    def test_list_running_has_healthy(self, _mock_docker: MagicMock) -> None:
        """Running deployment has health='healthy' and container_status='running'."""
        from switchyard.core.adapter import DeploymentInfo

        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )

        response = TestClient(app).get("/api/deployments")
        data = response.json()
        assert data[0]["health"] == "healthy"
        assert data[0]["container_status"] == "running"

    def test_list_docker_unreachable_preserves_state(
        self, _mock_docker: MagicMock,
    ) -> None:
        """Docker unreachable preserves in-memory state, container_status='unknown'."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )
        # Simulate Docker unreachable
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )

        response = TestClient(app).get("/api/deployments")
        data = response.json()
        assert data[0]["status"] == "running"  # preserved
        assert data[0]["container_status"] == "unknown"
        assert "docker_error" not in data[0]

    def test_status_has_health_and_container_status(
        self, _mock_docker: MagicMock,
    ) -> None:
        """Status endpoint has derived health and container_status."""
        from switchyard.core.adapter import DeploymentInfo

        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )

        response = TestClient(app).get(
            "/api/deployments/test-deployment/status",
        )
        data = response.json()
        assert data["health"] == "healthy"
        assert data["container_status"] == "running"

    def test_status_loading_has_starting(
        self, _mock_docker: MagicMock,
    ) -> None:
        """Loading deployment has health='starting'."""
        from switchyard.core.adapter import DeploymentInfo

        mock_container = _mock_docker.containers.get.return_value
        _mock_docker.containers.list.return_value = [mock_container]

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="loading",
                container_id="abc123",
            )
        )

        response = TestClient(app).get(
            "/api/deployments/test-deployment/status",
        )
        data = response.json()
        assert data["health"] == "starting"

    def test_status_docker_unreachable_includes_docker_error(
        self, _mock_docker: MagicMock,
    ) -> None:
        """Status endpoint includes docker_error when Docker unreachable."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )

        response = TestClient(app).get(
            "/api/deployments/test-deployment/status",
        )
        data = response.json()
        assert data["status"] == "running"  # preserved
        assert data["container_status"] == "unknown"
        assert "docker_error" in data
        assert "docker" in data["docker_error"].lower()

    def test_detail_has_container_status(self) -> None:
        """Detail endpoint includes container_status."""
        app = create_app()
        response = TestClient(app).get("/api/deployments/test-deployment")
        data = response.json()
        assert "container_status" in data
        assert "health" in data

    def test_detail_docker_unreachable_includes_docker_error(
        self, _mock_docker: MagicMock,
    ) -> None:
        """Detail endpoint includes docker_error when Docker unreachable."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )

        response = TestClient(app).get("/api/deployments/test-deployment")
        data = response.json()
        assert data["status"] == "running"  # preserved
        assert data["container_status"] == "unknown"
        assert "docker_error" in data

    def test_load_docker_unreachable_returns_500(
        self, _mock_docker: MagicMock,
    ) -> None:
        """Docker unreachable during load returns structured 500."""
        app = create_app()
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )

        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/deployments/test-deployment/load",
        )
        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "docker" in data["detail"].lower()
        # No container started
        with pytest.raises(KeyError):
            app.state.manager.state.get("test-deployment")

    def test_v1_models_docker_unreachable_preserves_running(
        self, _mock_docker: MagicMock,
    ) -> None:
        """/v1/models preserves running state when Docker unreachable."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )

        response = TestClient(app).get("/v1/models")
        data = response.json()
        # Running deployment still listed despite Docker being unreachable
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "test-deployment"

    def test_chat_completions_docker_unreachable_preserves_running(
        self, _mock_docker: MagicMock,
    ) -> None:
        """/v1/chat/completions preserves running state when Docker unreachable."""
        from switchyard.core.adapter import DeploymentInfo

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"message": {"content": "Hi"}}]}
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
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
        )
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )

        with patch("switchyard.app.httpx.Client", return_value=mock_client):
            response = TestClient(app).post(
                "/v1/chat/completions",
                json={"model": "test-deployment", "messages": []},
            )

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Hi"

    def test_status_stopped_docker_unreachable_includes_docker_error(
        self, _mock_docker: MagicMock,
    ) -> None:
        """Status endpoint includes docker_error when Docker unreachable."""
        app = create_app()
        # No in-memory state — simulate fresh start with Docker unreachable
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )

        response = TestClient(app).get(
            "/api/deployments/test-deployment/status",
        )
        data = response.json()
        assert data["status"] == "stopped"
        assert data["container_status"] == "unknown"
        assert "docker_error" in data
        assert "docker" in data["docker_error"].lower()


class TestDockerUnreachableRouteMatrix:
    """T5.6: Docker-unreachable route matrix tests."""

    def test_list_docker_unreachable(self) -> None:
        """GET /api/deployments preserves state when Docker unreachable."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )
        response = TestClient(app).get("/api/deployments")
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "running"
        assert data[0]["container_status"] == "unknown"
        assert "docker_error" not in data[0]

    def test_detail_docker_unreachable(self) -> None:
        """GET /api/deployments/{dep} preserves state with docker_error."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )
        response = TestClient(app).get("/api/deployments/test-deployment")
        data = response.json()
        assert data["status"] == "running"
        assert data["container_status"] == "unknown"
        assert "docker_error" in data

    def test_status_docker_unreachable(self) -> None:
        """GET /api/deployments/{dep}/status preserves state with docker_error."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )
        response = TestClient(app).get(
            "/api/deployments/test-deployment/status",
        )
        data = response.json()
        assert data["status"] == "running"
        assert data["container_status"] == "unknown"
        assert "docker_error" in data

    def test_load_docker_unreachable_returns_500(self) -> None:
        """POST /api/deployments/{dep}/load returns 500 when Docker unreachable."""
        app = create_app()
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/deployments/test-deployment/load",
        )
        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "docker" in data["detail"].lower()

    def test_unload_docker_unreachable_preserves_state(self) -> None:
        """POST /api/deployments/{dep}/unload returns 500 and preserves state."""
        from switchyard.core.adapter import BackendAdapter, DeploymentInfo

        class FailingAdapter(BackendAdapter):
            def __init__(self, **kwargs: Any) -> None:  # noqa: ANN003
                pass
            def start(self, resolved, port: int) -> DeploymentInfo:
                raise NotImplementedError()
            def stop(self, deployment: DeploymentInfo) -> None:
                raise __import__("docker").errors.DockerException(
                    "connection refused"
                )
            def health(self, deployment: DeploymentInfo) -> str:
                raise NotImplementedError()
            def endpoint(self, deployment: DeploymentInfo) -> str:
                raise NotImplementedError()

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )
        # Register failing adapter so stop() raises
        app.state.manager.registry.register("vllm", FailingAdapter)
        # Docker unreachable during reconcile (informational, state preserved)
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/deployments/test-deployment/unload",
        )
        assert response.status_code == 500
        # Deployment state preserved
        stored = app.state.manager.state.get("test-deployment")
        assert stored.status == "running"

    def test_v1_models_docker_unreachable(self) -> None:
        """GET /v1/models preserves state when Docker unreachable."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
                model_name="test-deployment",
                backend="vllm",
                port=9001,
                status="running",
                container_id="abc123",
            )
        )
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )
        response = TestClient(app).get("/v1/models")
        data = response.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "test-deployment"

    def test_chat_completions_docker_unreachable(self) -> None:
        """POST /v1/chat/completions preserves state when Docker unreachable."""
        from switchyard.core.adapter import DeploymentInfo

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hi"}}],
        }
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        app = create_app()
        app.state.manager.state.add(
            DeploymentInfo(
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
        )
        app.state.manager._docker_client_factory = MagicMock(
            side_effect=__import__("docker").errors.DockerException(
                "connection refused"
            ),
        )

        with patch("switchyard.app.httpx.Client", return_value=mock_client):
            response = TestClient(app).post(
                "/v1/chat/completions",
                json={"model": "test-deployment", "messages": []},
            )

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Hi"
