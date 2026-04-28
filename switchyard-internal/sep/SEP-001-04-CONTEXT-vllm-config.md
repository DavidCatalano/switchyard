# Context Document — vLLM Launch Parameters & Configuration

**Title**: vLLM Server Configuration — Launch Parameters and Tiered Exposure for Switchyard
**ID**: SEP-001-04-CONTEXT-vllm-config
**Date**: 2026-04-28
**Author**: AI Coding Agent (Claude)
**PRD**: SEP-001-01-PRD-mvp-control-plane.md

---

## External Research

> Research sourced from vLLM official documentation via Context7 MCP:
> - `/vllm-project/vllm` — vLLM source repository (10,056 snippets, benchmark 78.94)
> - `/websites/vllm_ai_en` — docs.vllm.ai (49,816 snippets, benchmark 40.90)
> - Real-world deployment references: `reference-then-delete/vLLM/docker-compose.yml`, `compose-openai.yml`, `compose-qwen2.5-vl.yml`

This document catalogs all vLLM `serve` command-line arguments relevant to Switchyard's `VLLMAdapter`, organized by vLLM's internal argument group categories, and concludes with recommendations for which parameters should be exposed in Switchyard's YAML configuration.

---

### 1. Networking & API Server (`FrontendArgs`)

| Parameter | Why it matters for Switchyard |
|---|---|
| `--host` | We bind to `0.0.0.0` so our control plane can reach it inside Docker |
| `--port` | Internally fixed (allocated by Switchyard), but exposed to user |
| `--api-key` | Per-model API key auth at the backend level |
| `--uvicorn-log-level` | Backend-level log verbosity |
| `--disable-uvicorn-access-log` | Reduce noise from `/health` polling |
| `--disable-access-log-for-endpoints` | Exclude `/health`, `/metrics` from logs |
| `--allowed-origins` / `--allowed-methods` / `--allowed-headers` | CORS configuration |
| `--ssl-keyfile` / `--ssl-certfile` / `--ssl-ca-certs` | TLS termination at backend level |
| `--root-path` | Needed if behind a path-based proxy |
| `--enable-request-id-headers` | Useful for request tracing |

### 2. Model & Tokenizer (`ModelConfig`)

| Parameter | Why |
|---|---|
| `--model` (positional) | **Essential** — the model to serve |
| `--served-model-name` | Decouple served name from HF repo ID |
| `--tokenizer` / `--tokenizer-mode` | Custom tokenizer path or mode |
| `--tokenizer-revision` | Pin tokenizer version |
| `--trust-remote-code` | Required for non-standard models |
| `--revision` / `--code-revision` | Pin model weights/code version |
| `--dtype` | `auto`, `float16`, `bfloat16`, `float32` |
| `--seed` | Reproducibility |
| `--quantization` (`-q`) | `awq`, `gptq`, `compressed-tensors`, `fp8`, `bitsandbytes`, etc. |
| `--max-model-len` | Context length limit |
| `--max-logprobs` / `--logprobs-mode` | Logprobs support |
| `--hf-token` | Auth for gated models |
| `--chat-template` | Custom chat template path |
| `--enforce-eager` | Disable CUDA graphs for debugging |
| `--disable-sliding-window` | Override sliding window behavior |
| `--config-format` | Config format selection |
| `--generation-config` / `--override-generation-config` | Default generation parameters |
| `--pooler-config` | For embedding/pooling models |

### 3. GPU Memory & KV Cache (`CacheConfig`)

| Parameter | Why |
|---|---|
| `--gpu-memory-utilization` | **Essential** — default 0.92, controls KV cache fraction |
| `--kv-cache-memory-bytes` | Precise byte-level override of above |
| `--block-size` | KV cache block size in tokens (16, 32, 64) |
| `--kv-cache-dtype` | Auto-quantize KV cache (`fp8`, `fp4`, etc.) |
| `--enable-prefix-caching` | Reuse KV cache across requests with common prefixes |
| `--prefix-caching-hash-algo` | Hash algorithm for prefix cache |
| `--kv-cache-dtype-skip-layers` | Skip certain layers from KV cache dtype override |
| `--kv-offloading-size` / `--kv-offloading-backend` | Offload KV cache to CPU/HBM |

### 4. Parallelism (`ParallelConfig`)

