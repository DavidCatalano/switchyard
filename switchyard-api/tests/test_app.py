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


@pytest.mark.no_isolate
class TestRedactCommandList:
    """Tests for list-aware sensitive field redactor (SEP-004, Task C2)."""

    def test_redacts_hf_token_flag(self) -> None:
        """--hf-token value is redacted in command list."""
        from switchyard.app import _redact_command_list

        args = ["--host", "0.0.0.0", "--hf-token", "secret"]
        result = _redact_command_list(args)
        assert result == ["--host", "0.0.0.0", "--hf-token", "***redacted***"]

    def test_redacts_api_key_flag(self) -> None:
        """--api-key value is redacted in command list."""
        from switchyard.app import _redact_command_list

        args = ["--api-key", "my-api-key", "--model", "/models/test"]
        result = _redact_command_list(args)
        assert result == ["--api-key", "***redacted***", "--model", "/models/test"]

    def test_non_sensitive_flags_unchanged(self) -> None:
        """Non-sensitive flags pass through unchanged."""
        from switchyard.app import _redact_command_list

        args = ["--host", "0.0.0.0", "--port", "8000", "--dtype", "auto"]
        result = _redact_command_list(args)
        assert result == args

    def test_no_redaction_when_no_sensitive_flags(self) -> None:
        """Command list with no sensitive flags is returned as-is."""
        from switchyard.app import _redact_command_list

        args = ["--model", "/models/test", "--tensor-parallel-size", "2"]
        result = _redact_command_list(args)
        assert result == args

    def test_case_insensitive_redaction(self) -> None:
        """Flag names are matched case-insensitively (e.g. --HF-TOKEN)."""
        from switchyard.app import _redact_command_list

        # extra_args key HF_TOKEN renders as --HF-TOKEN via kebab normalization
        args = ["--HF-TOKEN", "secret", "--model", "/models/test"]
        result = _redact_command_list(args)
        assert result == ["--HF-TOKEN", "***redacted***", "--model", "/models/test"]

    def test_does_not_redact_next_flag(self) -> None:
        """Sensitive-looking flag only redacts next token if it is not another flag."""
        from switchyard.app import _redact_command_list

        # --requires-token is sensitive-looking but next token is another flag
        args = ["--requires-token", "--model", "m"]
        result = _redact_command_list(args)
        assert result == ["--requires-token", "--model", "m"]

    def test_redacts_value_after_flag(self) -> None:
        """Sensitive flag followed by a plain value is redacted correctly."""
        from switchyard.app import _redact_command_list

        args = ["--api-key", "my-key", "--model", "/models/test"]
        result = _redact_command_list(args)
        assert result == ["--api-key", "***redacted***", "--model", "/models/test"]


