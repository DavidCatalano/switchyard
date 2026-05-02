"""API endpoint tests — model lifecycle routes (T4.1–T4.4).

Tests POST /models/load, POST /models/unload, GET /models,
GET /models/{model}/status against the FastAPI app using TestClient.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from switchyard.app import create_app
from switchyard.config.models import (
    ControlConfig,
    GlobalConfig,
    RuntimeDefaults,
    VLLMRuntimeConfig,
)
from switchyard.config.models import (
    LegacyConfig as Config,
)
from switchyard.config.models import (
    LegacyModelConfig as ModelConfig,
)


@pytest.fixture
def app():
    """Create a fresh FastAPI app with mocked lifecycle manager."""
    manager = MagicMock()
    manager.load_model = AsyncMock()
    manager.unload_model = AsyncMock()
    manager.state.get = MagicMock()
    manager.state.list_deployments = MagicMock(return_value=[])

    config = Config(
        global_config=GlobalConfig(log_level="debug"),
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
# T4.1 — POST /models/load
# ---------------------------------------------------------------------------

class TestLoadModel:
    """POST /models/load endpoint tests."""

    def _setup_model(self, config, model_name, backend="vllm") -> None:
        """Add a model entry to config.models."""
        config.models[model_name] = ModelConfig(
            backend=backend,
            image="vllm/vllm-openai:latest",
            control=ControlConfig(),
            runtime=VLLMRuntimeConfig(repo=f"hf/{model_name}"),
        )

    def test_load_returns_202(self, client):
        """Load a model returns 202 Accepted."""
        tc, manager = client
        self._setup_model(tc.app.state.config, "qwen-32b")
        manager.load_model.return_value = SimpleNamespace(
            model_name="qwen-32b",
            backend="vllm",
            port=8000,
            status="loading",
            container_id="abc123",
        )

        resp = tc.post("/models/load", json={"model": "qwen-32b"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["model_name"] == "qwen-32b"
        assert data["status"] == "loading"

    def test_load_unknown_model_404(self, client):
        """Load unknown model returns 404."""
        tc, manager = client

        resp = tc.post("/models/load", json={"model": "nonexistent"})
        assert resp.status_code == 404

    def test_load_duplicate_running_400(self, client):
        """Load already-running model returns 400."""
        tc, manager = client
        self._setup_model(tc.app.state.config, "qwen-32b")
        manager.load_model.side_effect = ValueError("already deployed")

        resp = tc.post("/models/load", json={"model": "qwen-32b"})
        assert resp.status_code == 400

    def test_load_no_body_400(self, client):
        """Load with no body returns 400."""
        tc, _ = client

        resp = tc.post("/models/load", content=b"")
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# T4.2 — POST /models/unload
# ---------------------------------------------------------------------------

class TestUnloadModel:
    """POST /models/unload endpoint tests."""

    def test_unload_returns_200(self, client):
        """Unload a model returns 200 OK."""
        tc, manager = client

        resp = tc.post("/models/unload", json={"model": "qwen-32b"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_name"] == "qwen-32b"
        assert data["status"] == "stopped"

    def test_unload_unknown_model_404(self, client):
        """Unload unknown model returns 404."""
        tc, manager = client
        manager.unload_model.side_effect = KeyError("not found")

        resp = tc.post("/models/unload", json={"model": "nonexistent"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# T4.3 — GET /models
# ---------------------------------------------------------------------------

class TestListModels:
    """GET /models endpoint tests."""

    def test_list_empty(self, client):
        """List models returns empty list when none deployed."""
        tc, manager = client

        resp = tc.get("/models")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_deployments(self, client):
        """List models returns all deployments with metadata."""
        tc, manager = client
        manager.state.list_deployments.return_value = ["qwen-32b"]
        manager.state.get.return_value = SimpleNamespace(
            model_name="qwen-32b",
            backend="vllm",
            port=8000,
            status="running",
            started_at=None,
        )

        resp = tc.get("/models")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["model_name"] == "qwen-32b"
        assert data[0]["status"] == "running"


# ---------------------------------------------------------------------------
# T4.4 — GET /models/{model}/status
# ---------------------------------------------------------------------------

class TestModelStatus:
    """GET /models/{model}/status endpoint tests."""

    def test_status_running(self, client):
        """Returns running status."""
        tc, manager = client
        manager.state.get.return_value = SimpleNamespace(
            model_name="qwen-32b", status="running",
        )

        resp = tc.get("/models/qwen-32b/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_status_loading(self, client):
        """Returns loading status."""
        tc, manager = client
        manager.state.get.return_value = SimpleNamespace(
            model_name="qwen-32b", status="loading",
        )

        resp = tc.get("/models/qwen-32b/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "loading"

    def test_status_error(self, client):
        """Returns error status."""
        tc, manager = client
        manager.state.get.return_value = SimpleNamespace(
            model_name="qwen-32b", status="error",
        )

        resp = tc.get("/models/qwen-32b/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_status_unknown_model_404(self, client):
        """Unknown model returns 404."""
        tc, manager = client
        manager.state.get.side_effect = KeyError("not found")

        resp = tc.get("/models/nonexistent/status")
        assert resp.status_code == 404
