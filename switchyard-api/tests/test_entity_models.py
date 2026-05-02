"""Tests for new entity-based config models (SEP-002 Phase 1).

Validates each entity model independently:
- HostConfig: Docker connectivity, port ranges, stores, accelerators, container defaults
- RuntimeConfig: backend name, container image, CLI defaults, container defaults
- ModelConfig: store reference, model-family defaults
- DeploymentConfig: references, overrides, placement, container overrides, extra_args
- Config: top-level dict of all four entity sections
- AppSettings: .env bootstrap fields
"""

from __future__ import annotations

import pytest

from switchyard.config.models import (
    AppSettings,
    Config,
    DeploymentConfig,
    HostConfig,
    ModelConfig,
    RuntimeConfig,
    StoreConfig,
)

# ---------------------------------------------------------------------------
# T1.7 — HostConfig tests
# ---------------------------------------------------------------------------


class TestHostConfig:
    """HostConfig — machine-specific infrastructure."""

    def test_minimal_valid_host(self) -> None:
        """Minimal host with only required fields."""
        host = HostConfig()
        assert host.docker_network == "model-runtime"
        assert host.backend_host == "localhost"
        assert host.backend_scheme == "http"

    def test_full_host_config(self) -> None:
        """HostConfig with all fields populated."""
        host = HostConfig(
            docker_host="tcp://127.0.0.1:2375",
            docker_network="ai_net",
            backend_host="trainbox",
            backend_scheme="http",
            port_range=[18000, 18100],
            accelerators=[
                {"id": "0", "type": "cuda", "vram_gb": 24},
                {"id": "1", "type": "cuda", "vram_gb": 24},
            ],
            stores={
                "models": {
                    "host_path": "/data/LLM/oobabooga/models",
                    "container_path": "/models",
                    "mode": "ro",
                },
                "hf_cache": {
                    "host_path": "/data/LLM/huggingface",
                    "container_path": "/hf-cache",
                    "mode": "rw",
                },
            },
            container_defaults={
                "environment": {
                    "HF_HOME": "/hf-cache",
                    "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
                },
                "options": {
                    "ipc": "host",
                    "ulimits": {"memlock": {"soft": -1, "hard": -1}},
                },
            },
        )
        assert host.docker_host == "tcp://127.0.0.1:2375"
        assert host.docker_network == "ai_net"
        assert host.backend_host == "trainbox"
        assert host.port_range == [18000, 18100]
        assert len(host.accelerators) == 2
        assert host.accelerators[0].id == "0"
        assert host.accelerators[0].type == "cuda"
        assert host.accelerators[0].vram_gb == 24
        assert "models" in host.stores
        assert host.stores["models"].host_path == "/data/LLM/oobabooga/models"
        assert host.stores["models"].container_path == "/models"
        assert host.stores["models"].mode == "ro"
        assert host.container_defaults.environment["HF_HOME"] == "/hf-cache"
        assert host.container_defaults.options["ipc"] == "host"

    def test_host_defaults(self) -> None:
        """HostConfig has sensible defaults for all optional fields."""
        host = HostConfig()
        assert host.docker_host is None
        assert host.docker_network == "model-runtime"
        assert host.backend_host == "localhost"
        assert host.backend_scheme == "http"
        assert host.port_range == [8000, 8100]
        assert host.accelerators == []
        assert host.stores == {}
        assert host.container_defaults.environment == {}
        assert host.container_defaults.options == {}

    def test_host_yaml_mapping(self) -> None:
        """HostConfig.model_validate works for raw YAML dict."""
        raw = {
            "docker_host": "tcp://10.0.0.2:2375",
            "docker_network": "custom-net",
            "backend_host": "remote-box",
            "port_range": [19000, 19100],
            "accelerators": [
                {"id": "0", "type": "cuda", "vram_gb": 12},
            ],
            "stores": {
                "models": {
                    "host_path": "D:/LLM/models",
                    "container_path": "/models",
                    "mode": "ro",
                },
            },
        }
        host = HostConfig.model_validate(raw)
        assert host.docker_host == "tcp://10.0.0.2:2375"
        assert host.backend_host == "remote-box"
        assert host.accelerators[0].vram_gb == 12


