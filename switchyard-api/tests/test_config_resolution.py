"""Tests for config resolution (SEP-002 Phase 2).

Validates:
- T2.1: YAML loader parses entity-based config into Config model
- T2.2: Reference resolution (deployment -> model/runtime/host)
- T2.3: Store resolution (store name -> host/container paths)
- T2.4: Runtime cascade merge (runtime defaults -> model -> deployment -> extra_args)
- T2.5: Container cascade merge (host defaults -> deployment overrides)
- T2.6: ResolvedDeployment output shape
- T2.7: Reference validation, store resolution, cascade merge, .env docker_host override
- T2.8: Full realistic fixture resolution
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from switchyard.config.loader import ConfigLoader, resolve_deployment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, content: str) -> Path:
    """Write a YAML config and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


REALISTIC_YAML = dedent("""\
    hosts:
      trainbox:
        backend_host: trainbox
        port_range: [18000, 18100]
        accelerators:
          - id: "0"
            type: cuda
            vram_gb: 24
          - id: "1"
            type: cuda
            vram_gb: 24
        stores:
          models:
            host_path: /data/LLM/oobabooga/models
            container_path: /models
            mode: ro
          hf_cache:
            host_path: /data/LLM/huggingface
            container_path: /hf-cache
            mode: rw
        container_defaults:
          environment:
            HF_HOME: /hf-cache
            PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True
          options:
            ipc: host

    runtimes:
      vllm:
        backend: vllm
        image: vllm/vllm-openai:latest
        defaults:
          dtype: auto
          enable_prefix_caching: true
          gpu_memory_utilization: 0.92
        container_defaults:
          internal_port: 8000

    models:
      qwen3-27b-fp8:
        source:
          store: models
          path: Qwen3.6-27B-FP8
        defaults:
          served_model_name: qwen3-27b
          reasoning_parser: qwen3
        runtime_defaults:
          max_model_len: 100000
          kv_cache_dtype: fp8_e4m3

    deployments:
      qwen3-27b-vllm-trainbox:
        model: qwen3-27b-fp8
        runtime: vllm
        host: trainbox
        runtime_overrides:
          tensor_parallel_size: 2
          gpu_memory_utilization: 0.97
        placement:
          accelerator_ids: ["0", "1"]
        container_overrides:
          environment:
            CUDA_VISIBLE_DEVICES: "0,1"
""")


# ---------------------------------------------------------------------------
# T2.1 — YAML loader
# ---------------------------------------------------------------------------


