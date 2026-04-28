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
| `pipeline_parallel_size` | 2 | Hardware topology; rarely varies per model |
| `distributed_executor_backend` | 2 | `mp` vs `ray` is an infrastructure decision |
| `block_size` | 2 | KV cache block size is a memory efficiency concern, not model-specific |
| `kv_cache_dtype` | 2 | GPU memory policy |
| `uvicorn_log_level` | 2 | Backend-level log verbosity, useful default |
| `enable_prefix_caching` | 2 | Reasonable to default on/off for the whole fleet |
| `otlp_traces_endpoint` | 2 | Single tracing collector for the whole system |
| `kv_cache_metrics` / `enable_mfu_metrics` | 3 | System-wide observability policy |

### Per-Model — Model-Specific Settings

These live in each model's `runtime:` block and must be unique per deployment:

| Parameter | Tier | Rationale |
|---|---|---|
| `repo` (model identifier) | 1 | **Essential** — which model to serve |
| `image` | 1 | Container image for the backend |
| `max_model_len` | 1 | Each model has different context limits |
| `quantization` | 1 | Depends on available quantized weights for that model |
| `trust_remote_code` | 1 | Model-specific requirement |
| `chat_template` | 1 | Model-specific |
| `revision` / `code_revision` | 1 | Pin model weights/code version |
| `served_model_name` | 1 | Decouples routing name from repo ID |
| `max_num_batched_tokens` / `max_num_seqs` | 2 | Tuned per model size and expected load |
| `enable_chunked_prefill` | 2 | May differ per model |
| `api_key` | 2 | Per-backend auth |
| `hf_token` | 2 | Per-model gated access |
| `seed` | 2 | Reproducibility per model |
| `max_logprobs` / `logprobs_mode` | 2 | Client request feature |
| `generation_config` / `override_generation_config` | 2 | Default generation parameters |
| `speculative_config` | 3 | Depends on draft model availability |
| LoRA config (`enable_lora`, `max_loras`, etc.) | 3 | Advanced per-model adapter setup |
| `enable_sleep_mode` | 3 | Some models you want always hot |
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
```

This approach gives the YAML config opinionated, well-documented fields for common cases, while `extra_args` lets users pass arbitrary vLLM arguments for advanced or experimental needs without requiring Switchyard to know about every flag.

---
