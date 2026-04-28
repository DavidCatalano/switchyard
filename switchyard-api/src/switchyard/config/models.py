"""Pydantic configuration models for Switchyard YAML configuration.

Defines typed models for the three-level config cascade:
  global -> runtime_defaults.{backend} -> models.{name}.runtime

VLLMRuntimeConfig promotes all Tier 1 and Tier 2 vLLM fields as named
Pydantic fields; Tier 3+ use the extra_args catch-all.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class GlobalConfig(BaseModel):
    """Switchyard-wide settings (host, network, ports, log level)."""

    docker_network: str = "model-runtime"
    base_port: int = 8000
    log_level: str = "info"


class ResourcesConfig(BaseModel):
    """Per-model resource constraints."""

    memory: str | None = None


class ControlConfig(BaseModel):
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
    # Enforced at the ModelConfig level (exactly one required).
    model: str | None = None
    repo: str | None = None

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


class ModelConfig(BaseModel):
    """Per-model configuration entry."""

    backend: str
    image: str
    control: ControlConfig = Field(default_factory=ControlConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    runtime: VLLMRuntimeConfig = Field(default_factory=VLLMRuntimeConfig)

    @model_validator(mode="after")
    def _validate_model_source(self) -> ModelConfig:
        """Exactly one of model or repo must be set in runtime config."""
        has_model = self.runtime.model is not None
        has_repo = self.runtime.repo is not None
        if not (has_model ^ has_repo):
            raise ValueError(
                "exactly one of 'model' (local path) or 'repo' (HuggingFace) "
                "must be set in runtime config"
            )
        return self


class RuntimeDefaults(BaseModel):
    """Per-engine defaults that cascade to all models of a given backend.

    Uses ``extra = "allow"`` so any key (backend name) becomes an attribute.
    Each value is a ``dict[str, Any]`` merged with per-model runtime config
    by the loader (T1.3).

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


class Config(BaseModel):
    """Top-level YAML configuration.

    Three-level cascade:
      global -> runtime_defaults.{backend} -> models.{name}.runtime
    """

    global_config: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    runtime_defaults: RuntimeDefaults = Field(default_factory=RuntimeDefaults)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
