"""Config loader — YAML parsing, env var overrides, and three-level cascade.

Loads YAML via ``model_validate`` and resolves the three-level cascade:
  runtime_defaults.{backend} -> per-model runtime -> extra_args passthrough
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings

from switchyard.config.models import LegacyConfig, VLLMRuntimeConfig

logger = logging.getLogger(__name__)


class AppSettings(BaseSettings):
    """Environment variable configuration for the control plane.

    Env vars override YAML values after loading.
    Reads from ``.env`` file in CWD when present.
    """

    model_config = {
        "env_prefix": "SWITCHYARD_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    config_path: str | None = None
    base_port: int | None = None
    log_level: str | None = None
    docker_host: str | None = None
    backend_host: str | None = None
    backend_scheme: str | None = None
    docker_network: str | None = None
    health_interval_seconds: float | None = None
    health_timeout_seconds: float | None = None


def _deep_merge(
    base: Mapping[str, Any], override: Mapping[str, Any],
) -> dict[str, Any]:
    """Deep-merge two dicts.

    ``override`` values win on conflict. Nested dicts are merged recursively,
    *except* ``extra_args`` which is merged at the top level (key-level merge).
    """
    merged = dict(base)
    for key, value in override.items():
        if key == "extra_args":
            # extra_args: merge key-level, per-model wins on conflict
            merged[key] = {**merged.get("extra_args", {}), **value}
        elif (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_cascade(config: LegacyConfig) -> LegacyConfig:
    """Apply the three-level cascade to each model's runtime config.

    For each model:
      1. Start with runtime_defaults.{backend} dict (if any)
      2. Merge per-model runtime dict on top (per-model wins)
      3. extra_args are merged additively (per-model wins on key conflict)
      4. Re-validate as VLLMRuntimeConfig
    """
    for name, model in config.models.items():
        backend = model.backend

        # Get defaults dict for this backend (if any)
        defaults_dict = config.runtime_defaults.get_backend_defaults(backend)

        # Per-model runtime as dict
        model_dict = model.runtime.model_dump(exclude_none=True)

        if not defaults_dict:
            continue

        # Deep-merge defaults + per-model (per-model wins)
        merged = _deep_merge(defaults_dict, model_dict)

        # Re-validate as typed VLLMRuntimeConfig
        model.runtime = VLLMRuntimeConfig.model_validate(merged)
        logger.debug("resolved cascade for model %r: backend=%s", name, backend)

    return config


class ConfigLoader:
    """Loads and validates YAML configuration.

    Three-level cascade is applied automatically:
      global -> runtime_defaults.{backend} -> models.{name}.runtime

    Env vars override global config values after loading.
    """

    @classmethod
    def load(cls, path: Path | str | None = None) -> LegacyConfig:
        """Load config from YAML file.

        ``path`` can be omitted if ``SWITCHYARD_CONFIG_PATH`` is set.

        Fatal (raises ``ValueError``) on invalid YAML or validation errors.
        """
        source = path or AppSettings().config_path

        if source is None:
            raise ValueError(
                "no config path provided; set SWITCHYARD_CONFIG_PATH env var "
                "or pass a path to ConfigLoader.load()"
            )

        file_path = Path(source)
        if not file_path.is_file():
            raise ValueError(f"config file not found: {file_path}")

        raw = cls._read_yaml(file_path)
        config = cls._validate(raw)
        config = cls._apply_env_overrides(config)
        config = _resolve_cascade(config)

        logger.info(
            "loaded config from %s: %d model(s)",
            file_path,
            len(config.models),
        )
        return config

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        """Read and parse YAML file."""
        import yaml  # noqa: PLC0414 — lazy import to avoid pydantic cycle

        try:
            with open(path) as fh:
                data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML in {path}: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"config file must be a YAML mapping, got {type(data).__name__}",
            )

        return data

    @staticmethod
    def _validate(raw: dict[str, Any]) -> LegacyConfig:
        """Validate raw dict against Pydantic models."""
        return LegacyConfig.model_validate(raw)

    @staticmethod
    def _apply_env_overrides(config: LegacyConfig) -> LegacyConfig:
        """Apply environment variable overrides to global config."""
        settings = AppSettings()
        if settings.base_port is not None:
            config.global_config.base_port = settings.base_port
        if settings.log_level is not None:
            config.global_config.log_level = settings.log_level
        if settings.docker_network is not None:
            config.global_config.docker_network = settings.docker_network
        if settings.backend_host is not None:
            config.global_config.backend_host = settings.backend_host
        if settings.backend_scheme is not None:
            config.global_config.backend_scheme = settings.backend_scheme
        return config