class TestAcceleratorConfig:
    """AcceleratorConfig — GPU/device inventory."""

    def test_cuda_accelerator(self) -> None:
        acc = HostConfig(
            accelerators=[{"id": "0", "type": "cuda", "vram_gb": 24}],
        )
        assert acc.accelerators[0].id == "0"
        assert acc.accelerators[0].type == "cuda"
        assert acc.accelerators[0].vram_gb == 24


class TestStoreConfig:
    """StoreConfig — named host/container path mapping."""

    def test_store_modes(self) -> None:
        """Store supports 'ro' and 'rw' modes."""
        host = HostConfig(
            stores={
                "read_only": {
                    "host_path": "/data/models",
                    "container_path": "/models",
                    "mode": "ro",
                },
                "read_write": {
                    "host_path": "/data/cache",
                    "container_path": "/cache",
                    "mode": "rw",
                },
            },
        )
        assert host.stores["read_only"].mode == "ro"
        assert host.stores["read_write"].mode == "rw"


class TestContainerDefaults:
    """ContainerDefaults — host-level container environment and options."""

    def test_empty_defaults(self) -> None:
        """Container defaults default to empty dicts."""
        host = HostConfig()
        assert host.container_defaults.environment == {}
        assert host.container_defaults.options == {}

    def test_custom_environment(self) -> None:
        """Custom environment variables are preserved."""
        host = HostConfig(
            container_defaults={"environment": {"CUDA_VISIBLE_DEVICES": "0,1"}},
        )
        assert host.container_defaults.environment["CUDA_VISIBLE_DEVICES"] == "0,1"

    def test_custom_options(self) -> None:
        """Custom Docker options are preserved."""
        host = HostConfig(
            container_defaults={
                "options": {
                    "ipc": "host",
                    "ulimits": {"memlock": {"soft": -1, "hard": -1}},
                },
            },
        )
        assert host.container_defaults.options["ipc"] == "host"


# ---------------------------------------------------------------------------
# T1.7 — RuntimeConfig tests
# ---------------------------------------------------------------------------


class TestRuntimeConfig:
    """RuntimeConfig — backend engine defaults and image choices."""

    def test_minimal_runtime(self) -> None:
        """Minimal runtime with only required fields."""
        runtime = RuntimeConfig(backend="vllm")
        assert runtime.backend == "vllm"
        assert runtime.image is None
        assert runtime.defaults == {}
        assert runtime.container_defaults.internal_port == 8000

    def test_full_runtime(self) -> None:
        """Runtime with all fields populated."""
        runtime = RuntimeConfig(
            backend="vllm",
            image="vllm/vllm-openai:latest",
            defaults={
                "dtype": "auto",
                "enable_prefix_caching": True,
            },
            container_defaults={"internal_port": 8000},
        )
        assert runtime.backend == "vllm"
        assert runtime.image == "vllm/vllm-openai:latest"
        assert runtime.defaults["dtype"] == "auto"
        assert runtime.defaults["enable_prefix_caching"] is True
        assert runtime.container_defaults.internal_port == 8000

    def test_vllm_cpu_runtime(self) -> None:
        """CPU-specific vLLM runtime."""
        runtime = RuntimeConfig(
            backend="vllm",
            image="vllm/vllm-openai-cpu:latest-x86_64",
            defaults={"device": "cpu", "dtype": "bfloat16"},
        )
        assert runtime.backend == "vllm"
        assert runtime.image == "vllm/vllm-openai-cpu:latest-x86_64"

    def test_sglang_runtime(self) -> None:
        """SGLang runtime."""
        runtime = RuntimeConfig(
            backend="sglang",
            image="lmsysorg/sglang:latest",
        )
        assert runtime.backend == "sglang"
        assert runtime.image == "lmsysorg/sglang:latest"

    def test_defaults_empty_by_default(self) -> None:
        """defaults dict is empty by default."""
        runtime = RuntimeConfig(backend="vllm")
        assert runtime.defaults == {}

    def test_yaml_mapping(self) -> None:
        """RuntimeConfig.model_validate works for raw YAML dict."""
        raw = {
            "backend": "vllm",
            "image": "vllm/vllm-openai:latest",
            "defaults": {"dtype": "auto", "enable_prefix_caching": True},
            "container_defaults": {"internal_port": 8000},
        }
        runtime = RuntimeConfig.model_validate(raw)
        assert runtime.backend == "vllm"
        assert runtime.defaults["dtype"] == "auto"


