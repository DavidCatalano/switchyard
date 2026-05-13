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
from switchyard.core.merge import deep_merge

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

    # 3. Cascade-merge runtime args with provenance tracking.
    # Order: model defaults -> runtime defaults -> model runtime_defaults
    #        -> deployment runtime_overrides (extra_args nested separately)
    runtime_args, provenance = _merge_runtime_args(
        runtime_cfg, model_cfg, deployment,
    )

    # Inject resolved model path into runtime args.
    # The adapter's _build_cli_args reads runtime.model for --model flag.
    runtime_args["model"] = container_path

    # Validate merged runtime args as typed config.
    # This catches typos in any cascade layer before any Docker call.
    # Only vLLM has a typed config model today; other backends skip this.
    if runtime_cfg.backend == "vllm":
        from pydantic import ValidationError

        from switchyard.config.models import VLLMRuntimeConfig

        try:
            VLLMRuntimeConfig.model_validate(runtime_args)
        except ValidationError as exc:
            # Enrich the error with provenance so operators know which
            # config layer introduced the unknown key.
            details = []
            for error in exc.errors():
                loc = error.get("loc", ())
                msg = error.get("msg", "validation error")
                field = loc[0] if loc else ""
                if field and field in provenance:
                    layer = provenance[field]
                    details.append(
                        f"{msg} for field {field!r} in {layer}"
                    )
                else:
                    details.append(f"{msg} for field {field!r}")
            raise ValueError(
                "config validation error(s): "
                + "; ".join(details)
            ) from exc

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

    # 8. Resolve health timeout (cascade: deployment > runtime > default)
    _HEALTH_TIMEOUT_DEFAULT = 300.0  # seconds
    if deployment.health_timeout_seconds is not None:
        health_timeout = deployment.health_timeout_seconds
    elif runtime_cfg.health_timeout_seconds is not None:
        health_timeout = runtime_cfg.health_timeout_seconds
    else:
        health_timeout = _HEALTH_TIMEOUT_DEFAULT

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
        health_timeout_seconds=health_timeout,
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
) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge runtime args in cascade order using deep merge.

    Typed layers (1–4) merge as top-level fields in the returned dict.
    `extra_args` can appear in any typed layer and is deep-merged
    naturally. `deployment.extra_args` is deep-merged on top last.

    Cascade order (later wins on conflict for typed layers):
      1. Model defaults (served_model_name, reasoning_parser, etc.)
      2. Runtime defaults
      3. Model runtime_defaults
      4. Deployment runtime_overrides
      5. Deployment extra_args (escape hatch, deep-merged over inherited)

    Runtime-cascade null policy (applied before structural merge):
      - Known nested-dict fields (speculative_config, limit_mm_per_prompt):
        null is an idempotent wipe — removes the block if it exists,
        silently ignored if there is nothing to wipe.
      - All other top-level scalar null values are rejected with ValueError.
      - None values inside nested dicts are preserved as-is; Switchyard
        does not validate backend-specific nested keys.

    Returns:
        A tuple of (merged_runtime_args, provenance) where provenance maps
        each top-level key to the layer name that last set it.

    Raises:
        ValueError: If ``health_timeout_seconds`` appears in any
            runtime-arg layer, or if a scalar ``None``/``null`` appears
            in any override layer.
    """
    _guard_health_timeout(
        model_cfg.defaults, "model.defaults",
    )
    _guard_health_timeout(
        runtime_cfg.defaults, "runtime.defaults",
    )
    _guard_health_timeout(
        model_cfg.runtime_defaults, "model.runtime_defaults",
    )
    _guard_health_timeout(
        deployment.runtime_overrides, "deployment.runtime_overrides",
    )
    _guard_health_timeout(
        deployment.extra_args, "deployment.extra_args",
    )

    # Track provenance: maps each top-level key to the source layer name.
    provenance: dict[str, str] = {}
    merged: dict[str, Any] = {}

    # Top-level fields known to be nested dicts. Null for these is an
    # idempotent wipe (Helm/Kustomize semantics): removes the block if
    # present, silently ignored if not. This avoids rejecting
    # `speculative_config: null` when no lower layer provides one.
    _KNOWN_NESTED_FIELDS = frozenset(
        {"speculative_config", "limit_mm_per_prompt"},
    )

    layers: list[tuple[str, dict[str, Any]]] = [
        ("model.defaults", model_cfg.defaults or {}),
        ("runtime.defaults", runtime_cfg.defaults),
        ("model.runtime_defaults", model_cfg.runtime_defaults),
        ("deployment.runtime_overrides", deployment.runtime_overrides),
    ]
    for layer_name, layer in layers:
        # Pre-process: apply null policy per key before structural merge.
        #   - known nested-dict field + null → wipe (remove from merged)
        #   - other field + null → reject with ValueError
        #   - everything else → pass through to deep_merge
        processed_layer: dict[str, Any] = {}
        for key, value in layer.items():
            if value is None:
                if key in _KNOWN_NESTED_FIELDS:
                    # Idempotent wipe: remove key if present, no-op otherwise.
                    merged.pop(key, None)
                    provenance.pop(key, None)
                    continue  # don't pass None through to deep_merge
                raise ValueError(
                    f"null (None) for field {key!r} is not allowed in "
                    f"{layer_name}; omit the key to inherit from lower "
                    "layers, or set a real value."
                )
            processed_layer[key] = value

        merged = deep_merge(merged, processed_layer)
        for key in processed_layer:
            provenance[key] = layer_name

    # Deep-merge deployment.extra_args into the "extra_args" nested key.
    # This preserves the escape hatch: unknown keys survive VLLMRuntimeConfig
    # validation by landing in its extra_args catch-all field. Any inherited
    # extra_args from lower typed layers are deep-merged with these.
    if deployment.extra_args:
        inherited = merged.get("extra_args")
        # Defensive: upper layer may have accidentally set extra_args to a
        # non-dict value (e.g. true). Treat as empty rather than crashing.
        if not isinstance(inherited, dict):
            inherited = {}
        merged["extra_args"] = deep_merge(inherited, deployment.extra_args)
        provenance["extra_args"] = "deployment.extra_args"

    return merged, provenance


def _guard_health_timeout(
    layer: dict[str, Any] | None,
    source: str,
) -> None:
    """Reject health_timeout_seconds in runtime-arg layers.

    This is a first-class Switchyard field (RuntimeConfig/DeploymentConfig),
    not a runtime flag. Placing it in runtime args would cause it to be
    passed to vLLM CLI incorrectly.
    """
    if layer is not None and "health_timeout_seconds" in layer:
        raise ValueError(
            f"health_timeout_seconds is not a runtime argument and cannot "
            f"be placed in {source}. Use the first-class Switchyard field "
            f"at deployment or runtime level instead."
        )


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