@pytest.mark.no_isolate
class TestPreviewEndpoint:
    """Tests for GET /api/deployments/{deployment}/preview (SEP-004, Task C1-C3)."""

    def _make_config(self, path: Path, extra: dict | None = None) -> None:
        """Write a minimal valid entity-based config file."""
        import yaml

        base = {
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
        }
        if extra:
            base.update(extra)
        path.write_text(yaml.dump(base))

    @pytest.mark.asyncio
    async def test_preview_returns_200_for_valid_deployment(self, tmp_path) -> None:
        """Preview endpoint returns 200 for a valid deployment."""
        import os

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        self._make_config(config_file)
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/deployments/test-deployment/preview"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["image"] == "vllm/vllm-openai:latest"
        assert isinstance(body["command"], list)
        assert "--host" in body["command"]
        assert "0.0.0.0" in body["command"]
        assert "--model" in body["command"]
        assert body["host_port"] is None
        assert body["deployment_status"] == "stopped"
        assert body["container_status"] == "unknown"

    @pytest.mark.asyncio
    async def test_preview_returns_404_for_unknown_deployment(
        self, tmp_path,
    ) -> None:
        """Preview endpoint returns 404 for unknown deployment."""
        import os

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        self._make_config(config_file)
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/deployments/unknown-deployment/preview"
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_redacts_sensitive_fields(self, tmp_path) -> None:
        """Preview redacts hf_token and api_key in command and environment."""
        import os

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        self._make_config(
            config_file,
            extra={
                "runtimes": {
                    "vllm": {
                        "backend": "vllm",
                        "image": "vllm/vllm-openai:latest",
                        "defaults": {
                            "hf_token": "secret-token",
                            "api_key": "secret-api-key",
                        },
                    },
                },
            },
        )
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/deployments/test-deployment/preview"
            )

        assert response.status_code == 200
        body = response.json()
        # Command list: --hf-token and --api-key values should be redacted
        cmd = body["command"]
        hf_idx = cmd.index("--hf-token") if "--hf-token" in cmd else -1
        if hf_idx >= 0:
            assert cmd[hf_idx + 1] == "***redacted***"
        api_idx = cmd.index("--api-key") if "--api-key" in cmd else -1
        if api_idx >= 0:
            assert cmd[api_idx + 1] == "***redacted***"
        # Environment dict: HF_TOKEN should be redacted
        env = body["environment"]
        if "HF_TOKEN" in env:
            assert env["HF_TOKEN"] == "***redacted***"

    @pytest.mark.asyncio
    async def test_preview_excludes_switchyard_env_vars(self, tmp_path) -> None:
        """Preview does not include SWITCHYARD_* environment variables."""
        import os

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        self._make_config(
            config_file,
            extra={
                "hosts": {
                    "test-host": {
                        "stores": {
                            "models": {
                                "host_path": "/data/models",
                                "container_path": "/models",
                            },
                        },
                        "container_defaults": {
                            "environment": {
                                "HF_HOME": "/hf-cache",
                            },
                        },
                    },
                },
            },
        )
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/deployments/test-deployment/preview"
            )

        assert response.status_code == 200
        body = response.json()
        env = body["environment"]
        assert env.get("HF_HOME") == "/hf-cache"
        for key in env:
            assert not key.startswith("SWITCHYARD_"), (
                f"SWITCHYARD_* key {key!r} leaked into preview"
            )

    @pytest.mark.asyncio
    async def test_preview_includes_port_range(self, tmp_path) -> None:
        """Preview includes port_range from host config."""
        import os

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        self._make_config(
            config_file,
            extra={
                "hosts": {
                    "test-host": {
                        "stores": {
                            "models": {
                                "host_path": "/data/models",
                                "container_path": "/models",
                            },
                        },
                        "port_range": [18000, 18100],
                    },
                },
            },
        )
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/deployments/test-deployment/preview"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["port_range"] == [18000, 18100]
        assert body["host_port"] is None

    @pytest.mark.asyncio
    async def test_preview_includes_container_options(self, tmp_path) -> None:
        """Preview includes normalized container_options (ipc_mode, etc.)."""
        import os

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        self._make_config(
            config_file,
            extra={
                "hosts": {
                    "test-host": {
                        "stores": {
                            "models": {
                                "host_path": "/data/models",
                                "container_path": "/models",
                            },
                        },
                        "container_defaults": {
                            "options": {
                                "ipc": "host",
                            },
                        },
                    },
                },
            },
        )
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/deployments/test-deployment/preview"
            )

        assert response.status_code == 200
        body = response.json()
        opts = body.get("container_options", {})
        assert opts.get("ipc_mode") == "host"
        assert "ipc" not in opts

    @pytest.mark.asyncio
    async def test_preview_labels_include_user_labels(self, tmp_path) -> None:
        """Preview labels include user-defined labels merged with Switchyard labels."""
        import os

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        self._make_config(
            config_file,
            extra={
                "hosts": {
                    "test-host": {
                        "stores": {
                            "models": {
                                "host_path": "/data/models",
                                "container_path": "/models",
                            },
                        },
                        "container_defaults": {
                            "options": {
                                "labels": {
                                    "user.team": "ml",
                                    "user.env": "dev",
                                },
                            },
                        },
                    },
                },
            },
        )
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/deployments/test-deployment/preview"
            )

        assert response.status_code == 200
        body = response.json()
        labels = body["labels"]
        # User labels should be present
        assert labels.get("user.team") == "ml"
        assert labels.get("user.env") == "dev"
        # Switchyard labels should also be present and not overwritten by user
        assert labels.get("switchyard.managed") == "true"
        assert labels.get("switchyard.deployment") == "test-deployment"

    @pytest.mark.asyncio
    async def test_preview_container_options_excludes_labels(self, tmp_path) -> None:
        """Preview container_options excludes labels (top-level field)."""
        import os

        from switchyard.app import create_app

        config_file = tmp_path / "config.yaml"
        self._make_config(
            config_file,
            extra={
                "hosts": {
                    "test-host": {
                        "stores": {
                            "models": {
                                "host_path": "/data/models",
                                "container_path": "/models",
                            },
                        },
                        "container_defaults": {
                            "options": {
                                "ipc": "host",
                                "labels": {
                                    "user.team": "ml",
                                },
                            },
                        },
                    },
                },
            },
        )
        os.environ["SWITCHYARD_CONFIG_PATH"] = str(config_file)
        os.environ["SWITCHYARD_ACTIVE_HOST"] = "test-host"

        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/deployments/test-deployment/preview"
            )

        assert response.status_code == 200
        body = response.json()
        opts = body.get("container_options", {})
        # labels should NOT appear in container_options
        assert "labels" not in opts
        # but user labels should appear in top-level labels
        assert body["labels"].get("user.team") == "ml"