# ---------------------------------------------------------------------------
# T1.7 — ModelConfig tests
# ---------------------------------------------------------------------------


class TestModelConfig:
    """ModelConfig — logical model sources and portable defaults."""

    def test_minimal_model(self) -> None:
        """Minimal model with only required source fields."""
        model = ModelConfig(
            source={"store": "models", "path": "Qwen3.6-27B-FP8"},
        )
        assert model.source.store == "models"
        assert model.source.path == "Qwen3.6-27B-FP8"
        assert model.defaults is None

    def test_full_model(self) -> None:
        """Model with source, defaults, and runtime defaults."""
        model = ModelConfig(
            source={"store": "models", "path": "Qwen3.6-27B-FP8"},
            defaults={
                "served_model_name": "qwen3-27b",
                "reasoning_parser": "qwen3",
                "tool_call_parser": "qwen3_coder",
                "capabilities": {
                    "text": True,
                    "reasoning": True,
                    "tools": True,
                    "vision": False,
                },
            },
            runtime_defaults={
                "max_model_len": 100000,
                "kv_cache_dtype": "fp8_e4m3",
            },
        )
        assert model.source.store == "models"
        assert model.source.path == "Qwen3.6-27B-FP8"
        assert model.defaults["served_model_name"] == "qwen3-27b"
        assert model.defaults["reasoning_parser"] == "qwen3"
        assert model.runtime_defaults["max_model_len"] == 100000

    def test_source_store_required(self) -> None:
        """source.store is required."""
        with pytest.raises((ValueError, TypeError)):
            ModelConfig(source={"path": "some-path"})  # type: ignore[arg-type]

    def test_yaml_mapping(self) -> None:
        """ModelConfig.model_validate works for raw YAML dict."""
        raw = {
            "source": {"store": "models", "path": "Llama-3.1-70B"},
            "defaults": {"served_model_name": "llama-70b"},
        }
        model = ModelConfig.model_validate(raw)
        assert model.source.store == "models"
        assert model.source.path == "Llama-3.1-70B"
        assert model.defaults["served_model_name"] == "llama-70b"


# ---------------------------------------------------------------------------
# T1.7 — DeploymentConfig tests
# ---------------------------------------------------------------------------


