"""Tests for OpenAI-compatible proxy passthrough routes.

Validates:
- /v1/chat/completions forwards to active deployment
- /v1/backends/{deployment}/{path:path} forwards to active deployment
- Proxy returns errors when deployment not found or not running
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


class TestChatCompletionsProxy:
    """Tests for POST /v1/chat/completions passthrough."""

    def test_no_active_deployment_returns_404(self) -> None:
        """Returns 404 when deployment not found in state."""
        app = create_app()
        # Real manager's state is empty, so deployment not found -> 404

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent"},
        )
        assert resp.status_code == 404

    def test_deployment_not_running_returns_400(self) -> None:
        """Returns 400 when deployment exists but is not running."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
        stopped_info = DeploymentInfo(
            model_name="stopped-deployment",
            backend="vllm",
            port=9001,
            status="stopped",
            container_id="stopped-123",
        )
        app.state.manager.state.add(stopped_info)

        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "stopped-deployment"},
        )
        assert resp.status_code == 400

    def test_non_streaming_proxy_success(self) -> None:
        """Non-streaming chat proxies to backend and returns response."""
        from switchyard.core.adapter import DeploymentInfo

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello"}}],
        }
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
                "/v1/chat/completions",
                json={
                    "model": "test-deployment",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )

        # Assert forwarded URL and body
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args.args[0] == "http://127.0.0.1:9001/v1/chat/completions"
        assert call_args.kwargs["json"] == {
            "model": "test-deployment",
            "messages": [{"role": "user", "content": "Hi"}],
        }

        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "Hello"

    def test_streaming_proxy_success(self) -> None:
        """Streaming chat proxies to backend and returns SSE response."""
        from switchyard.core.adapter import DeploymentInfo

        sse_data = b"data: {\"choices\": []}\n\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_bytes.return_value = [sse_data]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_client = MagicMock()
        mock_client.stream.return_value = mock_response
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
                json={
                    "model": "test-deployment",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )

        # Assert stream() called with correct URL and body
        mock_client.stream.assert_called_once()
        call_args = mock_client.stream.call_args
        assert call_args.args[0] == "POST"
        assert call_args.args[1] == "http://127.0.0.1:9001/v1/chat/completions"
        assert call_args.kwargs["json"] == {
            "model": "test-deployment",
            "stream": True,
            "messages": [{"role": "user", "content": "Hi"}],
        }

        assert resp.status_code == 200
        assert b"choices" in resp.content

    def test_streaming_upstream_unreachable_returns_503(self) -> None:
        """Streaming chat returns 503 when upstream stream cannot open."""
        from switchyard.core.adapter import DeploymentInfo

        stream_ctx = MagicMock()
        stream_ctx.__enter__.side_effect = httpx.ConnectError("refused")
        mock_client = MagicMock()
        mock_client.stream.return_value = stream_ctx

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
                json={
                    "model": "test-deployment",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )

        assert resp.status_code == 503
        assert resp.json() == {"detail": "backend unavailable"}
        mock_client.close.assert_called_once()

    def test_streaming_upstream_timeout_returns_504(self) -> None:
        """Streaming chat returns 504 when upstream stream setup times out."""
        from switchyard.core.adapter import DeploymentInfo

        mock_client = MagicMock()
        mock_client.stream.side_effect = httpx.TimeoutException("timeout")

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
                json={
                    "model": "test-deployment",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )

        assert resp.status_code == 504
        assert resp.json() == {"detail": "request timeout"}
        mock_client.close.assert_called_once()

    def test_upstream_unreachable_returns_503(self) -> None:
        """Returns 503 when backend is unreachable."""
        from switchyard.core.adapter import DeploymentInfo

        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")
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
        assert resp.status_code == 503


class TestBackendsPassthrough:
    """Tests for /v1/backends/{deployment}/{path:path} proxy."""

    def test_unknown_deployment_returns_404(self) -> None:
        """Returns 404 for deployment not in state."""
        app = create_app()

        client = TestClient(app)
        resp = client.post("/v1/backends/nonexistent/models", json={})
        assert resp.status_code == 404

    def test_deployment_not_running_returns_400(self) -> None:
        """Returns 400 when deployment is not running."""
        from switchyard.core.adapter import DeploymentInfo

        app = create_app()
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

    def test_backend_passthrough_success(self) -> None:
        """Backend passthrough forwards to backend and returns response."""
        from switchyard.core.adapter import DeploymentInfo

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"id": "text-embedding-3"}]}
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
                "/v1/backends/test-deployment/embeddings",
                json={"input": "hello"},
            )

        # Assert forwarded URL and body
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args.args[0] == "http://127.0.0.1:9001/v1/embeddings"
        assert call_args.kwargs["json"] == {"input": "hello"}

        assert resp.status_code == 200
        assert resp.json()["data"][0]["id"] == "text-embedding-3"
