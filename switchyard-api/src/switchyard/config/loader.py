"""Config loader — YAML parsing, env var overrides, and resolution.

SEP-002 entity-based loader:
  hosts, runtimes, models, deployments -> ResolvedDeployment
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from switchyard.config.models import (
    AppSettings,
    Config,
    DeploymentConfig,
    HostConfig,
    ModelConfig,
    ResolvedDeployment,
    RuntimeConfig,
)

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads and validates YAML configuration.

    Reads entity-based config (hosts, runtimes, models, deployments) from
    a YAML file and validates cross-entity references at load time.
    """

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        """Load entity-based config from YAML file.

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
        config = Config.model_validate(raw)

        logger.info(
            "loaded config from %s: %d host(s), %d runtime(s), "
            "%d model(s), %d deployment(s)",
            file_path,
            len(config.hosts),
            len(config.runtimes),
            len(config.models),
            len(config.deployments),
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
    def load_entity_config(path: Path | str) -> Config:
        """Load entity-based (SEP-002) config from YAML file.

        Alias for ``load()`` retained for compatibility during migration.
        """
        return ConfigLoader.load(path)


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
      4. Resolve all host store mounts (for volume mapping)
      5. Cascade-merge runtime args: model defaults -> runtime defaults ->
         model runtime_defaults -> deployment runtime_overrides. deployment
         extra_args are nested under runtime_args["extra_args"].
      6. Cascade-merge container config: host container_defaults -> deployment
         container_overrides
      7. Apply .env docker_host override
      8. Produce ResolvedDeployment
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
    # Order: model defaults -> runtime defaults -> model runtime_defaults
    #        -> deployment runtime_overrides (extra_args nested separately)
    runtime_args = _merge_runtime_args(runtime_cfg, model_cfg, deployment)

    # Inject resolved model path into runtime args.
    # The adapter's _build_cli_args reads runtime.model for --model flag.
    runtime_args["model"] = container_path

    # 4. Resolve all host store mounts for volume mapping
    store_mounts = _resolve_all_stores(host_cfg)

    # 5. Cascade-merge container config
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
        store_mounts=store_mounts,
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


def _resolve_all_stores(host_cfg: HostConfig) -> dict[str, dict[str, str]]:
    """Build Docker volume mount dicts for all host stores.

    Returns ``{host_path: {"bind": container_path, "mode": mode}}``
    for every store defined on the host. The adapter mounts these as
    the container's volumes so model weights and HF cache are visible.
    """
    mounts: dict[str, dict[str, str]] = {}
    for store_cfg in host_cfg.stores.values():
        host_base = store_cfg.host_path.rstrip("/")
        container_base = store_cfg.container_path.rstrip("/")
        mounts[host_base] = {"bind": container_base, "mode": store_cfg.mode}
    return mounts


def _merge_runtime_args(
    runtime_cfg: RuntimeConfig,
    model_cfg: ModelConfig,
    deployment: DeploymentConfig,
) -> dict[str, Any]:
    """Merge runtime args in cascade order.

    Typed layers (1–4) merge as top-level fields in the returned dict.
    deployment.extra_args is nested under "extra_args" to preserve the
    escape hatch: VLLMRuntimeConfig.model_validate() reads
    runtime_args["extra_args"] as its catch-all, so unknown keys are not
    silently dropped.

    Order (later wins on conflict for typed layers):
      1. Model defaults (served_model_name, reasoning_parser, etc.)
      2. Runtime defaults
      3. Model runtime_defaults
      4. Deployment runtime_overrides
    """
    merged: dict[str, Any] = {}
    for layer in (
        model_cfg.defaults or {},
        runtime_cfg.defaults,
        model_cfg.runtime_defaults,
        deployment.runtime_overrides,
    ):
        merged.update(layer)

    # Nested extra_args escape hatch: unknown keys survive resolution
    # and appear as VLLMRuntimeConfig.extra_args at CLI-building time.
    if deployment.extra_args:
        merged["extra_args"] = dict(deployment.extra_args)

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
