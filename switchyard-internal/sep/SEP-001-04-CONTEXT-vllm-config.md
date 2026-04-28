# Context Document — vLLM Launch Parameters & Configuration

**Title**: vLLM Server Configuration — Launch Parameters and Tiered Exposure for Switchyard
**ID**: SEP-001-04-CONTEXT-vllm-config-new
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

Not all of these parameters belong as first-class fields in the YAML `runtime` section. They are categorized into three tiers:

### Tier 1 — Always Exposed (core to Switchyard's orchestration role)

These parameters map directly to the `RuntimeConfig` Pydantic model and are always offered in YAML:

- `model` / `image` (already defined in `spec.md`)
- `tensor-parallel-size`
- `gpu-memory-utilization`
- `max-model-len`
- `dtype`
- `quantization`
- `trust-remote-code`
- `revision`
- `chat-template`

### Tier 2 — Commonly Useful (exposed via YAML, with sensible defaults)

These parameters are frequently tuned in production and should be offered as optional YAML fields:

- `max-num-batched-tokens` / `max-num-seqs`
- `enable-chunked-prefill`
- `enable-prefix-caching`
- `api-key`
- `seed`
- `served-model-name`
- `hf-token`
- `block-size`
- `uvicorn-log-level`

### Tier 3 — Advanced (pass-through `extra_args` dict)

Everything beyond Tier 1 and Tier 2 is passed through a catch-all mapping that the adapter converts into CLI flags (e.g., `extra_args["speculative_config"]` becomes `--speculative-config '...'`). This covers:

- Speculative decoding (`--speculative-config`)
- LoRA configuration (`--enable-lora`, `--max-loras`, etc.)
- Pipeline parallelism, data parallelism, context parallelism
- Observability (OTLP traces, KV cache metrics, MFU metrics)
- SSL/TLS termination at backend level
- Root path and middleware
- System tuning (sleep mode, performance mode, log suppression)

This approach gives the YAML config opinionated, well-documented fields for common cases, while the catch-all `extra_args` key lets users pass arbitrary vLLM arguments for advanced or experimental needs without requiring Switchyard to know about every flag.

---
