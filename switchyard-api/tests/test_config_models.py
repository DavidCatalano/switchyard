"""Tests for Pydantic configuration models (T1.2)."""

import pytest

from switchyard.config.models import (
    Config,
    ControlConfig,
    GlobalConfig,
    ModelConfig,
    ResourcesConfig,
    RuntimeDefaults,
    VLLMRuntimeConfig,
)


class TestVLLMRuntimeConfig:
    """Tests for VLLMRuntimeConfig — Tier 1 + Tier 2 named fields."""

    def test_minimal_valid_config(self) -> None:
        """Only repo is required; all other fields optional."""
        config = VLLMRuntimeConfig(repo="Qwen/Qwen2-32B")
        assert config.repo == "Qwen/Qwen2-32B"
        assert config.model is None

    def test_model_path_instead_of_repo(self) -> None:
        """Local filesystem path is accepted."""
        config = VLLMRuntimeConfig(model="/data/LLM/my-model")
        assert config.model == "/data/LLM/my-model"
        assert config.repo is None

    def test_minimal_config_allows_no_model_or_repo(self) -> None:
        """VLLMRuntimeConfig alone allows neither (used at defaults level)."""
        config = VLLMRuntimeConfig()
        assert config.model is None
        assert config.repo is None

    def test_tier1_fields(self) -> None:
        """Tier 1 fields are accepted with proper types."""
        config = VLLMRuntimeConfig(
            repo="Qwen/Qwen2-32B",
            max_model_len=100000,
            dtype="bfloat16",
            quantization="awq",
            trust_remote_code=True,
            served_model_name="my-model",
            reasoning_parser="qwen3",
            tool_call_parser="qwen3_coder",
            limit_mm_per_prompt={"image": 4},
        )
        assert config.max_model_len == 100000
        assert config.dtype == "bfloat16"
        assert config.quantization == "awq"
        assert config.trust_remote_code is True
        assert config.served_model_name == "my-model"
        assert config.reasoning_parser == "qwen3"
        assert config.tool_call_parser == "qwen3_coder"
        assert config.limit_mm_per_prompt == {"image": 4}

    def test_tier2_fields(self) -> None:
        """Tier 2 fields are accepted with proper types."""
        config = VLLMRuntimeConfig(
            repo="test/repo",
            pipeline_parallel_size=2,
            distributed_executor_backend="ray",
            block_size=32,
            uvicorn_log_level="warning",
            api_key="secret-key",
            hf_token="hf_token123",
            seed=42,
            max_num_seqs=8,
            max_num_batched_tokens=2048,
            enable_chunked_prefill=True,
            enable_auto_tool_choice=True,
            speculative_config={"method": "qwen3_next_mtp"},
            language_model_only=True,
        )
        assert config.pipeline_parallel_size == 2
        assert config.distributed_executor_backend == "ray"
        assert config.block_size == 32
        assert config.uvicorn_log_level == "warning"
        assert config.api_key == "secret-key"
        assert config.hf_token == "hf_token123"
        assert config.seed == 42
        assert config.hf_token == "hf_token123"
        assert config.seed == 42
        assert config.max_num_seqs == 8
        assert config.max_num_batched_tokens == 2048
        assert config.enable_chunked_prefill is True
        assert config.enable_auto_tool_choice is True
        assert config.speculative_config == {"method": "qwen3_next_mtp"}
        assert config.language_model_only is True

    def test_extra_args_passthrough(self) -> None:
        """extra_args accepts arbitrary key-value pairs."""
        config = VLLMRuntimeConfig(
            repo="test/repo",
            extra_args={"custom-flag": "value", "another-flag": 42},
        )
        assert config.extra_args == {"custom-flag": "value", "another-flag": 42}

    def test_defaults(self) -> None:
        """Optional fields default to None, extra_args to empty dict."""
        config = VLLMRuntimeConfig(repo="test/repo")
        assert config.max_model_len is None
        assert config.dtype is None
        assert config.tensor_parallel_size is None
        assert config.extra_args == {}

    def test_model_from_dict(self) -> None:
        """model_validate works for dict -> Config conversion."""
        config = VLLMRuntimeConfig.model_validate(
            {"repo": "test/repo", "dtype": "auto"},
        )
        assert config.repo == "test/repo"
        assert config.dtype == "auto"

    def test_limit_mm_per_prompt_rich_form(self) -> None:
        """limit_mm_per_prompt accepts rich form with size hints."""
        config = VLLMRuntimeConfig(
            repo="test/repo",
            limit_mm_per_prompt={
                "image": {"count": 5, "width": 512, "height": 512},
                "video": 2,
            },
        )
        assert config.limit_mm_per_prompt == {
            "image": {"count": 5, "width": 512, "height": 512},
            "video": 2,
        }


class TestGlobalConfig:
    """Tests for GlobalConfig."""

    def test_defaults(self) -> None:
        """Global config has sensible defaults."""
        config = GlobalConfig()
        assert config.docker_network == "model-runtime"
        assert config.base_port == 8000
        assert config.log_level == "info"

    def test_custom_values(self) -> None:
        """Custom values are accepted."""
        config = GlobalConfig(
            docker_network="custom-net",
            base_port=9000,
            log_level="debug",
        )
        assert config.docker_network == "custom-net"
        assert config.base_port == 9000
        assert config.log_level == "debug"


class TestResourcesConfig:
    """Tests for ResourcesConfig."""

    def test_memory_string(self) -> None:
        """Memory is stored as string (e.g. '32g')."""
        config = ResourcesConfig(memory="32g")
        assert config.memory == "32g"

    def test_optional_memory(self) -> None:
        """Memory is optional."""
        config = ResourcesConfig()
        assert config.memory is None


