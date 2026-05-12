# Context Document — VLLMRuntimeConfig Field Audit

**Title**: VLLMRuntimeConfig Field Audit — CLI vs. Switchyard-Only Classification
**ID**: SEP-004-05-CONTEXT-runtime-field-audit
**Date**: 2026-05-11
**Author**: AI Coding Agent (Pi/Qwen)
**PRD**: SEP-004 (TBD — this audit feeds the PRD)

## Purpose

One row per `VLLMRuntimeConfig` field, classified as:

- **(A) CLI flag** — renders to a `vllm serve` CLI argument. Notes the exact rendered form.
- **(B) Switchyard abstraction** — shapes container construction but does NOT render to CLI.
- **(C) Other** — notable edge cases, dual-purpose fields, or fields worth flagging.

This audit becomes the checklist for "Switchyard-only fields never serialize to CLI"
and the input for future tier-promotion decisions.

Source files:

- `switchyard-api/src/switchyard/config/models.py` — `VLLMRuntimeConfig` definition
- `switchyard-api/src/switchyard/adapters/vllm.py` — `_build_cli_args()` and `start()`

## Audit Table

| # | Field | Type | Classification | Detail |
|---|-------|------|----------------|--------|
| 1 | `model` | `str \| None` | (A) CLI | Renders as `--model <value>`. Handled specially in `_build_cli_args`: model source is `runtime.model or runtime.repo`. **Switchyard injects this from store resolution** (`_merge_runtime_args` sets `runtime_args["model"]` = container path). Operator never sets this in config. |
| 2 | `repo` | `str \| None` | (C) Switchyard-routed | Not a vLLM CLI flag — has no `--repo` equivalent. Switchyard routes it into `--model` when `model` is empty: `model_source = runtime.model or runtime.repo` (vllm.py:136). If both are set, `model` wins and `repo` is silently ignored. At the entity level, `ModelSource` has no HF repo field (only `store` + `path`), so `repo` is reachable only via direct `runtime_overrides.repo: <value>`. Functionally dead under the SEP-002 entity model but kept for adapter compatibility.
| 3 | `device` | `Literal["cpu", "cuda"]` | (B) Switchyard | **Skipped in `_build_cli_args`** (explicit `continue`). Controls: (1) device_requests vs. no-GPU Docker config, (2) CPU-specific env vars (`VLLM_CPU_KVCACHE_SPACE`, `VLLM_CPU_NUM_OF_RESERVED_CPU`). Does **not** select the image — image lives on `RuntimeConfig.image` at the runtime entity level (e.g. `runtimes.vllm` vs `runtimes.vllm-cpu` each pin their own image). `device` correlates with image by convention within a runtime variant, not by causation. Must never reach CLI.
| 4 | `max_model_len` | `int \| None` | (A) CLI | `--max-model-len <value>` |
| 5 | `dtype` | `str \| None` | (A) CLI | `--dtype <value>` |
| 6 | `quantization` | `str \| None` | (A) CLI | `--quantization <value>` |
| 7 | `trust_remote_code` | `bool \| None` | (A) CLI | `--trust-remote-code` (flag-only, True; False omitted) |
| 8 | `revision` | `str \| None` | (A) CLI | `--revision <value>` |
| 9 | `code_revision` | `str \| None` | (A) CLI | `--code-revision <value>` |
| 10 | `served_model_name` | `str \| None` | (A) CLI | `--served-model-name <value>` |
| 11 | `chat_template` | `str \| None` | (A) CLI | `--chat-template <value>` |
| 12 | `tokenizer` | `str \| None` | (A) CLI | `--tokenizer <value>` |
| 13 | `tokenizer_mode` | `str \| None` | (A) CLI | `--tokenizer-mode <value>` |
| 14 | `tokenizer_revision` | `str \| None` | (A) CLI | `--tokenizer-revision <value>` |
| 15 | `hf_token` | `str \| None` | (C) Dual | **Renders to BOTH CLI and env var.** `_build_cli_args` → `--hf-token <value>`. `start()` also sets `environment["HF_TOKEN"] = runtime.hf_token`. Redundant but not harmful — vLLM reads from both. |
| 16 | `seed` | `int \| None` | (A) CLI | `--seed <value>` |
| 17 | `reasoning_parser` | `str \| None` | (A) CLI | `--reasoning-parser <value>` |
| 18 | `tool_call_parser` | `str \| None` | (A) CLI | `--tool-call-parser <value>` |
| 19 | `gpu_memory_utilization` | `float \| None` | (A) CLI | `--gpu-memory-utilization <value>` |
| 20 | `kv_cache_dtype` | `str \| None` | (A) CLI | `--kv-cache-dtype <value>` |
| 21 | `block_size` | `int \| None` | (A) CLI | `--block-size <value>` |
| 22 | `enable_prefix_caching` | `bool \| None` | (A) CLI | `--enable-prefix-caching` (flag-only, True; False omitted) |
| 23 | `tensor_parallel_size` | `int \| None` | (A) CLI | `--tensor-parallel-size <value>` |
| 24 | `pipeline_parallel_size` | `int \| None` | (A) CLI | `--pipeline-parallel-size <value>` |
| 25 | `distributed_executor_backend` | `str \| None` | (A) CLI | `--distributed-executor-backend <value>` |
| 26 | `max_num_batched_tokens` | `int \| None` | (A) CLI | `--max-num-batched-tokens <value>` |
| 27 | `max_num_seqs` | `int \| None` | (A) CLI | `--max-num-seqs <value>` |
| 28 | `enable_chunked_prefill` | `bool \| None` | (A) CLI | `--enable-chunked-prefill` (flag-only, True; False omitted) |
| 29 | `stream_interval` | `int \| None` | (A) CLI | `--stream-interval <value>` |
| 30 | `api_key` | `str \| None` | (A) CLI | `--api-key <value>` |
| 31 | `uvicorn_log_level` | `str \| None` | (A) CLI | `--uvicorn-log-level <value>` |
| 32 | `otlp_traces_endpoint` | `str \| None` | (A) CLI | `--otlp-traces-endpoint <value>` |
| 33 | `max_logprobs` | `int \| None` | (A) CLI | `--max-logprobs <value>` |
| 34 | `logprobs_mode` | `str \| None` | (A) CLI | `--logprobs-mode <value>` |
| 35 | `disable_custom_all_reduce` | `bool \| None` | (A) CLI | `--disable-custom-all-reduce` (flag-only, True; False omitted) |
| 36 | `enable_auto_tool_choice` | `bool \| None` | (A) CLI | `--enable-auto-tool-choice` (flag-only, True; False omitted) |
| 37 | `speculative_config` | `dict \| None` | (A) CLI | `--speculative-config <json>`. JSON-serialized in `_build_cli_args`. |
| 38 | `limit_mm_per_prompt` | `dict \| None` | (A) CLI | `--limit-mm-per-prompt <json>`. JSON-serialized in `_build_cli_args`. |
| 39 | `language_model_only` | `bool \| None` | (A) CLI | `--language-model-only` (flag-only, True; False omitted) |
| 40 | `extra_args` | `dict[str, Any]` | (A) CLI (passthrough) | Verbatim key → `--key value`. Booleans: flag-only if True. Dicts in extra_args: `str(value)`, NOT JSON-serialized (unlike named dict fields). |