class TestYAMLLoader:
    """Entity-based config loads from YAML."""

    def test_load_minimal_config(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop: {}
            runtimes:
              vllm:
                backend: vllm
            models: {}
            deployments: {}
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        assert "laptop" in config.hosts
        assert "vllm" in config.runtimes

    def test_load_realistic_config(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, REALISTIC_YAML)
        config = ConfigLoader.load_entity_config(path)
        assert "trainbox" in config.hosts
        assert "vllm" in config.runtimes
        assert "qwen3-27b-fp8" in config.models
        assert "qwen3-27b-vllm-trainbox" in config.deployments

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            ConfigLoader.load_entity_config(tmp_path / "nope.yaml")

    def test_load_invalid_yaml_raises(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, "{{invalid: yaml: [{{[")
        with pytest.raises(ValueError, match="invalid YAML"):
            ConfigLoader.load_entity_config(path)

    # Finding 1: cross-entity reference validation at load time
    def test_load_validates_unknown_model_ref(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop: {}
            runtimes:
              vllm:
                backend: vllm
            models: {}
            deployments:
              d1:
                model: nonexistent
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="unknown model"):
            ConfigLoader.load_entity_config(path)

    def test_load_validates_unknown_runtime_ref(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop: {}
            runtimes: {}
            models:
              m1:
                source:
                  store: models
                  path: model
            deployments:
              d1:
                model: m1
                runtime: nonexistent
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="unknown runtime"):
            ConfigLoader.load_entity_config(path)

    def test_load_validates_unknown_host_ref(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts: {}
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: nonexistent
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="unknown host"):
            ConfigLoader.load_entity_config(path)

    def test_load_validates_unknown_store_ref(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  cache:
                    host_path: /cache
                    container_path: /cache
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="store 'models'"):
            ConfigLoader.load_entity_config(path)


# ---------------------------------------------------------------------------
# T2.2 — Reference resolution
# ---------------------------------------------------------------------------


class TestReferenceResolution:
    """Deployment resolves model/runtime/host by name."""

    def test_resolve_valid_refs(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, REALISTIC_YAML)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "qwen3-27b-vllm-trainbox")
        assert resolved.deployment_name == "qwen3-27b-vllm-trainbox"
        assert resolved.model_name == "qwen3-27b-fp8"
        assert resolved.backend == "vllm"
        assert resolved.host_name == "trainbox"

    def test_unknown_model_raises(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop: {} 
            runtimes:
              vllm:
                backend: vllm
            models: {}
            deployments:
              d1:
                model: nonexistent
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="unknown model"):
            ConfigLoader.load_entity_config(path)

    def test_unknown_runtime_raises(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop: {}
            runtimes: {}
            models:
              m1:
                source:
                  store: models
                  path: model
            deployments:
              d1:
                model: m1
                runtime: nonexistent
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="unknown runtime"):
            ConfigLoader.load_entity_config(path)

    def test_unknown_host_raises(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts: {}
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: nonexistent
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="unknown host"):
            ConfigLoader.load_entity_config(path)


# ---------------------------------------------------------------------------
# T2.3 — Store resolution
# ---------------------------------------------------------------------------


class TestStoreResolution:
    """Named store resolves to host path + container path."""

    def test_store_resolves(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, REALISTIC_YAML)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "qwen3-27b-vllm-trainbox")
        assert resolved.model_host_path == "/data/LLM/oobabooga/models/Qwen3.6-27B-FP8"
        assert resolved.model_container_path == "/models/Qwen3.6-27B-FP8"

    def test_unknown_store_raises(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  cache:
                    host_path: /cache
                    container_path: /cache
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="store 'models'"):
            ConfigLoader.load_entity_config(path)

    def test_storage_override_replaces_path(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  models:
                    host_path: /data/models
                    container_path: /models
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: default-model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
                storage_overrides:
                  path: override-model
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "d1")
        assert resolved.model_host_path == "/data/models/override-model"
        assert resolved.model_container_path == "/models/override-model"

    # Finding 3: reject absolute paths and .. traversal
    def test_reject_absolute_path_in_model_source(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  models:
                    host_path: /data/models
                    container_path: /models
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: /etc/passwd
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="must be a relative path"):
            ConfigLoader.load_entity_config(path)

    def test_reject_dotdot_in_model_source(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  models:
                    host_path: /data/models
                    container_path: /models
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: ../escape
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="must not contain"):
            ConfigLoader.load_entity_config(path)

    def test_reject_absolute_path_in_storage_override(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  models:
                    host_path: /data/models
                    container_path: /models
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
                storage_overrides:
                  path: /etc/passwd
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="must be a relative path"):
            ConfigLoader.load_entity_config(path)

    def test_reject_dotdot_in_storage_override(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  models:
                    host_path: /data/models
                    container_path: /models
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
                storage_overrides:
                  path: ../escape
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="must not contain"):
            ConfigLoader.load_entity_config(path)

    def test_trailing_slash_store_paths(self, tmp_path: Path) -> None:
        """Store paths with trailing slashes produce clean joins."""
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  models:
                    host_path: /data/models/
                    container_path: /models/
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: my-model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "d1")
        assert resolved.model_host_path == "/data/models/my-model"
        assert resolved.model_container_path == "/models/my-model"


