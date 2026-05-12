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

import asyncio
from pathlib import Path
from textwrap import dedent
from typing import Any
from unittest.mock import patch

import pytest

from switchyard.config.loader import ConfigLoader, resolve_deployment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from switchyard.config.models import ResolvedDeployment
from switchyard.core.adapter import DeploymentInfo


def _make_resolved(
    backend: str = "mock",
    deployment_name: str = "test-deployment",
    overrides: dict[str, Any] | None = None,
) -> ResolvedDeployment:
    """Create a minimal ResolvedDeployment for tests."""
    defaults: dict[str, Any] = {
        "deployment_name": deployment_name,
        "model_name": "test-model",
        "runtime_name": backend,
        "backend": backend,
        "host_name": "test-host",
        "backend_host": "localhost",
        "backend_scheme": "http",
        "port_range": [9500, 9600],
        "image": "mock:latest",
        "internal_port": 8000,
        "model_host_path": "/data/models/test",
        "model_container_path": "/models/test",
        "accelerator_ids": [],
        "docker_host": None,
        "docker_network": "model-runtime",
        "runtime_args": {"model": "/models/test"},
        "container_environment": {},
        "container_options": {},
        "store_mounts": {},
        "model_defaults": None,
        "health_timeout_seconds": 300.0,
    }
    if overrides:
        defaults.update(overrides)
    return ResolvedDeployment(**defaults)


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

    def test_reject_windows_forward_slash_absolute_path(self, tmp_path: Path) -> None:
        """Reject C:/LLM/model (Windows forward-slash absolute path)."""
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
                  path: C:/LLM/model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="relative path"):
            ConfigLoader.load_entity_config(path)

    def test_reject_windows_backslash_absolute_path(self, tmp_path: Path) -> None:
        """Reject C:\\LLM\\model (Windows backslash absolute path)."""
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
                  path: C:\\LLM\\model
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="relative path"):
            ConfigLoader.load_entity_config(path)

    def test_reject_windows_backslash_dotdot(self, tmp_path: Path) -> None:
        """Reject ..\\escape (Windows backslash traversal)."""
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
                  path: ..\\escape
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="must not contain"):
            ConfigLoader.load_entity_config(path)

    def test_reject_windows_mixed_backslash_traversal(self, tmp_path: Path) -> None:
        """Reject foo\\..\\bar (Windows mixed backslash traversal)."""
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
                  path: foo\\..\\bar
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

    def test_reject_windows_drive_in_storage_override(self, tmp_path: Path) -> None:
        """Reject C:/model in storage override path."""
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
                  path: C:/LLM/model
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="relative path"):
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

    def test_store_mounts_includes_all_host_stores(self, tmp_path: Path) -> None:
        """store_mounts contains all stores defined on the host."""
        content = dedent("""\
            hosts:
              laptop:
                stores:
                  models:
                    host_path: /data/models
                    container_path: /models
                    mode: ro
                  hf_cache:
                    host_path: /data/hf-cache
                    container_path: /hf-cache
                    mode: rw
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
        # Both stores present
        assert "/data/models" in resolved.store_mounts
        assert "/data/hf-cache" in resolved.store_mounts
        # Correct bind targets and modes
        assert resolved.store_mounts["/data/models"] == {
            "bind": "/models", "mode": "ro",
        }
        assert resolved.store_mounts["/data/hf-cache"] == {
            "bind": "/hf-cache", "mode": "rw",
        }


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
        # extra_args are nested under runtime_args["extra_args"] to preserve
        # the escape hatch: VLLMRuntimeConfig reads them from that key.
        assert resolved.runtime_args["extra_args"] == {"custom_flag": True}

    def test_extra_args_survive_as_vllm_config(self, tmp_path: Path) -> None:
        """extra_args nested under runtime_args survive VLLMRuntimeConfig
        validation and appear in the extra_args catch-all."""
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
                extra_args:
                  unknown-thing: 42
                  another-flag: true
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "d1")
        from switchyard.config.models import VLLMRuntimeConfig

        runtime = VLLMRuntimeConfig.model_validate(resolved.runtime_args)
        assert runtime.extra_args["unknown-thing"] == 42
        assert runtime.extra_args["another-flag"] is True

    def test_extra_args_rendered_as_cli_flags(self, tmp_path: Path) -> None:
        """Adapter builds --flag value CLI args from extra_args."""
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
                extra_args:
                  some-weird-flag: weird-value
                  boolean-flag: true
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "d1")
        from switchyard.adapters.vllm import VLLMAdapter
        from switchyard.config.models import VLLMRuntimeConfig

        runtime = VLLMRuntimeConfig.model_validate(resolved.runtime_args)
        args = VLLMAdapter._build_cli_args(runtime)
        assert "--some-weird-flag" in args
        assert "weird-value" in args
        assert "--boolean-flag" in args

    def test_typo_in_runtime_defaults_raises_at_resolve(self, tmp_path: Path) -> None:
        """Unknown key in runtime.defaults raises ValidationError at resolve time."""
        from pydantic import ValidationError

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
                  max_seqs: 10
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
        config = ConfigLoader.load_entity_config(path)
        with pytest.raises(ValidationError):
            resolve_deployment(config, "d1")

    def test_typo_in_model_defaults_raises_at_resolve(self, tmp_path: Path) -> None:
        """Unknown key in model.defaults raises ValidationError at resolve time."""
        from pydantic import ValidationError

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
                defaults:
                  tensor_parallel_sizes: 2
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        with pytest.raises(ValidationError):
            resolve_deployment(config, "d1")

    def test_typo_in_model_runtime_defaults_raises_at_resolve(
        self, tmp_path: Path
    ) -> None:
        """Unknown key in model.runtime_defaults raises at resolve time."""
        from pydantic import ValidationError

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
                runtime_defaults:
                  max_model_lens: 4096
            deployments:
              d1:
                model: m1
                runtime: vllm
                host: laptop
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        with pytest.raises(ValidationError):
            resolve_deployment(config, "d1")

    def test_typo_in_deployment_runtime_overrides_raises_at_resolve(
        self, tmp_path: Path
    ) -> None:
        """Unknown key in deployment.runtime_overrides raises at resolve time."""
        from pydantic import ValidationError

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
                runtime_overrides:
                  gpu_memory_utilizations: 0.9
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        with pytest.raises(ValidationError):
            resolve_deployment(config, "d1")


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
        assert resolved.docker_host is None

        # Store mounts: all host stores present
        assert len(resolved.store_mounts) == 2
        assert "/data/LLM/oobabooga/models" in resolved.store_mounts
        assert "/data/LLM/huggingface" in resolved.store_mounts
        assert resolved.store_mounts["/data/LLM/oobabooga/models"]["mode"] == "ro"
        assert resolved.store_mounts["/data/LLM/huggingface"]["mode"] == "rw"


# ---------------------------------------------------------------------------
# T4.1-T4.3 — Health timeout cascade
# ---------------------------------------------------------------------------


class TestHealthTimeoutCascade:
    """health_timeout_seconds cascades: deployment > runtime > default."""

    def test_default_300s_when_not_set(self, tmp_path: Path) -> None:
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
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "d1")
        assert resolved.health_timeout_seconds == 300.0

    def test_runtime_level_default_used(self, tmp_path: Path) -> None:
        """Runtime-level default is used when deployment doesn't set it."""
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
                health_timeout_seconds: 600
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
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "d1")
        assert resolved.health_timeout_seconds == 600.0

    def test_deployment_override_takes_precedence(self, tmp_path: Path) -> None:
        """Deployment-level override takes precedence over runtime default."""
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
                health_timeout_seconds: 600
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
                health_timeout_seconds: 900
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "d1")
        assert resolved.health_timeout_seconds == 900.0

    def test_rejects_negative_value(self, tmp_path: Path) -> None:
        """Negative health_timeout_seconds is rejected at config level."""
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
                health_timeout_seconds: -10
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="greater than 0"):
            ConfigLoader.load_entity_config(path)

    def test_rejects_zero_value(self, tmp_path: Path) -> None:
        """Zero health_timeout_seconds is rejected at config level."""
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
                health_timeout_seconds: 0
        """)
        path = _write_config(tmp_path, content)
        with pytest.raises(ValueError, match="greater than 0"):
            ConfigLoader.load_entity_config(path)

    def test_rejects_health_timeout_in_runtime_overrides(self, tmp_path: Path) -> None:
        """health_timeout_seconds in runtime_overrides is rejected."""
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
                runtime_overrides:
                  health_timeout_seconds: 600
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        with pytest.raises(ValueError, match="not a runtime argument"):
            resolve_deployment(config, "d1")

    def test_rejects_health_timeout_in_extra_args(self, tmp_path: Path) -> None:
        """health_timeout_seconds in extra_args is rejected."""
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
                extra_args:
                  health_timeout_seconds: 600
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        with pytest.raises(ValueError, match="not a runtime argument"):
            resolve_deployment(config, "d1")

    def test_integer_yaml_value_coerced_to_float(self, tmp_path: Path) -> None:
        """Integer YAML value (e.g. 600) is coerced to float."""
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
                health_timeout_seconds: 600
        """)
        path = _write_config(tmp_path, content)
        config = ConfigLoader.load_entity_config(path)
        resolved = resolve_deployment(config, "d1")
        assert resolved.health_timeout_seconds == 600.0
        assert isinstance(resolved.health_timeout_seconds, float)