## Summary

| Classification | Count | Fields |
|---|---|---|
| (A) CLI flag | 35 | All fields except `model` (special), `repo` (C), `device` (B), `hf_token` (C), `extra_args` (passthrough) |
| (A) CLI — special | 1 | `model` — renders as `--model`, resolved from store path by the resolver |
| (A) CLI — passthrough | 1 | `extra_args` — verbatim dict, operator escape hatch |
| (B) Switchyard-only | 1 | `device` — shapes container/device/env config, explicitly skipped in `_build_cli_args` |
| (C) Dual / Switchyard-routed | 2 | `hf_token` — renders to CLI (`--hf-token`) AND container env (`HF_TOKEN`); `repo` — Switchyard-internal field routed into `--model` when `model` is empty, functionally dead at entity level |

## Observations & Risks

1. **`device` is the only Switchyard-only field.** The adapter guards it with an explicit `continue` in `_build_cli_args`. This works today but is fragile — a future developer could remove the skip list item or add `device` to `_FLAG_OVERRIDE`. The `extra: "forbid"` policy on `VLLMRuntimeConfig` (SEP-004) will prevent new unknown fields from leaking in, but the explicit skip for `device` needs a structural guarantee (e.g., a `switchyard_only` field marker or a separate nested model).

2. **`hf_token` dual rendering** is not harmful (vLLM reads from both paths) but is worth documenting explicitly in the adapter so future maintainers don't "fix" one path thinking it's dead code.