class TestControlConfig:
    """Tests for ControlConfig."""

    def test_auto_start_default(self) -> None:
        """auto_start defaults to False."""
        config = ControlConfig()
        assert config.auto_start is False

    def test_auto_start_override(self) -> None:
        """auto_start can be explicitly set."""
        config = ControlConfig(auto_start=True)
        assert config.auto_start is True


class TestModelConfig:
    """Tests for ModelConfig."""

    def test_full_model_config(self) -> None:
        """ModelConfig with all optional fields populated."""
        config = ModelConfig(
            backend="vllm",
            image="vllm/vllm-openai:latest",
            runtime=VLLMRuntimeConfig(
                repo="Qwen/Qwen2-32B",
                max_model_len=4096,
            ),
        )
        assert config.backend == "vllm"
        assert config.image == "vllm/vllm-openai:latest"
        assert config.runtime.repo == "Qwen/Qwen2-32B"
        assert config.control.auto_start is False
        assert config.resources.memory is None

    def test_minimal_model_config(self) -> None:
        """Minimal ModelConfig with only required fields."""
        config = ModelConfig(
            backend="vllm",
            image="vllm/vllm-openai:latest",
            runtime=VLLMRuntimeConfig(repo="test/repo"),
        )
        assert config.backend == "vllm"

    def test_requires_exactly_one_model_or_repo(self) -> None:
        """ModelConfig enforces that runtime has model or repo (not both)."""
        with pytest.raises(ValueError, match="exactly one of"):
            ModelConfig(
                backend="vllm",
                image="img:latest",
                runtime=VLLMRuntimeConfig(model="/path", repo="hf/repo"),
            )

    def test_requires_at_least_one_model_or_repo(self) -> None:
        """ModelConfig requires runtime to have model or repo."""
        with pytest.raises(ValueError, match="exactly one of"):
            ModelConfig(
                backend="vllm",
                image="img:latest",
                runtime=VLLMRuntimeConfig(),
            )


class TestRuntimeDefaults:
    """Tests for RuntimeDefaults."""

    def test_empty_defaults(self) -> None:
        """Defaults can be empty."""
        defaults = RuntimeDefaults()
        assert defaults.defaults == {}

    def test_multiple_backends(self) -> None:
        """Defaults can specify multiple backends."""
        defaults = RuntimeDefaults(
            defaults={
                "vllm": {"gpu_memory_utilization": 0.92, "tensor_parallel_size": 2},
                "koboldcpp": {"n_gpu_layers": -1},
            },
        )
        assert "vllm" in defaults.defaults
        assert "koboldcpp" in defaults.defaults


class TestConfig:
    """Tests for top-level Config."""

    def test_minimal_valid_config(self) -> None:
        """Minimal valid config with one model."""
        config = Config(
            models={
                "qwen-32b": {
                    "backend": "vllm",
                    "image": "vllm/vllm-openai:latest",
                    "runtime": {"repo": "Qwen/Qwen2-32B"},
                },
            },
        )
        assert "qwen-32b" in config.models
        assert config.models["qwen-32b"].backend == "vllm"

    def test_full_config_with_all_levels(self) -> None:
        """Config with global, runtime_defaults, and models."""
        config = Config.model_validate({
            "global": {"base_port": 9000},
            "runtime_defaults": {
                "vllm": {
                    "gpu_memory_utilization": 0.92,
                    "tensor_parallel_size": 2,
                    "dtype": "auto",
                },
            },
            "models": {
                "llama-70b": {
                    "backend": "vllm",
                    "image": "vllm/vllm-openai:latest",
                    "runtime": {
                        "repo": "meta-llama/Llama-3.1-70B",
                        "tensor_parallel_size": 4,
                    },
                },
            },
        })
        assert config.global_config.base_port == 9000
        assert config.models["llama-70b"].runtime.tensor_parallel_size == 4

    def test_model_validate_from_dict(self) -> None:
        """Config.model_validate works for raw YAML dict."""
        raw = {
            "global": {"base_port": 8000},
            "runtime_defaults": {
                "vllm": {"gpu_memory_utilization": 0.92},
            },
            "models": {
                "test-model": {
                    "backend": "vllm",
                    "image": "vllm/vllm-openai:latest",
                    "runtime": {"repo": "test/repo"},
                },
            },
        }
        config = Config.model_validate(raw)
        assert config.global_config.base_port == 8000
        assert config.models["test-model"].runtime.repo == "test/repo"

    def test_rejects_both_model_and_repo(self) -> None:
        """Config rejects models that specify both model and repo."""
        with pytest.raises(ValueError, match="exactly one of"):
            Config.model_validate({
                "models": {
                    "bad-model": {
                        "backend": "vllm",
                        "image": "vllm/vllm-openai:latest",
                        "runtime": {"model": "/path", "repo": "hf/repo"},
                    },
                },
            })

    def test_rejects_missing_model_and_repo(self) -> None:
        """Config rejects models missing both model and repo."""
        with pytest.raises(ValueError, match="exactly one of"):
            Config.model_validate({
                "models": {
                    "bad-model": {
                        "backend": "vllm",
                        "image": "vllm/vllm-openai:latest",
                        "runtime": {},
                    },
                },
            })

    def test_model_from_dict(self) -> None:
        """Config.model_validate works for dict -> Config conversion."""
        config = Config.model_validate({
            "models": {
                "test": {
                    "backend": "vllm",
                    "image": "vllm/vllm-openai:latest",
                    "runtime": {"model": "/data/models/test"},
                },
            },
        })
        assert config.models["test"].runtime.model == "/data/models/test"
