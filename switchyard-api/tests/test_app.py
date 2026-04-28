"""Tests for the minimal FastAPI application (T1.8)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_config(path: Path) -> None:
    """Write a minimal valid config file."""
    import yaml

    path.write_text(
        yaml.dump({
            "global": {
                "version": "0.1.0",
                "env": "development",
                "log_level": "debug",
                "host": "127.0.0.1",
                "base_port": 8000,
            },
            "models": {
                "test-model": {
                    "backend": "vllm",
                    "image": "vllm/vllm-openai:latest",
                    "runtime": {"repo": "test/repo"},
                },
            },
        })
    )


@pytest.fixture
def app(tmp_path) -> FastAPI:
    """Create app with a minimal config file."""
    import os

    from switchyard.app import create_app

    config_file = tmp_path / "config.yaml"
    _make_config(config_file)
    os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)

    return create_app()


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, app: FastAPI) -> None:
        """/health returns 200 OK."""
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_returns_ok_status(self, app: FastAPI) -> None:
        """/health returns {status: ok} body."""
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_health_includes_request_id(self, app: FastAPI) -> None:
        """/health response includes X-Request-ID header."""
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert "x-request-id" in response.headers


class TestAppCreation:
    """Tests for application creation and configuration."""

    def test_create_app_returns_fastapi_instance(self, app: FastAPI) -> None:
        """create_app returns a FastAPI instance."""
        assert isinstance(app, FastAPI)

    def test_app_has_correct_title(self, app: FastAPI) -> None:
        """App title is set to 'Switchyard API'."""
        assert app.title == "Switchyard API"

    def test_create_app_with_overrides(self, tmp_path) -> None:
        """create_app accepts config overrides for testing."""
        import os

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        _make_config(config_file)
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)

        app = create_app(
            config_overrides={"global_config": {"log_level": "warning"}},
        )
        # Verify middleware was added (app has user_middleware)
        assert len(app.user_middleware) > 0
