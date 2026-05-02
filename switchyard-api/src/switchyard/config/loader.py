"""Config loader — YAML parsing, env var overrides, and resolution.

Legacy SEP-001 loader:
  runtime_defaults.{backend} -> per-model runtime -> extra_args passthrough

SEP-002 entity-based loader:
  hosts, runtimes, models, deployments -> ResolvedDeployment
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Use the canonical AppSettings from models.py (SEP-002)
# This replaces the duplicate AppSettings class that was here.
from switchyard.config.models import (
    AppSettings,
    Config,
    DeploymentConfig,
    HostConfig,
    LegacyConfig,
    ModelConfig,
    ResolvedDeployment,
    RuntimeConfig,
    VLLMRuntimeConfig,
)

logger = logging.getLogger(__name__)


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

    @staticmethod
    def load_entity_config(path: Path | str) -> Config:
        """Load entity-based (SEP-002) config from YAML file."""
        file_path = Path(path)
        if not file_path.is_file():
            raise ValueError(f"config file not found: {file_path}")

        raw = ConfigLoader._read_yaml(file_path)
        config = Config.model_validate(raw)

        logger.info(
            "loaded entity config from %s: %d host(s), %d runtime(s), "
            "%d model(s), %d deployment(s)",
            file_path,
            len(config.hosts),
            len(config.runtimes),
            len(config.models),
            len(config.deployments),
        )
        return config


# =====================================================================
# SEP-002 Entity Config Loader and Resolver
# =====================================================================


def resolve_deployment(
    config: Config,
    deployment_name: str,
) -> ResolvedDeployment:
    """Resolve a named deployment into a complete ResolvedDeployment.

    Steps:
      1. Look up the deployment by name (references already validated at load)
      2. Resolve model, runtime, host references
      3. Resolve store paths (host path + container path)
      4. Cascade-merge runtime args: runtime defaults -> model runtime_defaults
         -> deployment runtime_overrides -> deployment extra_args
      5. Cascade-merge container config: host container_defaults -> deployment
         container_overrides
      6. Apply .env docker_host override
      7. Produce ResolvedDeployment
    """
    deployment = config.deployments.get(deployment_name)
    if deployment is None:
        raise ValueError(
            f"deployment {deployment_name!r} not found in config"
        )

    # 1. Resolve references (already validated at load, but lookup here)
    model_cfg = config.models[deployment.model]
    runtime_cfg = config.runtimes[deployment.runtime]
    host_cfg = config.hosts[deployment.host]

    # 2. Resolve store paths
    host_path, container_path = _resolve_store_path(
        host_cfg, deployment, model_cfg,
    )

    # 3. Cascade-merge runtime args
    runtime_args = _merge_runtime_args(runtime_cfg, model_cfg, deployment)

    # 4. Cascade-merge container config
    container_env, container_options = _merge_container_config(
        host_cfg, deployment,
    )

    # 5. Determine image
    image = runtime_cfg.image or f"{runtime_cfg.backend}:latest"

    # 6. Determine docker_host (.env override)
    docker_host = AppSettings().docker_host
    if docker_host is None:
        docker_host = host_cfg.docker_host

    # 7. Determine accelerators
    accelerator_ids = (
        deployment.placement.accelerator_ids
        if deployment.placement
        else []
    )

    return ResolvedDeployment(
        deployment_name=deployment_name,
        model_name=deployment.model,
        runtime_name=deployment.runtime,
        backend=runtime_cfg.backend,
        host_name=deployment.host,
        backend_host=host_cfg.backend_host,
        backend_scheme=host_cfg.backend_scheme,
        port_range=host_cfg.port_range,
        image=image,
        internal_port=runtime_cfg.container_defaults.internal_port,
        model_host_path=host_path,
        model_container_path=container_path,
        accelerator_ids=accelerator_ids,
        docker_host=docker_host,
        docker_network=host_cfg.docker_network,
        runtime_args=runtime_args,
        container_environment=container_env,
        container_options=container_options,
        model_defaults=model_cfg.defaults,
    )


def _resolve_store_path(
    host_cfg: HostConfig,
    deployment: DeploymentConfig,
    model_cfg: ModelConfig,
) -> tuple[str, str]:
    """Resolve store reference to host/container paths.

    Returns (host_path, container_path) with proper slash handling.
    Absolute paths and .. traversal are rejected by model validators.
    """
    store_name = model_cfg.source.store
    store_cfg = host_cfg.stores.get(store_name)
    if store_cfg is None:
        raise ValueError(
            f"store {store_name!r} not found on host {host_cfg.backend_host!r}"
        )

    # Use storage_overrides.path if provided, else model source path
    model_path = (
        deployment.storage_overrides.path
        if deployment.storage_overrides
        else model_cfg.source.path
    )

    # Normalize store base paths (strip trailing slashes)
    host_base = store_cfg.host_path.rstrip("/")
    container_base = store_cfg.container_path.rstrip("/")

    return (
        f"{host_base}/{model_path}",
        f"{container_base}/{model_path}",
    )


def _merge_runtime_args(
    runtime_cfg: RuntimeConfig,
    model_cfg: ModelConfig,
    deployment: DeploymentConfig,
) -> dict[str, Any]:
    """Merge runtime args in cascade order.

    Order (later wins on conflict):
      1. Runtime defaults
      2. Model runtime_defaults
      3. Deployment runtime_overrides
      4. Deployment extra_args
    """
    merged: dict[str, Any] = {}
    for layer in (
        runtime_cfg.defaults,
        model_cfg.runtime_defaults,
        deployment.runtime_overrides,
        deployment.extra_args,
    ):
        merged.update(layer)
    return merged


def _merge_container_config(
    host_cfg: HostConfig,
    deployment: DeploymentConfig,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Merge container config in cascade order.

    Order (later wins on conflict):
      1. Host container defaults (environment + options)
      2. Deployment container overrides (environment + options)

    Returns (environment, options) tuple.
    """
    env: dict[str, str] = dict(host_cfg.container_defaults.environment)
    options: dict[str, Any] = dict(host_cfg.container_defaults.options)

    if deployment.container_overrides:
        env.update(deployment.container_overrides.environment)
        options.update(deployment.container_overrides.options)

    return env, options