# ---------------------------------------------------------------------------
# T2.4 — Runtime cascade merge
# ---------------------------------------------------------------------------


class TestRuntimeCascade:
    """Runtime config cascades: defaults -> model -> deployment -> extra_args."""

    def test_cascade_merge_order(self, tmp_path: Path) -> None:
        # dtype and enable_prefix_caching come from runtime defaults
        # max_model_len and kv_cache_dtype come from model runtime_defaults
        # tensor_parallel_size and gpu_memory_utilization come from deployment overrides
        path = _write_config(tmp_path, REALISTIC_YAML)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "qwen3-27b-vllm-trainbox")

        assert resolved.runtime_args["dtype"] == "auto"
        assert resolved.runtime_args["enable_prefix_caching"] is True
        assert resolved.runtime_args["max_model_len"] == 100000
        assert resolved.runtime_args["kv_cache_dtype"] == "fp8_e4m3"
        assert resolved.runtime_args["tensor_parallel_size"] == 2
        # deployment override wins over runtime default
        assert resolved.runtime_args["gpu_memory_utilization"] == 0.97

    def test_extra_args_append(self, tmp_path: Path) -> None:
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  models:
                    host_path: /data/models
                    container_path: /models
            runtimes:
              vllm:
                backend: vllm
                defaults:
                  dtype: auto
            models:
              m1:
                source:
                  store: models
                  path: model
                runtime_defaults:
                  max_model_len: 50000
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
                runtime_overrides:
                  tensor_parallel_size: 1
                extra_args:
                  custom_flag: true
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "d1")
        assert resolved.runtime_args["dtype"] == "auto"
        assert resolved.runtime_args["max_model_len"] == 50000
        assert resolved.runtime_args["tensor_parallel_size"] == 1
        assert resolved.runtime_args["custom_flag"] is True


# ---------------------------------------------------------------------------
# T2.5 — Container cascade merge
# ---------------------------------------------------------------------------


class TestContainerCascade:
    """Container config cascades: host defaults -> deployment overrides."""

    def test_container_env_merge(self, tmp_path: Path) -> None:
        # HF_HOME from host, CUDA_VISIBLE_DEVICES from deployment override
        path = _write_config(tmp_path, REALISTIC_YAML)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "qwen3-27b-vllm-trainbox")

        assert resolved.container_environment["HF_HOME"] == "/hf-cache"
        assert resolved.container_environment[
            "PYTORCH_CUDA_ALLOC_CONF"
        ] == "expandable_segments:True"
        assert resolved.container_environment[
            "CUDA_VISIBLE_DEVICES"
        ] == "0,1"

    def test_container_options_merge(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, REALISTIC_YAML)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "qwen3-27b-vllm-trainbox")
        assert resolved.container_options["ipc"] == "host"


# ---------------------------------------------------------------------------
# T2.6 — ResolvedDeployment shape
# ---------------------------------------------------------------------------


class TestResolvedDeploymentShape:
    """ResolvedDeployment has all expected fields."""

    def test_all_fields_present(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, REALISTIC_YAML)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "qwen3-27b-vllm-trainbox")

        # Identity
        assert resolved.deployment_name == "qwen3-27b-vllm-trainbox"
        assert resolved.model_name == "qwen3-27b-fp8"
        assert resolved.backend == "vllm"
        assert resolved.host_name == "trainbox"

        # Container
        assert resolved.image == "vllm/vllm-openai:latest"
        assert resolved.internal_port == 8000

        # Store paths
        assert resolved.model_host_path == "/data/LLM/oobabooga/models/Qwen3.6-27B-FP8"
        assert resolved.model_container_path == "/models/Qwen3.6-27B-FP8"

        # Placement
        assert resolved.accelerator_ids == ["0", "1"]

        # Docker
        assert resolved.docker_network == "model-runtime"
        assert resolved.docker_host is None

        # Runtime args (subset check)
        assert resolved.runtime_args["tensor_parallel_size"] == 2

        # Container env
        assert "HF_HOME" in resolved.container_environment
        assert "CUDA_VISIBLE_DEVICES" in resolved.container_environment

    # Finding 2: ResolvedDeployment must have runtime_name, backend_host,
    # backend_scheme, port_range
    def test_resolved_has_identity_fields(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, REALISTIC_YAML)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "qwen3-27b-vllm-trainbox")

        assert resolved.runtime_name == "vllm"
        assert resolved.backend_host == "trainbox"
        assert resolved.backend_scheme == "http"
        assert resolved.port_range == [18000, 18100]