class TestDeploymentConfig:
    """DeploymentConfig — concrete model/runtime/host deployment record."""

    def test_minimal_deployment(self) -> None:
        """Minimal deployment with required references."""
        deployment = DeploymentConfig(
            model="qwen3-27b-fp8",
            runtime="vllm",
            host="trainbox",
        )
        assert deployment.model == "qwen3-27b-fp8"
        assert deployment.runtime == "vllm"
        assert deployment.host == "trainbox"
        assert deployment.runtime_overrides == {}
        assert deployment.storage_overrides is None
        assert deployment.placement is None
        assert deployment.container_overrides is None
        assert deployment.extra_args == {}

    def test_full_deployment(self) -> None:
        """Deployment with all override sections."""
        deployment = DeploymentConfig(
            model="qwen3-27b-fp8",
            runtime="vllm",
            host="trainbox",
            runtime_overrides={
                "tensor_parallel_size": 2,
                "gpu_memory_utilization": 0.97,
                "max_model_len": 100000,
            },
            storage_overrides={"path": "Qwen3.6-27B-FP8-alt"},
            placement={"accelerator_ids": ["0", "1"]},
            container_overrides={
                "environment": {"CUDA_VISIBLE_DEVICES": "0,1"},
                "options": {},
            },
            extra_args={"some-new-flag": "value"},
        )
        assert deployment.model == "qwen3-27b-fp8"
        assert deployment.runtime == "vllm"
        assert deployment.host == "trainbox"
        assert deployment.runtime_overrides["tensor_parallel_size"] == 2
        assert deployment.storage_overrides.path == "Qwen3.6-27B-FP8-alt"
        assert deployment.placement.accelerator_ids == ["0", "1"]
        assert deployment.container_overrides.environment[
            "CUDA_VISIBLE_DEVICES"
        ] == "0,1"
        assert deployment.extra_args["some-new-flag"] == "value"

    def test_placement_only(self) -> None:
        """Deployment with only placement override (single GPU)."""
        deployment = DeploymentConfig(
            model="qwen3-27b-fp8",
            runtime="vllm",
            host="laptop",
            placement={"accelerator_ids": ["0"]},
        )
        assert deployment.placement.accelerator_ids == ["0"]

    def test_yaml_mapping(self) -> None:
        """DeploymentConfig.model_validate works for raw YAML dict."""
        raw = {
            "model": "qwen3-27b-fp8",
            "runtime": "vllm",
            "host": "trainbox",
            "runtime_overrides": {
                "tensor_parallel_size": 2,
                "gpu_memory_utilization": 0.97,
            },
            "placement": {"accelerator_ids": ["0", "1"]},
        }
        deployment = DeploymentConfig.model_validate(raw)
        assert deployment.model == "qwen3-27b-fp8"
        assert deployment.runtime == "vllm"
        assert deployment.host == "trainbox"
        assert deployment.runtime_overrides["tensor_parallel_size"] == 2
        assert deployment.placement.accelerator_ids == ["0", "1"]

    # -- Negative / constraint tests --

    def test_deployment_requires_model(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            DeploymentConfig(runtime="vllm", host="laptop")  # type: ignore[arg-type]

    def test_deployment_requires_runtime(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            DeploymentConfig(model="qwen", host="laptop")  # type: ignore[arg-type]

    def test_deployment_requires_host(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            DeploymentConfig(model="qwen", runtime="vllm")  # type: ignore[arg-type]

    def test_placement_empty_accelerator_ids_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            DeploymentConfig(
                model="qwen",
                runtime="vllm",
                host="laptop",
                placement={"accelerator_ids": []},
            )


class TestHostConfigConstraints:
    """HostConfig constraint validation."""

    def test_port_range_must_be_two_elements(self) -> None:
        with pytest.raises(ValueError, match="exactly 2"):
            HostConfig(port_range=[1])
        with pytest.raises(ValueError, match="exactly 2"):
            HostConfig(port_range=[1, 2, 3])

    def test_port_range_start_must_be_le_end(self) -> None:
        with pytest.raises(ValueError, match="must be <="):
            HostConfig(port_range=[9000, 8000])

    def test_port_range_values_must_be_valid(self) -> None:
        with pytest.raises(ValueError, match="1-65535"):
            HostConfig(port_range=[0, 8000])
        with pytest.raises(ValueError, match="1-65535"):
            HostConfig(port_range=[8000, 70000])

    def test_accelerator_type_constraints(self) -> None:
        """Accelerator type is a constrained Literal."""
        host = HostConfig(
            accelerators=[
                {"id": "0", "type": "cuda"},
                {"id": "1", "type": "cpu"},
                {"id": "2", "type": "mps"},
                {"id": "3", "type": "rocm"},
            ],
        )
        assert host.accelerators[0].type == "cuda"
        assert host.accelerators[3].type == "rocm"

    def test_accelerator_invalid_type_rejected(self) -> None:
        with pytest.raises(ValueError):
            HostConfig(accelerators=[{"id": "0", "type": "hypothetical"}])


class TestStoreConfigConstraints:
    """StoreConfig constraint validation."""

    def test_store_requires_host_path(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            StoreConfig(container_path="/models")  # type: ignore[arg-type]

    def test_store_requires_container_path(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            StoreConfig(host_path="/data/models")  # type: ignore[arg-type]

    def test_store_invalid_mode_rejected(self) -> None:
        with pytest.raises(ValueError):
            StoreConfig(
                host_path="/data",
                container_path="/models",
                mode="x",  # type: ignore[arg-type]
            )


class TestRuntimeConfigConstraints:
    """RuntimeConfig constraint validation."""

    def test_runtime_requires_backend(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            RuntimeConfig()  # type: ignore[arg-type]


class TestModelConfigConstraints:
    """ModelConfig constraint validation."""

    def test_model_requires_source(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            ModelConfig()  # type: ignore[arg-type]

    def test_source_requires_store(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            ModelConfig(source={"path": "some-path"})  # type: ignore[arg-type]

    def test_source_requires_path(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            ModelConfig(source={"store": "models"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T1.7 — Config (top-level) tests
# ---------------------------------------------------------------------------


class TestConfigTopLevel:
    """Top-level Config — dict of all four entity sections."""

    def test_minimal_config(self) -> None:
        """Config with empty entity dicts."""
        config = Config()
        assert config.hosts == {}
        assert config.runtimes == {}
        assert config.models == {}
        assert config.deployments == {}

    def test_config_with_entities(self) -> None:
        """Config with one of each entity type."""
        config = Config.model_validate(
            {
                "hosts": {
                    "trainbox": {
                        "backend_host": "trainbox",
                        "port_range": [18000, 18100],
                    },
                },
                "runtimes": {
                    "vllm": {
                        "backend": "vllm",
                        "image": "vllm/vllm-openai:latest",
                    },
                },
                "models": {
                    "qwen3-27b-fp8": {
                        "source": {"store": "models", "path": "Qwen3.6-27B-FP8"},
                    },
                },
                "deployments": {
                    "qwen3-27b-vllm-trainbox": {
                        "model": "qwen3-27b-fp8",
                        "runtime": "vllm",
                        "host": "trainbox",
                    },
                },
            },
        )
        assert "trainbox" in config.hosts
        assert "vllm" in config.runtimes
        assert "qwen3-27b-fp8" in config.models
        assert "qwen3-27b-vllm-trainbox" in config.deployments

    def test_config_partial_sections(self) -> None:
        """Config with only some sections populated."""
        config = Config.model_validate(
            {
                "hosts": {"host1": {}},
                "runtimes": {},
            },
        )
        assert "host1" in config.hosts
        assert config.runtimes == {}
        assert config.models == {}
        assert config.deployments == {}


# ---------------------------------------------------------------------------
# T1.7 — Blank-string rejection tests
# ---------------------------------------------------------------------------


class TestBlankStringRejection:
    """SEP-002 entity models reject empty/whitespace strings."""

    def test_accelerator_blank_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            HostConfig(accelerators=[{"id": "", "type": "cuda"}])
        with pytest.raises(ValueError, match="must not be empty"):
            HostConfig(accelerators=[{"id": "   ", "type": "cuda"}])

    def test_store_blank_host_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            HostConfig(
                stores={
                    "m": {"host_path": "", "container_path": "/m"},
                },
            )
        with pytest.raises(ValueError, match="must not be empty"):
            HostConfig(
                stores={
                    "m": {"host_path": "   ", "container_path": "/m"},
                },
            )

    def test_store_blank_container_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            HostConfig(
                stores={
                    "m": {"host_path": "/data", "container_path": ""},
                },
            )

    def test_runtime_blank_backend_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            RuntimeConfig(backend="")
        with pytest.raises(ValueError, match="must not be empty"):
            RuntimeConfig(backend="  ")

    def test_model_blank_source_store_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            ModelConfig(source={"store": "", "path": "model"})
        with pytest.raises(ValueError, match="must not be empty"):
            ModelConfig(source={"store": "  ", "path": "model"})

    def test_model_blank_source_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            ModelConfig(source={"store": "m", "path": ""})

    def test_deployment_blank_model_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            DeploymentConfig(model="", runtime="vllm", host="h")
        with pytest.raises(ValueError, match="must not be empty"):
            DeploymentConfig(model="  ", runtime="vllm", host="h")

    def test_deployment_blank_runtime_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            DeploymentConfig(model="m", runtime="", host="h")

    def test_deployment_blank_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            DeploymentConfig(model="m", runtime="vllm", host="")

    def test_placement_blank_accelerator_ids_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            DeploymentConfig(
                model="m",
                runtime="vllm",
                host="h",
                placement={"accelerator_ids": [""]},
            )
        with pytest.raises(ValueError, match="must not be empty"):
            DeploymentConfig(
                model="m",
                runtime="vllm",
                host="h",
                placement={"accelerator_ids": ["0", "   "]},
            )

    def test_storage_overrides_blank_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            DeploymentConfig(
                model="m",
                runtime="vllm",
                host="h",
                storage_overrides={"path": ""},
            )


# ---------------------------------------------------------------------------
# T1.7 — AppSettings tests
# ---------------------------------------------------------------------------


class TestAppSettings:
    """AppSettings — .env bootstrap fields."""

    def test_defaults(self) -> None:
        """All AppSettings fields default to None."""
        # AppSettings reads from .env / env vars; in isolated tests they're None
        settings = AppSettings()
        assert settings.config_path is None
        assert settings.log_level is None
        assert settings.api_host is None
        assert settings.api_port is None
        assert settings.active_host is None
        assert settings.docker_host is None
