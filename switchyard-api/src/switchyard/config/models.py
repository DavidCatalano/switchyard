"""Pydantic configuration models for Switchyard.

Legacy SEP-001 models (prefixed ``Legacy``):
  global -> runtime_defaults.{backend} -> models.{name}.runtime

SEP-002 entity models:
  hosts, runtimes, models, deployments

VLLMRuntimeConfig promotes all Tier 1 and Tier 2 vLLM fields as named
Pydantic fields; Tier 3+ use the extra_args catch-all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings

# =====================================================================
# SEP-002 Entity Models
# =====================================================================


def _non_empty_string(value: str) -> str:
    """Validate that a config string is not empty or whitespace."""
    if not value.strip():
        raise ValueError("must not be empty")
    return value


class AcceleratorConfig(BaseModel):
    """A single accelerator (GPU/CPU device) on a host."""

    id: str
    type: Literal["cuda", "cpu", "mps", "rocm"] = "cuda"
    vram_gb: int | None = None

    _validate_id = field_validator("id")(_non_empty_string)


class StoreConfig(BaseModel):
    """Named host/container path mapping for model or cache storage."""

    host_path: str
    container_path: str
    mode: Literal["ro", "rw"] = "ro"

    _validate_paths = field_validator("host_path", "container_path")(
        _non_empty_string
    )


class ContainerDefaults(BaseModel):
    """Host-level container environment and Docker options."""

    environment: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class HostConfig(BaseModel):
    """Machine-specific infrastructure configuration.

    Owns Docker connectivity, port ranges, named stores, accelerator
    inventory, and container defaults for a single host machine.
    """

    docker_host: str | None = None
    docker_network: str = "model-runtime"
    backend_host: str = "localhost"
    backend_scheme: str = "http"
    port_range: list[int] = Field(default_factory=lambda: [8000, 8100])
    accelerators: list[AcceleratorConfig] = Field(default_factory=list)
    stores: dict[str, StoreConfig] = Field(default_factory=dict)
    container_defaults: ContainerDefaults = Field(
        default_factory=ContainerDefaults
    )

    @model_validator(mode="after")
    def _validate_port_range(self) -> HostConfig:
        """port_range must be exactly two ints, start <= end, valid range."""
        pr = self.port_range
        if len(pr) != 2:
            raise ValueError(
                "port_range must have exactly 2 elements [start, end]"
            )
        if pr[0] > pr[1]:
            raise ValueError(
                f"port_range start ({pr[0]}) must be <= end ({pr[1]})"
            )
        if pr[0] < 1 or pr[1] > 65535:
            raise ValueError(
                f"port_range values must be in 1-65535, got [{pr[0]}, {pr[1]}]"
            )
        return self


class RuntimeContainerDefaults(BaseModel):
    """Runtime-level container defaults."""

    internal_port: int = 8000


class RuntimeConfig(BaseModel):
    """Backend engine definition and default launch behavior.

    Owns the container image, CLI flag defaults, and runtime-level
    container defaults for a named runtime (e.g. ``vllm``, ``sglang``).
    """

    backend: str
    image: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    container_defaults: RuntimeContainerDefaults = Field(
        default_factory=RuntimeContainerDefaults
    )

    _validate_backend = field_validator("backend")(_non_empty_string)


class ModelSource(BaseModel):
    """Model source — a reference to a named store + path within it."""

    store: str
    path: str

    _validate_source = field_validator("store", "path")(_non_empty_string)

    @field_validator("path")
    @classmethod
    def _validate_relative_path(cls, value: str) -> str:
        _require_safe_relative_path(value, "ModelSource.path")
        return value


def _require_safe_relative_path(value: str, field_name: str) -> None:
    """Reject absolute paths and path traversal in store-relative paths.

    These paths are within a named store, so they must be relative
    and must not escape the store via ``..`` components.
    """
    if value.startswith("/"):
        raise ValueError(
            f"{field_name} must be a relative path (store-relative), "
            f"not an absolute path: {value!r}"
        )
    if value.startswith("../") or value == "..":
        raise ValueError(
            f"{field_name} must not contain path traversal (..): {value!r}"
        )
    # Also check for .. in the middle of the path
    parts = value.split("/")
    if ".." in parts:
        raise ValueError(
            f"{field_name} must not contain path traversal (..): {value!r}"
        )


class ModelConfig(BaseModel):
    """Logical model source and portable model-family defaults.

    Points to a named ``store`` rather than an absolute host path,
    enabling model configs to be portable across hosts.
    """

    source: ModelSource
    defaults: dict[str, Any] | None = None
    runtime_defaults: dict[str, Any] = Field(default_factory=dict)


class Placement(BaseModel):
    """Hardware placement — accelerator selection for a deployment."""

    accelerator_ids: list[str] = Field(min_length=1)

    @field_validator("accelerator_ids")
    @classmethod
    def _validate_accelerator_ids(cls, value: list[str]) -> list[str]:
        for accelerator_id in value:
            _non_empty_string(accelerator_id)
        return value

    @model_validator(mode="after")
    def _validate_non_empty(self) -> Placement:
        """placement must select at least one accelerator."""
        if not self.accelerator_ids:
            raise ValueError(
                "placement.accelerator_ids must contain at least one ID"
            )
        return self


class StorageOverrides(BaseModel):
    """Storage overrides for a deployment."""

    path: str

    _validate_path = field_validator("path")(_non_empty_string)

    @field_validator("path")
    @classmethod
    def _validate_relative_path(cls, value: str) -> str:
        _require_safe_relative_path(value, "StorageOverrides.path")
        return value


class ContainerOverrides(BaseModel):
    """Container overrides for a deployment."""

    environment: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class DeploymentConfig(BaseModel):
    """Concrete deployment: run this model with this runtime on this host.

    Owns concrete tuning overrides, placement, storage overrides,
    container overrides, and the ``extra_args`` escape hatch.
    """

    model: str
    runtime: str
    host: str
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)
    storage_overrides: StorageOverrides | None = None
    placement: Placement | None = None
    container_overrides: ContainerOverrides | None = None
    extra_args: dict[str, Any] = Field(default_factory=dict)

    _validate_refs = field_validator("model", "runtime", "host")(
        _non_empty_string
    )


class Config(BaseModel):
    """Top-level entity-based YAML configuration (SEP-002).

    Four top-level sections: hosts, runtimes, models, deployments.
    Cross-entity references are validated at load time.
    """

    hosts: dict[str, HostConfig] = Field(default_factory=dict)
    runtimes: dict[str, RuntimeConfig] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    deployments: dict[str, DeploymentConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_references(self) -> Config:
        """Validate all deployment references against known entities."""
        for dep_name, dep in self.deployments.items():
            if dep.model not in self.models:
                raise ValueError(
                    f"deployment {dep_name!r} references unknown model "
                    f"{dep.model!r}"
                )
            if dep.runtime not in self.runtimes:
                raise ValueError(
                    f"deployment {dep_name!r} references unknown runtime "
                    f"{dep.runtime!r}"
                )
            if dep.host not in self.hosts:
                raise ValueError(
                    f"deployment {dep_name!r} references unknown host "
                    f"{dep.host!r}"
                )
            # Validate store reference against host stores
            model_cfg = self.models[dep.model]
            host_cfg = self.hosts[dep.host]
            store_name = model_cfg.source.store
            if store_name not in host_cfg.stores:
                raise ValueError(
                    f"deployment {dep_name!r}: model {dep.model!r} references "
                    f"store {store_name!r} not found on host {dep.host!r}"
                )
        return self


@dataclass
class ResolvedDeployment:
    """Fully resolved deployment configuration.

    Produced by resolving a deployment's model, runtime, host, stores,
    cascaded defaults, and overrides into a single launch-ready object.

    This is a plain dataclass (not Pydantic) to avoid circular dependency
    with the Pydantic entity models during resolution.
    """

    # Identity
    deployment_name: str
    model_name: str
    runtime_name: str
    backend: str
    host_name: str

    # Host reachability
    backend_host: str
    backend_scheme: str
    port_range: list[int]

    # Container
    image: str
    internal_port: int

    # Store resolution
    model_host_path: str
    model_container_path: str

    # Hardware placement
    accelerator_ids: list[str]

    # Docker
    docker_host: str | None
    docker_network: str

    # Cascade-merged runtime config
    runtime_args: dict[str, Any]

    # Cascade-merged container config
    container_environment: dict[str, str]
    container_options: dict[str, Any]

    # Model defaults (portable)
    model_defaults: dict[str, Any] | None


class AppSettings(BaseSettings):
    """Process-local bootstrap settings from .env.

    Answers: "how does this Switchyard process start on this machine?"
    Owned by .env, not config.yaml.

    Fields from both SEP-001 (legacy loader) and SEP-002 (entity loader)
    are present here for backward compatibility during transition.
    """

    model_config = {
        "env_prefix": "SWITCHYARD_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    config_path: str | None = None
    log_level: str | None = None
    api_host: str | None = None
    api_port: int | None = None
    active_host: str | None = None
    docker_host: str | None = None

    # Legacy SEP-001 fields (to be removed in Phase 3)
    base_port: int | None = None
    docker_network: str | None = None
    backend_host: str | None = None
    backend_scheme: str | None = None
    health_interval_seconds: float | None = None
    health_timeout_seconds: float | None = None


# =====================================================================
# Legacy SEP-001 Models (to be removed in Phase 3)
# =====================================================================


class LegacyGlobalConfig(BaseModel):
    """Switchyard-wide settings (host, network, ports, log level)."""

    docker_network: str = "model-runtime"
    base_port: int = 8000
    log_level: str = "info"
    backend_host: str = "localhost"
    backend_scheme: str = "http"


class LegacyResourcesConfig(BaseModel):
    """Per-model resource constraints."""

    memory: str | None = None


class LegacyControlConfig(BaseModel):
    """Per-model control settings."""

    auto_start: bool = False


class VLLMRuntimeConfig(BaseModel):
    """Typed vLLM runtime configuration.

    Promotes all Tier 1 and Tier 2 fields as named Pydantic fields.
    Tier 3+ use the ``extra_args`` catch-all mapping.

    Fields are grouped by vLLM internal argument categories for readability.
    No runtime branching on tier — all named fields translate to CLI flags
    identically via the adapter.
    """

    # --- Model & Tokenizer (ModelConfig) ---
    # Essential model identity: local path OR HuggingFace repo ID.
    # Enforced at the LegacyModelConfig level (exactly one required).
    model: str | None = None
    repo: str | None = None

    # --- Device ---
    # "cuda" (default) → GPU image, device_requests, gpu_memory_utilization
    # "cpu" → CPU image, no GPU devices, VLLM_CPU_* env vars
    device: Literal["cpu", "cuda"] = "cuda"

    # Core model parameters
    max_model_len: int | None = None
    dtype: str | None = None
    quantization: str | None = None
    trust_remote_code: bool | None = None
    revision: str | None = None
    code_revision: str | None = None
    served_model_name: str | None = None
    chat_template: str | None = None

    # Tokenizer settings
    tokenizer: str | None = None
    tokenizer_mode: str | None = None
    tokenizer_revision: str | None = None
    hf_token: str | None = None
    seed: int | None = None

    # Reasoning & tool-calling parsers (Tier 1)
    reasoning_parser: str | None = None
    tool_call_parser: str | None = None

    # --- GPU Memory & KV Cache (CacheConfig) ---
    gpu_memory_utilization: float | None = None
    kv_cache_dtype: str | None = None
    block_size: int | None = None
    enable_prefix_caching: bool | None = None

    # --- Parallelism (ParallelConfig) ---
    tensor_parallel_size: int | None = None
    pipeline_parallel_size: int | None = None
    distributed_executor_backend: str | None = None

    # --- Scheduling (SchedulerConfig) ---
    max_num_batched_tokens: int | None = None
    max_num_seqs: int | None = None
    enable_chunked_prefill: bool | None = None
    stream_interval: int | None = None

    # --- Networking & API Server (FrontendArgs) ---
    api_key: str | None = None
    uvicorn_log_level: str | None = None

    # --- Observability (ObservabilityConfig) ---
    otlp_traces_endpoint: str | None = None

    # --- Generation defaults ---
    max_logprobs: int | None = None
    logprobs_mode: str | None = None

    # --- System & Performance ---
    disable_custom_all_reduce: bool | None = None

    # --- Reasoning & Tool Use ---
    enable_auto_tool_choice: bool | None = None

    # --- Speculative Decoding ---
    speculative_config: dict[str, Any] | None = None

    # --- Multimodal (MultiModalConfig) ---
    limit_mm_per_prompt: dict[str, Any] | None = None
    language_model_only: bool | None = None

    # --- Catch-all for arbitrary backend flags ---
    # Tier 3+ or any vLLM flag not explicitly modeled above.
    # Passed verbatim to CLI as --flag value pairs.
    extra_args: dict[str, Any] = Field(default_factory=dict)


class LegacyModelConfig(BaseModel):
    """Per-model configuration entry (SEP-001)."""

    backend: str
    image: str
    control: LegacyControlConfig = Field(default_factory=LegacyControlConfig)
    resources: LegacyResourcesConfig = Field(
        default_factory=LegacyResourcesConfig
    )
    runtime: VLLMRuntimeConfig = Field(default_factory=VLLMRuntimeConfig)

    @model_validator(mode="after")
    def _validate_model_source(self) -> LegacyModelConfig:
        """Exactly one of model or repo must be set in runtime config."""
        has_model = self.runtime.model is not None
        has_repo = self.runtime.repo is not None
        if not (has_model ^ has_repo):
            raise ValueError(
                "exactly one of 'model' (local path) or 'repo' (HuggingFace) "
                "must be set in runtime config"
            )
        return self


class LegacyRuntimeDefaults(BaseModel):
    """Per-engine defaults that cascade to all models of a given backend (SEP-001).

    Uses ``extra = "allow"`` so any key (backend name) becomes an attribute.
    Each value is a ``dict[str, Any]`` merged with per-model runtime config
    by the loader.

    Example:
        runtime_defaults:
            vllm:
                gpu_memory_utilization: 0.92
            koboldcpp:
                n_gpu_layers: -1
    """

    model_config = {"extra": "allow"}

    def get_backend_defaults(self, backend: str) -> dict[str, Any]:
        """Get defaults dict for a given backend name."""
        extra = self.__pydantic_extra__ or {}
        return dict(extra.get(backend) or {})


class LegacyConfig(BaseModel):
    """Top-level YAML configuration (SEP-001).

    Three-level cascade:
      global -> runtime_defaults.{backend} -> models.{name}.runtime
    """

    global_config: LegacyGlobalConfig = Field(
        default_factory=LegacyGlobalConfig, alias="global"
    )
    runtime_defaults: LegacyRuntimeDefaults = Field(
        default_factory=LegacyRuntimeDefaults
    )
    models: dict[str, LegacyModelConfig] = Field(default_factory=dict)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Validation happens automatically via @model_validator on each
        # LegacyModelConfig. No explicit loop needed.


# Backward-compatibility aliases (remove in Phase 3)
GlobalConfig = LegacyGlobalConfig
ResourcesConfig = LegacyResourcesConfig
ControlConfig = LegacyControlConfig
RuntimeDefaults = LegacyRuntimeDefaults