# ---------------------------------------------------------------------------
# T2.7 — .env docker_host override
# ---------------------------------------------------------------------------


class TestDockerHostOverride:
    """.env SWITCHYARD_DOCKER_HOST overrides host docker_host."""

    def test_env_docker_host_overrides(self, tmp_path: Path) -> None:
        """Deployment docker_host reflects .env override."""
        content = dedent("""\
            hosts:
              remote:
                docker_host: tcp://remote-box:2375
                stores:
                  models:
                    host_path: /data/models
                    container_path: /models
            runtimes:
              vllm:
                backend: vllm
            models:
              m1:
                source:
                  store: models
                  path: model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: remote
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)

        # Without override, resolves to host's docker_host
        with patch(
            "switchyard.config.loader.AppSettings"
        ) as mock_settings:
            mock_settings.return_value.docker_host = None
            resolved = resolve_deployment(config, "d1")
            assert resolved.docker_host == "tcp://remote-box:2375"

        # With override, .env wins
        with patch(
            "switchyard.config.loader.AppSettings"
        ) as mock_settings:
            mock_settings.return_value.docker_host = "tcp://override:2375"
            resolved = resolve_deployment(config, "d1")
            assert resolved.docker_host == "tcp://override:2375"


# ---------------------------------------------------------------------------
# T2.8 — Full realistic fixture
# ---------------------------------------------------------------------------


class TestRealisticFixture:
    """End-to-end resolution of qwen3-27b-vllm-trainbox."""

    def test_full_resolution(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, REALISTIC_YAML)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "qwen3-27b-vllm-trainbox")

        # Identity
        assert resolved.deployment_name == "qwen3-27b-vllm-trainbox"
        assert resolved.model_name == "qwen3-27b-fp8"
        assert resolved.runtime_name == "vllm"
        assert resolved.backend == "vllm"
        assert resolved.host_name == "trainbox"
        assert resolved.backend_host == "trainbox"
        assert resolved.backend_scheme == "http"
        assert resolved.port_range == [18000, 18100]

        # Container
        assert resolved.image == "vllm/vllm-openai:latest"
        assert resolved.internal_port == 8000

        # Store
        assert resolved.model_host_path == "/data/LLM/oobabooga/models/Qwen3.6-27B-FP8"
        assert resolved.model_container_path == "/models/Qwen3.6-27B-FP8"

        # Placement
        assert resolved.accelerator_ids == ["0", "1"]

        # Runtime cascade
        runtime = resolved.runtime_args
        assert runtime["dtype"] == "auto"
        assert runtime["enable_prefix_caching"] is True
        assert runtime["max_model_len"] == 100000
        assert runtime["kv_cache_dtype"] == "fp8_e4m3"
        assert runtime["tensor_parallel_size"] == 2
        assert runtime["gpu_memory_utilization"] == 0.97

        # Container cascade
        env = resolved.container_environment
        assert env["HF_HOME"] == "/hf-cache"
        assert env["PYTORCH_CUDA_ALLOC_CONF"] == "expandable_segments:True"
        assert env["CUDA_VISIBLE_DEVICES"] == "0,1"
        assert resolved.container_options["ipc"] == "host"

        # Docker
        assert resolved.docker_network == "model-runtime"
