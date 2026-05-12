"""Tests for the minimal FastAPI application."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_config(path: Path) -> None:
    """Write a minimal valid entity-based config file."""
    import yaml

    path.write_text(
        yaml.dump({
            "hosts": {
                "test-host": {
                    "stores": {
                        "models": {
                            "host_path": "/data/models",
                            "container_path": "/models",
                        },
                    },
                },
            },
            "runtimes": {
                "vllm": {
                    "backend": "vllm",
                    "image": "vllm/vllm-openai:latest",
                },
            },
            "models": {
                "test-model": {
                    "source": {
                        "store": "models",
                        "path": "test-model",
                    },
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
    )


@pytest.fixture
def app(tmp_path) -> FastAPI:
    """Create app with a minimal config file."""
    import os

    from switchyard.app import create_app

    config_file = tmp_path / "config.yaml"
    _make_config(config_file)
    os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
    os.environ.setdefault("SWITCHYARD_ACTIVE_HOST", "test-host")

    return create_app()


@pytest.mark.no_isolate
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


@pytest.mark.no_isolate
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
        os.environ.setdefault("SWITCHYARD_ACTIVE_HOST", "test-host")

        app = create_app()
        # Verify middleware was added (app has user_middleware)
        assert len(app.user_middleware) > 0


@pytest.mark.no_isolate
class TestResolverErrorHandler:
    """Tests for pydantic.ValidationError handling in API routes (SEP-004)."""

    @pytest.mark.asyncio
    async def test_typo_in_runtime_overrides_returns_400(self, tmp_path) -> None:
        """Typo in deployment.runtime_overrides returns 400, not 500."""
        import os

        import yaml

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump({
                "hosts": {
                    "test-host": {
                        "stores": {
                            "models": {
                                "host_path": "/data/models",
                                "container_path": "/models",
                            },
                        },
                    },
                },
                "runtimes": {
                    "vllm": {
                        "backend": "vllm",
                        "image": "vllm/vllm-openai:latest",
                    },
                },
                "models": {
                    "test-model": {
                        "source": {
                            "store": "models",
                            "path": "test-model",
                        },
                    },
                },
                "deployments": {
                    "test-deployment": {
                        "model": "test-model",
                        "runtime": "vllm",
                        "host": "test-host",
                        "runtime_overrides": {
                            "max_seqs": 10,  # typo: should be max_num_seqs
                        },
                    },
                },
            })
        )
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/deployments/test-deployment/load")

        assert response.status_code == 400
        body = response.json()
        assert "detail" in body
        # Error should mention the unknown field
        assert "max_seqs" in body["detail"] or "extra" in body["detail"].lower()

    @pytest.mark.asyncio
    async def test_typo_in_runtime_defaults_returns_400(self, tmp_path) -> None:
        """Typo in runtime.defaults returns 400, not 500."""
        import os

        import yaml

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump({
                "hosts": {
                    "test-host": {
                        "stores": {
                            "models": {
                                "host_path": "/data/models",
                                "container_path": "/models",
                            },
                        },
                    },
                },
                "runtimes": {
                    "vllm": {
                        "backend": "vllm",
                        "image": "vllm/vllm-openai:latest",
                        "defaults": {
                            "max_seqs": 10,  # typo
                        },
                    },
                },
                "models": {
                    "test-model": {
                        "source": {
                            "store": "models",
                            "path": "test-model",
                        },
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
        )
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/deployments/test-deployment/load")

        assert response.status_code == 400
        body = response.json()
        assert "detail" in body