| Parameter | Why |
|---|---|
| `--tensor-parallel-size` (`-tp`) | **Essential** — multi-GPU splitting |
| `--pipeline-parallel-size` (`-pp`) | Pipeline parallelism across GPUs |
| `--data-parallel-size` (`-dp`) | Replicate model for throughput |
| `--enable-expert-parallel` (`-ep`) | For MoE models |
| `--decode-context-parallel-size` (`-dcp`) | Decode context parallelism |
| `--prefill-context-parallel-size` (`-pcp`) | Prefill context parallelism |
| `--distributed-executor-backend` | `mp` or `ray` |
| `--master-addr` / `--master-port` | Multi-node coordination |
| `--nnodes` / `--node-rank` | Multi-node setup |

### 5. Scheduling (`SchedulerConfig`)

| Parameter | Why |
|---|---|
| `--max-num-batched-tokens` | Max tokens per iteration (impacts TTFT) |
| `--max-num-seqs` | Max concurrent sequences (impacts throughput) |
| `--enable-chunked-prefill` | Chunk long prompts to reduce TTFT spikes |
| `--max-num-partial-prefills` | Limit concurrent partial prefills |
| `--scheduling-policy` | `fcfs`, `pf`, etc. |
| `--stream-interval` | Streaming token output interval |
| `--async-scheduling` | Decouple scheduling from execution |
| `--long-prefill-token-threshold` | Threshold for "long" prefill handling |

### 6. LoRA (`LoRAConfig`)

| Parameter | Why |
|---|---|
| `--enable-lora` | Enable LoRA adapter serving |
| `--max-loras` | Max simultaneous LoRAs per batch |
| `--max-lora-rank` | Max rank of LoRA adapters |
| `--lora-dtype` | LoRA weight dtype |
| `--lora-target-modules` | Target modules for LoRA |

### 7. Speculative Decoding

| Parameter | Why |
|---|---|
| `--speculative-config` | JSON config with `method`, `model`, `num_speculative_tokens`, `parallel_drafting`, etc. |

### 8. Observability (`ObservabilityConfig`)

| Parameter | Why |
|---|---|
| `--otlp-traces-endpoint` | OpenTelemetry tracing endpoint |
| `--collect-detailed-traces` | Collect detailed per-module traces |
| `--kv-cache-metrics` | Enable KV cache usage metrics |
| `--enable-mfu-metrics` | Model FLOPs utilization metrics |
| `--cudagraph-metrics` | CUDA graph capture metrics |

### 9. System & Performance

| Parameter | Why |
|---|---|
| `--config` | YAML config file (alternative to CLI args) |
| `--performance-mode` | `throughput` or `latency` mode |
| `--disable-log-stats` / `--disable-log-requests` | Suppress request-level logging |
| `--enable-sleep-mode` | Save GPU memory when idle |
| `--disable-custom-all-reduce` | Disables vLLM's custom NCCL all-reduce; may improve TP stability on some hardware |

### 10. Reasoning & Tool Use (Model-Specific Parsers)

| Parameter | Why |
|---|---|
| `--reasoning-parser` | Model-specific reasoning (CoT/thinking) parser; e.g. `qwen3`, `deepseek_r1`. Required for reasoning models to properly parse and surface thinking blocks. |
| `--tool-call-parser` | Model-specific tool/function-call parser; e.g. `qwen3_coder`, `glm4`. Needed for models with custom tool-calling formats. |
| `--enable-auto-tool-choice` | Enables automatic tool/function-call routing at the model level. |

### 11. Multimodal (`MultiModalConfig`)

| Parameter | Why |
|---|---|
| `--limit-mm-per-prompt` | Dict mapping modality to count (and optional size hints: width, height, num_frames). Controls memory by bounding how many images/videos/audio per prompt. Accepts simple form (`{"image": 4}`) or rich form (`{"image": {"count": 5, "width": 512, "height": 512}}`). Supports `image`, `video`, and `audio` modalities. Setting a modality to 0 disables it. |
| `--language-model-only` | Boolean. Disables all multimodal inputs by setting all modality limits to 0. Allows running a hybrid model (Qwen 3.5, Llama 4) in text-only mode, freeing GPU memory by skipping multimodal module loading. |
| `--enable-mm-embeds` | Boolean. Enables multimodal embedding outputs for models that support it. |
| `--mm-processor-kwargs` | Dict forwarded to the model's transformers processor. Model-specific overrides: `max_pixels`, `min_pixels`, `fps` (Qwen-VL series), `num_crops` (Phi-3-Vision), etc. Controls image/video resolution and processing behavior. |

---

## Real-World Validation

The parameters above were validated against an actual home AI server deployment (`reference-then-delete/vLLM/docker-compose.yml`) running Qwen3.6-27B-FP8 on dual GPUs. Key observations:

- `gpu_memory_utilization` was set to **0.97** (vs 0.92 default) — actively squeezed for maximum KV cache.
- `max_model_len` was **100000** with `max_num_seqs` at **4** — a deliberate tradeoff of context length vs concurrency that is actively tuned.
- `kv_cache_dtype: fp8_e4m3` was required to make 100K context feasible — this is not optional, it's the enabler.
- `speculative_config` was already in production using `qwen3_next_mtp` with 2 speculative tokens — graduates from experimental to commonly useful.
- `reasoning_parser`, `tool_call_parser`, and `enable_auto_tool_choice` are new categories for reasoning models — not in our original research but actively deployed.
- `--model` referenced a **local filesystem path** (`/data/LLM/...`), not a HuggingFace repo ID — the config schema must support both `repo:` and `model:` (local path).
- `disable_custom_all_reduce` was set, suggesting TP stability tuning is needed on some hardware configurations.
- `compose-qwen2.5-vl.yml` revealed an entirely new parameter group: `MultiModalConfig`. The `limit_mm_per_prompt` flag is a critical memory safety control for VLMs, and `language_model_only` allows running hybrid models in text-only mode. `mm_processor_kwargs` provides model-specific vision processor tuning.
- `compose-openai.yml` confirmed no new flags beyond what was already cataloged; it reinforced that `enable_chunked_prefill` may now be a vLLM default and `VLLM_ATTENTION_BACKEND=FLASH_ATTN` may be vestigial.

---

## Recommendations

Not all of these parameters belong as first-class fields in the YAML `runtime` section. They are categorized across **three config levels** that cascade, with the lowest level prevailing:

```
global                    ← Switchyard-wide (host, network, ports, log level)
└── vllm_defaults         ← Runtime-engine defaults (apply to all models)
    └── models.<name>.runtime  ← Per-model overrides (highest priority)
```

Each parameter also has a **tier** indicating how explicitly it should be surfaced in the schema.

### Global — Switchyard-Wide Settings

These are system/host properties that apply to the control plane as a whole. No per-model override is needed:

| Parameter | Tier | Rationale |
|---|---|---|
| `base_port` | 1 | Already in spec; controls port allocation pool |
| `docker_network` | 1 | Already in spec; all backends share it |
| `log_level` | 1 | Already in spec; global control plane verbosity |

### vLLM Defaults — Runtime-Engine Level

These are vLLM-level settings that represent sensible defaults for all models on a given host. They go in a `vllm_defaults:` block (or similar) and cascade down to every model unless overridden:

| Parameter | Tier | Rationale |
|---|---|---|
| `gpu_memory_utilization` | 1 | Host GPU memory is a shared pool; a single default makes sense |
| `tensor_parallel_size` | 1 | Determined by GPU count on the host, not by the model |
| `dtype` | 1 | Host GPU capability (bfloat16 support, etc.) |
| `kv_cache_dtype` | 1 | GPU memory policy; required enabler for long context (e.g. fp8_e4m3 for 100K context). Graduated from Tier 2 based on real-world deployment. |
| `pipeline_parallel_size` | 2 | Hardware topology; rarely varies per model |
| `distributed_executor_backend` | 2 | `mp` vs `ray` is an infrastructure decision |
| `block_size` | 2 | KV cache block size is a memory efficiency concern, not model-specific |
| `uvicorn_log_level` | 2 | Backend-level log verbosity, useful default |
| `enable_prefix_caching` | 2 | Reasonable to default on/off for the whole fleet |
| `otlp_traces_endpoint` | 2 | Single tracing collector for the whole system |
| `kv_cache_metrics` / `enable_mfu_metrics` | 3 | System-wide observability policy |

### Per-Model — Model-Specific Settings

These live in each model's `runtime:` block and must be unique per deployment:

