"""Proxy endpoint tests — T4.5 (chat completions), T4.6 (streaming), T4.7 (passthrough).

Tests POST /v1/chat/completions, streaming proxy,
and POST /v1/backends/{model}/{path...} against the FastAPI app.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from switchyard.app import create_app
from switchyard.config.models import Config, GlobalConfig, RuntimeDefaults


def _mock_deployment(status: str = "running", port: int = 8000) -> SimpleNamespace:
    """Create a mock deployment info."""
    return SimpleNamespace(
        model_name="qwen-32b",
        backend="vllm",
        port=port,
        status=status,
        started_at=None,
    )


@pytest.fixture
def app():
    """Create a fresh FastAPI app with mocked lifecycle manager."""
    manager = MagicMock()
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
# T4.5 — POST /v1/chat/completions (non-streaming)
# ---------------------------------------------------------------------------

class TestChatCompletions:
    """POST /v1/chat/completions endpoint tests."""

    def test_chat_unknown_model_404(self, client):
        """Chat with unknown model returns 404."""
        tc, manager = client
        manager.state.get.side_effect = KeyError("not found")

        resp = tc.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": []},
        )
        assert resp.status_code == 404

    def test_chat_model_not_running_400(self, client):
        """Chat with model not running returns 400."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="loading")

        resp = tc.post(
            "/v1/chat/completions",
            json={"model": "qwen-32b", "messages": []},
        )
        assert resp.status_code == 400

    def test_chat_proxy_success(self, client):
        """Chat with running model proxies to backend successfully."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="running", port=8000)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{"message": {"content": "Hello!"}}],
        }

        with patch("switchyard.app.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client_cls.return_value.__exit__.return_value = False

            resp = tc.post(
                "/v1/chat/completions",
                json={
                    "model": "qwen-32b",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["choices"][0]["message"]["content"] == "Hello!"


# ---------------------------------------------------------------------------
# T4.6 — Streaming proxy
# ---------------------------------------------------------------------------

class TestStreamingProxy:
    """POST /v1/chat/completions streaming tests."""

    def test_stream_proxy(self, client):
        """Streaming request proxies SSE chunks transparently."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="running", port=8000)

        # Simulate SSE chunks
        chunks = [
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/event-stream"}
        mock_response.iter_bytes.return_value = iter(chunks)

        with patch("switchyard.app.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client_cls.return_value.__exit__.return_value = False

            resp = tc.post(
                "/v1/chat/completions",
                json={
                    "model": "qwen-32b",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
            )
            assert resp.status_code == 200
            body = resp.content.decode()
            assert "Hello" in body


# ---------------------------------------------------------------------------
# T4.7 — Backend passthrough
# ---------------------------------------------------------------------------

class TestPassthrough:
    """POST /v1/backends/{model}/{path...} tests."""

    def test_passthrough_unknown_model_404(self, client):
        """Passthrough with unknown model returns 404."""
        tc, manager = client
        manager.state.get.side_effect = KeyError("not found")

        resp = tc.post("/v1/backends/nonexistent/v1/embeddings", json={})
        assert resp.status_code == 404

    def test_passthrough_model_not_running_400(self, client):
        """Passthrough with model not running returns 400."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="loading")

        resp = tc.post("/v1/backends/qwen-32b/v1/embeddings", json={})
        assert resp.status_code == 400

    def test_passthrough_success(self, client):
        """Passthrough proxies to backend endpoint successfully."""
        tc, manager = client
        manager.state.get.return_value = _mock_deployment(status="running", port=8000)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2]}]}

        with patch("switchyard.app.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client_cls.return_value.__exit__.return_value = False

            resp = tc.post(
                "/v1/backends/qwen-32b/v1/embeddings",
                json={"input": "test"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["data"][0]["embedding"] == [0.1, 0.2]