3. **`model` and `repo` are operator-invisible.** Both are resolved from store paths or model source config, never set directly in `runtime_overrides`. They are kept on `VLLMRuntimeConfig` for adapter compatibility but are effectively write-only from the resolver. Additionally, `repo` is functionally dead at the entity level: `ModelSource` has no HF repo field (only `store` + `path`), so `repo` is reachable only via `runtime_overrides.repo: <value>`. Consider marking them `internal` or moving them to a resolved-only struct.

4. **`extra_args` serialization mismatch:** Named dict fields (`speculative_config`, `limit_mm_per_prompt`) are JSON-serialized in `_build_cli_args`. Keys inside `extra_args` are passed as `str(value)`, which means a dict value inside `extra_args` would render as `--key "{'a': 1}"` (Python repr with single quotes), not valid JSON. This is a latent bug if an operator puts a dict value inside `extra_args`.

5. **`extra_args` underscore-vs-dash inconsistency.** Named fields go through `_field_to_flag()` which converts `snake_case` to `--kebab-case`. `extra_args` keys are rendered verbatim (`flag = f"--{key}"`), so `extra_args: {disable_log_requests: true}` renders as `--disable_log_requests` — which vLLM won't recognize (vLLM uses kebab-case). Either normalize `extra_args` keys through the same conversion or document that operators must write kebab-case keys there. This pairs naturally with the dict-serialization fix above.

6. **`_FLAG_OVERRIDE` escape hatch is reserved but unused.** `_FLAG_OVERRIDE: dict[str, str] = {}` exists at vllm.py:32 for named fields that need non-conventional CLI rendering. Currently empty because the snake-to-kebab convention covers all 36+ CLI fields. Important context for whoever adds a future field that breaks the pattern.

7. **No `model_config = {"extra": "forbid"}`** on `VLLMRuntimeConfig` today. Top-level typos in YAML runtime args are silently swallowed by Pydantic. SEP-004 must add this.

8. **Cascade merge is shallow (`dict.update`)** in `_merge_runtime_args`. Partial nested dict overrides at deployment level (e.g., overriding only `num_speculative_tokens` inside `speculative_config`) would wipe the entire dict from lower layers. The agreed cascade contract calls for deep merge on nested dicts.

## Forward References (preview API / hardening context)

9. **Container env vars beyond `hf_token`.** The adapter also sets `VLLM_CPU_KVCACHE_SPACE` and `VLLM_CPU_NUM_OF_RESERVED_CPU` (CPU-only, vllm.py:270-271), and the resolver merges `resolved.container_environment` into the env dict. These shape runtime behavior but are not fields on `VLLMRuntimeConfig`. The preview API will need to surface them.

10. **Hardcoded CLI flags not driven by `VLLMRuntimeConfig`.** `--host 0.0.0.0` and `--port <internal_port>` are prepended unconditionally in `start()` (vllm.py:194-197). They're not on the audited model but the preview API must include them in the rendered command.

11. **`tensor_parallel_size` × `placement.accelerator_ids` correlation is unvalidated.** A deployment can declare `tensor_parallel_size: 4` and `placement.accelerator_ids: ["0", "1"]` (2 GPUs) and Switchyard won't notice. Not strictly an audit-row issue, but a hardening candidate. More natural home: SEP-007 (Host Resource Discovery), but flagging here costs nothing.

## Tier Promotion Candidates (from CONTEXT-vllm-config research)

Fields in `SEP-001-04-CONTEXT-vllm-config.md` that are Tier 1/2 in research but NOT yet on `VLLMRuntimeConfig`:

| Field | Tier | Notes |
|---|---|---|
| `kv_cache_memory_bytes` | 2 | Alternative to `gpu_memory_utilization` |
| `prefix_caching_hash_algo` | 3 | Prefix cache hash algorithm |
| `data_parallel_size` | 2 | Model replication for throughput |
| `enable_expert_parallel` | 2 | MoE models |
| `scheduling_policy` | 3 | `fcfs`, `pf` |
| `enable_lora` / LoRA group | 3 | LoRA adapter serving |
| `enable_sleep_mode` | 3 | GPU memory save when idle |
| `mm_processor_kwargs` | 3 | Multimodal processor overrides |
| `enable_mm_embeds` | 3 | Multimodal embedding outputs |
| `performance_mode` | 2 | `throughput` or `latency` |
| `disable_log_stats` / `disable_log_requests` | 2 | Suppress request logging |

None of these are required for SEP-004. They are noted for future tier-promotion when the Web UI (SEP-008) or real-world deployments demand them.