| Parameter | Tier | Rationale |
|---|---|---|
| `model` (local path) or `repo` (HF ID) | 1 | **Essential** — which model to serve. Must support both local filesystem paths (`/data/LLM/...`) and HuggingFace repo IDs. |
| `image` | 1 | Container image for the backend |
| `max_model_len` | 1 | Each model has different context limits; actively tuned (e.g. 100K vs 32K). |
| `quantization` | 1 | Depends on available quantized weights for that model |
| `trust_remote_code` | 1 | Model-specific requirement |
| `chat_template` | 1 | Model-specific |
| `revision` / `code_revision` | 1 | Pin model weights/code version |
| `served_model_name` | 1 | Decouples routing name from repo ID |
| `reasoning_parser` | 1 | Required for reasoning models (Qwen3, DeepSeek R1) to parse thinking blocks. New category from real-world deployment. |
| `tool_call_parser` | 1 | Required for models with custom tool-calling formats. New category from real-world deployment. |
| `limit_mm_per_prompt` | 1 | Critical memory safety control for VLMs — prevents OOM by bounding images/videos/audio per prompt. Accepts dict with simple count or rich form (count + size hints). Tier 1 for VLMs, N/A for text-only models. |
| `enable_auto_tool_choice` | 2 | Enables automatic tool/function-call routing. New category from real-world deployment. |
| `max_num_seqs` | 2 | Actively tuned per model to trade context length for concurrency (e.g. 4 seqs @ 100K context vs 8 @ 65K). |
| `max_num_batched_tokens` | 2 | Tuned per model size and expected load |
| `enable_chunked_prefill` | 2 | May differ per model |
| `speculative_config` | 2 | Graduated from Tier 3 — already in production use with qwen3_next_mtp for MTP-based speculative decoding. |
| `api_key` | 2 | Per-backend auth |
| `hf_token` | 2 | Per-model gated access |
| `seed` | 2 | Reproducibility per model |
| `max_logprobs` / `logprobs_mode` | 2 | Client request feature |
| `generation_config` / `override_generation_config` | 2 | Default generation parameters |
| `disable_custom_all_reduce` | 2 | TP stability tuning on certain hardware configurations. |
| LoRA config (`enable_lora`, `max_loras`, etc.) | 3 | Advanced per-model adapter setup |
| `enable_sleep_mode` | 3 | Some models you want always hot |
| `language_model_only` | 2 | Run hybrid models (Qwen 3.5, Llama 4) in text-only mode; frees GPU memory by skipping multimodal module loading. |
| `mm_processor_kwargs` | 3 | Model-specific vision processor overrides (`max_pixels`, `fps`, `num_crops`). Recognized key name rather than buried in `extra_args`. |
| `enable_mm_embeds` | 3 | Multimodal embedding outputs; niche use case. |
| `extra_args` | 3 | **Catch-all** — arbitrary vLLM CLI flags as a mapping |

### Tier Definitions

| Tier | Meaning | Exposure |
|---|---|---|
| **Tier 1** | Core — essential for any deployment | First-class, documented Pydantic fields |
| **Tier 2** | Common — frequently tuned in production | First-class, documented Pydantic fields with sensible defaults |
| **Tier 3** | Advanced — niche or experimental | Catch-all `extra_args` mapping that the adapter converts into `--flag value` CLI arguments |

### Cascade Semantics

A model's effective config is resolved by merging from top to bottom:

1. **vllm_defaults** provides the base for every model
2. **Per-model runtime** overrides any keys it specifies
3. **extra_args** on either level supplies arbitrary flags not explicitly modeled

A model that defines only `repo` and `image` inherits everything else from `vllm_defaults`. A model that overrides `tensor_parallel_size` gets its own value; all other models keep the default.

### Example YAML Structure

```yaml
global:
  base_port: 8000
  docker_network: model-runtime
  log_level: info

vllm_defaults:
  gpu_memory_utilization: 0.92
  tensor_parallel_size: 2
  dtype: auto
  kv_cache_dtype: auto
  enable_prefix_caching: true
  block_size: 16

models:
  qwen-32b:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      repo: Qwen/Qwen2-32B-Instruct
      max_model_len: 4096
      quantization: awq
      # inherits gpu_memory_utilization, tensor_parallel_size, dtype, etc.

  llama-70b:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      repo: meta-llama/Llama-3.1-70B-Instruct
      tensor_parallel_size: 4    # override default
      # inherits everything else from vllm_defaults

  qwen3-reasoning:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      model: /data/LLM/oobabooga/models/Qwen3.6-27B-FP8
      max_model_len: 100000
      max_num_seqs: 4
      kv_cache_dtype: fp8_e4m3        # required for 100K context
      reasoning_parser: qwen3
      enable_auto_tool_choice: true
      tool_call_parser: qwen3_coder
      speculative_config:
        method: qwen3_next_mtp
        num_speculative_tokens: 2
      # inherits gpu_memory_utilization, tensor_parallel_size, dtype

  qwen2.5-vl:
    backend: vllm
    image: vllm/vllm-openai:latest
    runtime:
      model: /data/LLM/oobabooga/models/Qwen2.5-VL-32B-Instruct-AWQ
      max_model_len: 32768
      limit_mm_per_prompt:
        image: 4
      trust_remote_code: true
      enable_auto_tool_choice: true
      tool_call_parser: hermes
      # inherits gpu_memory_utilization, tensor_parallel_size, dtype
```

This approach gives the YAML config opinionated, well-documented fields for common cases, while `extra_args` lets users pass arbitrary vLLM arguments for advanced or experimental needs without requiring Switchyard to know about every flag.

---
