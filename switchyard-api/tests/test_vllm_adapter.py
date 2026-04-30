"""Tests for the VLLMAdapter (Phase 5 — T5.1, T5.2).

Validates:
- _build_cli_args translates named Pydantic fields to CLI flags
- extra_args pass through verbatim
- Dict-type fields (speculative_config, limit_mm_per_prompt) serialize to JSON
- Boolean fields only appear as flags when True
- Docker container lifecycle (start/stop) via mocked SDK
- Health check via GET /health
- Endpoint URL construction
- Registration in AdapterRegistry (T5.2)
- Configured backend_host and docker_network usage
- Docker client factory fallback
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from switchyard.config.models import (
    ModelConfig,
    ResourcesConfig,
    VLLMRuntimeConfig,
)
from switchyard.core.adapter import DeploymentInfo
from switchyard.core.registry import AdapterRegistry

# --- Helpers ---


def _make_model_config(
    overrides: dict[str, Any] | None = None,
) -> ModelConfig:
    """Create a minimal ModelConfig for vLLM tests."""
    defaults: dict[str, Any] = {
        "backend": "vllm",
        "image": "vllm/vllm-openai:latest",
        "runtime": VLLMRuntimeConfig(repo="test/model"),
    }
    if overrides:
        defaults.update(overrides)
    return ModelConfig(**defaults)


def _make_mock_docker_client() -> MagicMock:
    """Create a mock Docker client for adapter tests."""
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.short_id = "abc123"
    mock_client.containers.run.return_value = mock_container
    return mock_client


# --- T5.1: _build_cli_args ---


class TestBuildCliArgs:
    """Tests for VLLMAdapter._build_cli_args static method."""

    def test_minimal_config_produces_model_flag(self) -> None:
        runtime = VLLMRuntimeConfig(repo="org/model")
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--model" in args
        idx = args.index("--model")
        assert args[idx + 1] == "org/model"

    def test_model_local_path(self) -> None:
        runtime = VLLMRuntimeConfig(model="/data/models/my-model")
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--model" in args
        idx = args.index("--model")
        assert args[idx + 1] == "/data/models/my-model"

    def test_served_model_name(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            served_model_name="my-custom-name",
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--served-model-name" in args
        idx = args.index("--served-model-name")
        assert args[idx + 1] == "my-custom-name"

    def test_gpu_memory_utilization(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            gpu_memory_utilization=0.95,
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--gpu-memory-utilization" in args
        idx = args.index("--gpu-memory-utilization")
        assert args[idx + 1] == "0.95"

    def test_tensor_parallel_size(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            tensor_parallel_size=4,
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--tensor-parallel-size" in args
        idx = args.index("--tensor-parallel-size")
        assert args[idx + 1] == "4"

    def test_bool_true_becomes_flag(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            trust_remote_code=True,
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--trust-remote-code" in args

    def test_bool_false_omitted(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            trust_remote_code=False,
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--trust-remote-code" not in args

    def test_none_fields_omitted(self) -> None:
        runtime = VLLMRuntimeConfig(repo="org/model", dtype=None)
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--dtype" not in args

    def test_extra_args_passthrough(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            extra_args={"custom-flag": "custom-value", "another-flag": "123"},
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--custom-flag" in args
        idx = args.index("--custom-flag")
        assert args[idx + 1] == "custom-value"
        assert "--another-flag" in args

    def test_extra_args_bool_true(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            extra_args={"some-boolean": True},
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--some-boolean" in args

    def test_extra_args_bool_false_omitted(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            extra_args={"some-boolean": False},
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--some-boolean" not in args

    def test_dict_field_json_serialized(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            speculative_config={"method": "ngram", "num_speculative_tokens": 3},
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--speculative-config" in args

    def test_limit_mm_per_prompt_json(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            limit_mm_per_prompt={"image": 4},
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--limit-mm-per-prompt" in args

    def test_reasoning_parser(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            reasoning_parser="qwen3",
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--reasoning-parser" in args
        idx = args.index("--reasoning-parser")
        assert args[idx + 1] == "qwen3"

    def test_tool_call_parser(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            tool_call_parser="qwen3_coder",
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--tool-call-parser" in args
        idx = args.index("--tool-call-parser")
        assert args[idx + 1] == "qwen3_coder"

    def test_enable_auto_tool_choice(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            enable_auto_tool_choice=True,
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--enable-auto-tool-choice" in args

    def test_kv_cache_dtype(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            kv_cache_dtype="fp8_e4m3",
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--kv-cache-dtype" in args
        idx = args.index("--kv-cache-dtype")
        assert args[idx + 1] == "fp8_e4m3"

    def test_max_model_len(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            max_model_len=100000,
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--max-model-len" in args
        idx = args.index("--max-model-len")
        assert args[idx + 1] == "100000"

    def test_quantization(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            quantization="awq",
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--quantization" in args
        idx = args.index("--quantization")
        assert args[idx + 1] == "awq"

    def test_disable_custom_all_reduce(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            disable_custom_all_reduce=True,
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--disable-custom-all-reduce" in args

    def test_language_model_only(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            language_model_only=True,
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--language-model-only" in args

    def test_many_fields_combined(self) -> None:
        runtime = VLLMRuntimeConfig(
            repo="org/model",
            dtype="bfloat16",
            tensor_parallel_size=2,
            gpu_memory_utilization=0.92,
            max_model_len=4096,
            trust_remote_code=True,
            extra_args={"custom-flag": "value"},
        )
        from switchyard.adapters.vllm import VLLMAdapter

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--model" in args
        assert "--dtype" in args
        assert "--tensor-parallel-size" in args
        assert "--gpu-memory-utilization" in args
        assert "--max-model-len" in args
        assert "--trust-remote-code" in args
        assert "--custom-flag" in args


# --- T5.1: start / stop / health / endpoint ---


class TestVLLMAdapter:
    """VLLMAdapter lifecycle tests (Docker SDK mocked)."""

    @pytest.fixture
    def adapter(self) -> Generator:  # type: ignore[type-arg]
        from switchyard.adapters.vllm import VLLMAdapter

        mock_client = _make_mock_docker_client()
        yield VLLMAdapter(docker_client=mock_client)

    @pytest.fixture
    def adapter_remote(self) -> Generator:  # type: ignore[type-arg]
        """Adapter configured for remote Docker."""
        from switchyard.adapters.vllm import VLLMAdapter

        mock_client = _make_mock_docker_client()
        yield VLLMAdapter(
            docker_client=mock_client,
            backend_host="trainbox",
            backend_scheme="http",
            docker_network="custom-net",
        )

    def test_start_creates_container(
        self, adapter,
    ) -> None:
        config = _make_model_config()

        info = adapter.start(config, 8001)

        adapter._client.containers.run.assert_called_once()
        assert info.container_id == "abc123"
        assert info.port == 8001
        assert info.backend == "vllm"

    def test_start_passes_image_and_port(
        self, adapter,
    ) -> None:
        config = _make_model_config()

        adapter.start(config, 8001)

        call_kwargs = adapter._client.containers.run.call_args
        assert call_kwargs[1]["image"] == "vllm/vllm-openai:latest"
        # port bindings should include host:container mapping
        assert "ports" in call_kwargs[1]
        # Internal port should be 8000 (vLLM default)
        assert 8000 in call_kwargs[1]["ports"]

    def test_start_passes_network(
        self, adapter,
    ) -> None:
        config = _make_model_config()

        adapter.start(config, 8001)

        # Default adapter has no network set, so network is omitted
        call_kwargs = adapter._client.containers.run.call_args
        assert "network" not in call_kwargs[1]

    def test_start_passes_configured_network(
        self, adapter_remote,
    ) -> None:
        """Adapter with docker_network passes it to container.run()."""
        config = _make_model_config()

        adapter_remote.start(config, 8001)

        call_kwargs = adapter_remote._client.containers.run.call_args
        assert call_kwargs[1]["network"] == "custom-net"

    def test_start_passes_hf_token_env(
        self, adapter,
    ) -> None:
        config = _make_model_config(
            {"runtime": VLLMRuntimeConfig(repo="org/model", hf_token="secret123")}
        )

        adapter.start(config, 8001)

        call_kwargs = adapter._client.containers.run.call_args
        assert "environment" in call_kwargs[1]
        assert call_kwargs[1]["environment"]["HF_TOKEN"] == "secret123"

    def test_start_passes_memory_limit(
        self, adapter,
    ) -> None:
        config = _make_model_config(
            {
                "runtime": VLLMRuntimeConfig(repo="org/model"),
                "resources": ResourcesConfig(memory="32g"),
            }
        )

        adapter.start(config, 8001)

        call_kwargs = adapter._client.containers.run.call_args
        assert call_kwargs[1]["mem_limit"] == "32g"

    def test_start_raises_on_docker_error(
        self, adapter,
    ) -> None:
        config = _make_model_config()
        adapter._client.containers.run.side_effect = Exception("docker error")

        with pytest.raises(RuntimeError, match="failed to start"):
            adapter.start(config, 8001)

    def test_start_uses_internal_port_8000(self) -> None:
        """Container port binding maps internal 8000 to host port."""
        from switchyard.adapters.vllm import VLLMAdapter

        mock_client = _make_mock_docker_client()
        adapter = VLLMAdapter(docker_client=mock_client)
        config = _make_model_config()

        adapter.start(config, 9000)

        call_kwargs = mock_client.containers.run.call_args
        assert call_kwargs[1]["ports"] == {8000: 9000}

    def test_start_without_docker_client_uses_factory(self) -> None:
        """Adapter without injected client uses docker factory."""
        from switchyard.adapters.vllm import VLLMAdapter

        with patch("switchyard.core.docker.get_docker_client") as mock_factory:
            mock_client = MagicMock()
            mock_container = MagicMock()
            mock_container.short_id = "abc123"
            mock_client.containers.run.return_value = mock_container
            mock_factory.return_value = mock_client

            adapter = VLLMAdapter()  # no docker_client
            config = _make_model_config()
            adapter.start(config, 8001)

            mock_factory.assert_called_once()

    def test_stop_stops_and_removes_container(
        self, adapter,
    ) -> None:
        deployment = DeploymentInfo(
            model_name="test-model",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
        )
        container_mock = MagicMock()
        adapter._client.containers.get.return_value = container_mock

        adapter.stop(deployment)

        container_mock.stop.assert_called_once()
        container_mock.remove.assert_called_once()

    def test_health_running(self) -> None:
        from switchyard.adapters.vllm import VLLMAdapter

        adapter = VLLMAdapter()
        deployment = DeploymentInfo(
            model_name="test-model",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
        )

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_instance = MagicMock()
            mock_instance.get.return_value = mock_response
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_instance
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            status = adapter.health(deployment)
            assert status == "running"

            mock_instance.get.assert_called_with("http://localhost:8001/health")

    def test_health_uses_configured_backend_host(self) -> None:
        """Health check uses backend_host from adapter config."""
        from switchyard.adapters.vllm import VLLMAdapter

        adapter = VLLMAdapter(backend_host="trainbox")
        deployment = DeploymentInfo(
            model_name="test-model",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
            metadata={"backend_host": "trainbox"},
        )

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_instance = MagicMock()
            mock_instance.get.return_value = mock_response
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_instance
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            status = adapter.health(deployment)
            assert status == "running"

            mock_instance.get.assert_called_with("http://trainbox:8001/health")

    def test_health_uses_metadata_backend_host(self) -> None:
        """Health check prefers backend_host from DeploymentInfo metadata."""
        from switchyard.adapters.vllm import VLLMAdapter

        adapter = VLLMAdapter(backend_host="localhost")
        deployment = DeploymentInfo(
            model_name="test-model",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
            metadata={"backend_host": "remote-host", "backend_scheme": "https"},
        )

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_instance = MagicMock()
            mock_instance.get.return_value = mock_response
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_instance
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            status = adapter.health(deployment)
            assert status == "running"

            mock_instance.get.assert_called_with("https://remote-host:8001/health")

    def test_health_error_status(self) -> None:
        from switchyard.adapters.vllm import VLLMAdapter

        adapter = VLLMAdapter()
        deployment = DeploymentInfo(
            model_name="test-model",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
        )

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_instance = MagicMock()
            mock_instance.get.return_value = mock_response
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_instance
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            status = adapter.health(deployment)
            assert status == "error"

    def test_health_connection_error(self) -> None:
        from switchyard.adapters.vllm import VLLMAdapter

        adapter = VLLMAdapter()
        deployment = DeploymentInfo(
            model_name="test-model",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
        )

        with patch("httpx.Client") as mock_client:
            mock_instance = MagicMock()
            import httpx

            mock_instance.get.side_effect = httpx.ConnectError("refused")
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_instance
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            status = adapter.health(deployment)
            assert status == "error"

    def test_endpoint_url(self) -> None:
        from switchyard.adapters.vllm import VLLMAdapter

        adapter = VLLMAdapter()
        deployment = DeploymentInfo(
            model_name="test-model",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
        )

        url = adapter.endpoint(deployment)
        assert url == "http://localhost:8001"

    def test_endpoint_uses_configured_host(self) -> None:
        """Endpoint uses backend_host and backend_scheme from metadata."""
        from switchyard.adapters.vllm import VLLMAdapter

        adapter = VLLMAdapter(backend_host="trainbox", backend_scheme="https")
        deployment = DeploymentInfo(
            model_name="test-model",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
            metadata={"backend_host": "trainbox", "backend_scheme": "https"},
        )

        url = adapter.endpoint(deployment)
        assert url == "https://trainbox:8001"

    def test_endpoint_falls_back_to_adapter_defaults(self) -> None:
        """Endpoint falls back to adapter defaults when metadata is empty."""
        from switchyard.adapters.vllm import VLLMAdapter

        adapter = VLLMAdapter(backend_host="remote-host")
        deployment = DeploymentInfo(
            model_name="test-model",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
        )

        url = adapter.endpoint(deployment)
        assert url == "http://remote-host:8001"

    def test_start_returns_backend_host_in_metadata(self) -> None:
        """DeploymentInfo metadata includes backend_host and backend_scheme."""
        from switchyard.adapters.vllm import VLLMAdapter

        mock_client = _make_mock_docker_client()
        adapter = VLLMAdapter(
            docker_client=mock_client,
            backend_host="trainbox",
            backend_scheme="https",
        )
        config = _make_model_config()

        info = adapter.start(config, 8001)

        assert info.metadata["backend_host"] == "trainbox"
        assert info.metadata["backend_scheme"] == "https"


# --- T5.2: Adapter Registration ---


class TestVLLMAdapterRegistration:
    """Tests for vLLM adapter registration in the registry."""

    def test_vllm_registered_after_setup(self) -> None:
        """After calling register_vllm(), 'vllm' is in the registry."""
        registry = AdapterRegistry()
        from switchyard.adapters.vllm import register_vllm

        register_vllm(registry)
        assert "vllm" in registry.list_backends()

    def test_vllm_creates_correct_adapter(self) -> None:
        registry = AdapterRegistry()
        from switchyard.adapters.vllm import register_vllm

        register_vllm(registry)
        adapter = registry.create("vllm")
        from switchyard.adapters.vllm import VLLMAdapter

        assert isinstance(adapter, VLLMAdapter)
