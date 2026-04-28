"""Tests for config loader and three-level cascade (T1.3)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from switchyard.config.loader import ConfigLoader


def _write_yaml(content: str) -> Path:
    """Write YAML content to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False,
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


VALID_MINIMAL = """\
models:
  test:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      repo: Qwen/Qwen2-32B
"""

VALID_THREE_LEVEL = """\
global:
  base_port: 9000
  docker_network: custom-net
  log_level: debug

runtime_defaults:
  vllm:
    gpu_memory_utilization: 0.92
    tensor_parallel_size: 2
    dtype: auto
    enable_prefix_caching: true
    extra_args:
      global-flag: global-value

models:
  qwen-32b:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      repo: Qwen/Qwen2-32B
      max_model_len: 4096
      tensor_parallel_size: 4
      extra_args:
        model-flag: model-value

  llama-70b:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      model: /data/LLM/llama-70b
      # inherits everything from runtime_defaults.vllm
"""


class TestConfigLoader:
    """Tests for ConfigLoader — YAML loading + cascade resolution."""

    def test_load_minimal_config(self) -> None:
        """Minimal valid YAML loads successfully."""
        path = _write_yaml(VALID_MINIMAL)
        try:
            config = ConfigLoader.load(path)
            assert "test" in config.models
            assert config.models["test"].runtime.repo == "Qwen/Qwen2-32B"
        finally:
            path.unlink()

    def test_load_full_three_level_config(self) -> None:
        """Full config with global, defaults, and models loads."""
        path = _write_yaml(VALID_THREE_LEVEL)
        try:
            config = ConfigLoader.load(path)
            assert config.global_config.base_port == 9000
            assert config.global_config.docker_network == "custom-net"
            assert config.global_config.log_level == "debug"
        finally:
            path.unlink()

    def test_cascade_inherits_defaults(self) -> None:
        """Models inherit runtime_defaults values they don't override."""
        path = _write_yaml(VALID_THREE_LEVEL)
        try:
            config = ConfigLoader.load(path)
            llama = config.models["llama-70b"]
            # llama-70b defines only `model` and `image` — inherits all defaults
            assert llama.runtime.gpu_memory_utilization == 0.92
            assert llama.runtime.tensor_parallel_size == 2
            assert llama.runtime.dtype == "auto"
            assert llama.runtime.enable_prefix_caching is True
        finally:
            path.unlink()

    def test_cascade_per_model_overrides_defaults(self) -> None:
        """Per-model values override runtime_defaults."""
        path = _write_yaml(VALID_THREE_LEVEL)
        try:
            config = ConfigLoader.load(path)
            qwen = config.models["qwen-32b"]
            # Per-model override wins
            assert qwen.runtime.tensor_parallel_size == 4
            # Inherited default
            assert qwen.runtime.gpu_memory_utilization == 0.92
        finally:
            path.unlink()

    def test_cascade_extra_args_merged(self) -> None:
        """extra_args from both levels are merged (per-model wins on conflict)."""
        path = _write_yaml(VALID_THREE_LEVEL)
        try:
            config = ConfigLoader.load(path)
            qwen = config.models["qwen-32b"]
            assert qwen.runtime.extra_args == {
                "global-flag": "global-value",
                "model-flag": "model-value",
            }
            # llama inherits only defaults extra_args
            llama = config.models["llama-70b"]
            assert llama.runtime.extra_args == {
                "global-flag": "global-value",
            }
        finally:
            path.unlink()

    def test_cascade_extra_args_conflict_per_model_wins(self) -> None:
        """Conflicting extra_args keys: per-model value wins."""
        content = """\
runtime_defaults:
  vllm:
    extra_args:
      shared-flag: default-value

models:
  test:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      repo: test/repo
      extra_args:
        shared-flag: override-value
"""
        path = _write_yaml(content)
        try:
            config = ConfigLoader.load(path)
            assert config.models["test"].runtime.extra_args == {
                "shared-flag": "override-value",
            }
        finally:
            path.unlink()

    def test_invalid_yaml_raises(self) -> None:
        """Invalid YAML raises a ValueError."""
        path = _write_yaml("{invalid yaml content")
        try:
            with pytest.raises(ValueError):
                ConfigLoader.load(path)
        finally:
            path.unlink()

    def test_missing_models_field(self) -> None:
        """Config with no models field is valid (empty models dict)."""
        path = _write_yaml("global:\n  base_port: 8000\n")
        try:
            config = ConfigLoader.load(path)
            assert config.models == {}
        finally:
            path.unlink()

    def test_requires_model_or_repo(self) -> None:
        """Config with model missing both model and repo raises."""
        content = """\
models:
  bad:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime: {}
"""
        path = _write_yaml(content)
        try:
            with pytest.raises(ValueError, match="exactly one of"):
                ConfigLoader.load(path)
        finally:
            path.unlink()

    def test_rejects_both_model_and_repo(self) -> None:
        """Config with model specifying both model and repo raises."""
        content = """\
models:
  bad:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      model: /path
      repo: hf/repo
"""
        path = _write_yaml(content)
        try:
            with pytest.raises(ValueError, match="exactly one of"):
                ConfigLoader.load(path)
        finally:
            path.unlink()

    def test_env_override_config_path(self) -> None:
        """SWITCHYARD_CONFIG_PATH env var sets config file location."""
        path = _write_yaml(VALID_MINIMAL)
        try:
            env_path = os.environ
            os.environ = dict(env_path)
            os.environ["SWITCHYARD_CONFIG_PATH"] = str(path)
            try:
                config = ConfigLoader.load()
                assert "test" in config.models
            finally:
                os.environ = env_path
        finally:
            path.unlink()

    def test_env_override_base_port(self) -> None:
        """SWITCHYARD_BASE_PORT env var overrides YAML base_port."""
        path = _write_yaml(VALID_MINIMAL)
        try:
            env_path = os.environ
            os.environ = dict(env_path)
            os.environ["SWITCHYARD_BASE_PORT"] = "7000"
            try:
                config = ConfigLoader.load(path)
                assert config.global_config.base_port == 7000
            finally:
                os.environ = env_path
        finally:
            path.unlink()

    def test_env_override_log_level(self) -> None:
        """SWITCHYARD_LOG_LEVEL env var overrides YAML log_level."""
        path = _write_yaml(VALID_MINIMAL)
        try:
            env_path = os.environ
            os.environ = dict(env_path)
            os.environ["SWITCHYARD_LOG_LEVEL"] = "debug"
            try:
                config = ConfigLoader.load(path)
                assert config.global_config.log_level == "debug"
            finally:
                os.environ = env_path
        finally:
            path.unlink()

    def test_env_overrides_take_precedence(self) -> None:
        """Env vars override YAML values."""
        content = """\
global:
  base_port: 8000
  log_level: info
models:
  test:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      repo: test/repo
"""
        path = _write_yaml(content)
        try:
            env_path = os.environ
            os.environ = dict(env_path)
            os.environ["SWITCHYARD_BASE_PORT"] = "9999"
            os.environ["SWITCHYARD_LOG_LEVEL"] = "warning"
            try:
                config = ConfigLoader.load(path)
                assert config.global_config.base_port == 9999
                assert config.global_config.log_level == "warning"
            finally:
                os.environ = env_path
        finally:
            path.unlink()

    def test_cascade_non_vllm_backend(self) -> None:
        """Non-vLLM backends still work (cascade skips unknown backends)."""
        content = """\
runtime_defaults:
  vllm:
    gpu_memory_utilization: 0.92

models:
  kobold-model:
    backend: koboldcpp
    image: something/kobold:latest
    runtime:
      repo: some/kobold-model
"""
        path = _write_yaml(content)
        try:
            config = ConfigLoader.load(path)
            # kobold model loads; cascade only applies vllm defaults to vllm models
            assert "kobold-model" in config.models
        finally:
            path.unlink()

    def test_no_runtime_defaults_no_error(self) -> None:
        """Config without runtime_defaults section works fine."""
        content = """\
models:
  test:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      repo: test/repo
"""
        path = _write_yaml(content)
        try:
            config = ConfigLoader.load(path)
            assert config.models["test"].runtime.repo == "test/repo"
        finally:
            path.unlink()