class TestHealthTimeoutLifecycle:
    """health_timeout_seconds flows through metadata to _health_poll()."""

    async def test_load_model_sets_health_timeout_in_metadata(self) -> None:
        """load_model() passes resolved health_timeout_seconds to metadata."""
        from switchyard.core.adapter import BackendAdapter
        from switchyard.core.lifecycle import LifecycleManager
        from switchyard.core.ports import PortAllocator
        from switchyard.core.registry import AdapterRegistry

        class MockAdapter(BackendAdapter):
            def __init__(self, **kwargs: Any) -> None:  # noqa: ANN003
                pass

            def start(self, resolved, port: int) -> DeploymentInfo:
                return DeploymentInfo(
                    model_name="test",
                    backend="mock",
                    port=port,
                    status="loading",
                    container_id="x",
                )

            def stop(self, deployment: DeploymentInfo) -> None:
                pass

            def health(self, deployment: DeploymentInfo) -> str:
                return "running"

            def endpoint(self, deployment: DeploymentInfo) -> str:
                return ""

        registry = AdapterRegistry()
        registry.register("mock", MockAdapter)
        manager = LifecycleManager(
            registry=registry,
            port_allocator=PortAllocator(base_port=9500),
        )
        resolved = _make_resolved(
            deployment_name="test-deployment",
            overrides={"health_timeout_seconds": 600.0},
        )
        info = await manager.load_model("test-deployment", resolved)
        assert info.metadata["health_timeout_seconds"] == 600.0

    async def test_short_timeout_triggers_error(self) -> None:
        """Short health_timeout_seconds triggers error transition."""
        from switchyard.core.adapter import BackendAdapter
        from switchyard.core.lifecycle import LifecycleManager
        from switchyard.core.ports import PortAllocator
        from switchyard.core.registry import AdapterRegistry

        class UnhealthyAdapter(BackendAdapter):
            def __init__(self, **kwargs: Any) -> None:  # noqa: ANN003
                pass

            def start(self, resolved, port: int) -> DeploymentInfo:
                return DeploymentInfo(
                    model_name="test",
                    backend="mock",
                    port=port,
                    status="loading",
                    container_id="x",
                )

            def stop(self, deployment: DeploymentInfo) -> None:
                pass

            def health(self, deployment: DeploymentInfo) -> str:
                return "error"

            def endpoint(self, deployment: DeploymentInfo) -> str:
                return ""

        registry = AdapterRegistry()
        registry.register("mock", UnhealthyAdapter)
        manager = LifecycleManager(
            registry=registry,
            port_allocator=PortAllocator(base_port=9500),
        )
        manager._health_interval = 0.01  # fast polling for test
        resolved = _make_resolved(
            deployment_name="test-deployment",
            overrides={"health_timeout_seconds": 0.1},
        )
        info = await manager.load_model("test-deployment", resolved)
        assert info.status == "loading"
        # Wait for timeout-based error transition
        await manager._wait_for_status("test-deployment", "error", timeout=5.0)
        assert manager.get_status("test-deployment") == "error"

    async def test_long_timeout_keeps_loading(self) -> None:
        """Long health_timeout_seconds keeps loading beyond manager default."""
        from switchyard.core.adapter import BackendAdapter
        from switchyard.core.lifecycle import LifecycleManager
        from switchyard.core.ports import PortAllocator
        from switchyard.core.registry import AdapterRegistry

        class UnhealthyAdapter(BackendAdapter):
            def __init__(self, **kwargs: Any) -> None:  # noqa: ANN003
                pass

            def start(self, resolved, port: int) -> DeploymentInfo:
                return DeploymentInfo(
                    model_name="test",
                    backend="mock",
                    port=port,
                    status="loading",
                    container_id="x",
                )

            def stop(self, deployment: DeploymentInfo) -> None:
                pass

            def health(self, deployment: DeploymentInfo) -> str:
                return "error"

            def endpoint(self, deployment: DeploymentInfo) -> str:
                return ""

        registry = AdapterRegistry()
        registry.register("mock", UnhealthyAdapter)
        manager = LifecycleManager(
            registry=registry,
            port_allocator=PortAllocator(base_port=9500),
        )
        manager._health_interval = 0.01
        manager._health_timeout = 0.05  # manager default is very short
        # But resolved timeout is longer
        resolved = _make_resolved(
            deployment_name="test-deployment",
            overrides={"health_timeout_seconds": 10.0},
        )
        info = await manager.load_model("test-deployment", resolved)
        assert info.status == "loading"
        # After 0.1s, still loading (resolved timeout > manager default)
        await asyncio.sleep(0.1)
        assert manager.get_status("test-deployment") == "loading"
        # Clean up
        await manager.unload_model("test-deployment")
