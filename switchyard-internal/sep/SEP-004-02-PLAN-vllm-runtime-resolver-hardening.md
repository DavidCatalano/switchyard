# Project Plan (PLAN)

**Title**: vLLM Runtime Resolver Hardening
**ID**: SEP-004-02-PLAN-vllm-runtime-resolver-hardening
**Status**: Complete
**Date**: 2026-05-11
**PRD**: `SEP-004-01-PRD-vllm-runtime-resolver-hardening.md`

---

## Implementation Approach

Execute the PRD's three phases in sequence: schema hardening (tighten `VLLMRuntimeConfig` and adapter CLI building), cascade contract (deep merge for nested dicts), preview API (structured JSON container spec endpoint). Each phase is self-contained and testable before the next begins.

Key existing code to work from:
- `switchyard-api/src/switchyard/config/models.py` — `VLLMRuntimeConfig` (line 398+), entity models
- `switchyard-api/src/switchyard/config/loader.py` — `_merge_runtime_args` (shallow merge), `resolve_deployment`
- `switchyard-api/src/switchyard/adapters/vllm.py` — `_build_cli_args` (line 118+), `start()` (line 171+) — **`start()` owns full launch construction** (env, ports, labels, volumes, device requests), not just CLI args
- `switchyard-api/src/switchyard/app.py` — `_mask_sensitive_args` (line ~425) — **dict-only, cannot redact flat CLI lists**
- `switchyard-api/tests/test_vllm_resolved_command.py` — existing resolved→CLI test pattern
- `switchyard-api/tests/live/` — live smoke test fixtures (`live_docker`, `live_backend_url`)

---

## Task Breakdown

### Phase 1: Schema Hardening

**Goal**: Enforce the two-bucket strict contract on `VLLMRuntimeConfig` and fix adapter serialization bugs.

#### Tasks
- [x] **A1**: Add `model_config = {"extra": "forbid"}` to `VLLMRuntimeConfig` (models.py).
- [x] **A1.5**: Add explicit validation in `resolve_deployment()` (loader.py). After merging runtime args, call `VLLMRuntimeConfig.model_validate(merged_args)` before constructing `ResolvedDeployment`. Typos now surface at resolution time, before any Docker call.
  - Test: `test_config_resolution.py` — unknown key in any cascade layer raises `ValidationError` at resolve time
- [x] **A2**: Replace hardcoded skip tuple in `_build_cli_args` (vllm.py:143) with structural skip derived from Pydantic field metadata. Mark `device` with `Field(json_schema_extra={"switchyard_only": True})` and derive skip set via `VLLMRuntimeConfig.model_fields[<name>].json_schema_extra`.
  - Confirm Pydantic v2 access path for `json_schema_extra` during implementation (forward note from consultant).
  - Test: `test_vllm_resolved_command.py` — `device` never appears in CLI args; adapter rejects no fields by string matching
- [x] **A3**: Fix `extra_args` dict serialization. Dict values inside `extra_args` must use `json.dumps()` (matching named dict fields at vllm.py:154-156), not `str(value)`.
  - Test: `test_vllm_resolved_command.py` — `extra_args: {some_config: {"a": 1}}` renders as valid JSON
- [x] **A4**: Normalize `extra_args` keys through `_field_to_flag` (snake-case → kebab-case) so they match vLLM's CLI convention. Currently `extra_args` keys render verbatim (vllm.py:162).
  - Test: `test_vllm_resolved_command.py` — `extra_args: {disable_log_requests: true}` renders as `--disable-log-requests`
- [x] **A5**: Verify that typos in each cascade layer (`runtime.defaults`, `model.runtime_defaults`, `deployment.runtime_overrides`) are caught when `resolve_deployment()` validates through `VLLMRuntimeConfig.model_validate()`.
  - Test: `test_config_resolution.py` — typo in each cascade layer raises `ValidationError`
- [x] **A6**: Document `hf_token` dual rendering in the adapter. Add a comment at vllm.py:201-202 noting that `hf_token` renders to both CLI (`--hf-token`) and env (`HF_TOKEN`) intentionally — vLLM reads from both paths (see audit Observation #2 in `SEP-004-05-CONTEXT-runtime-field-audit.md`).
- [x] **A7**: Extract `VLLMAdapter.build_launch_spec(resolved)` method. Move the full launch construction from `start()` (env, ports, normalized container options, labels, volumes, name, device requests/CPU env vars) into a structured dict-returning method. `start()` consumes this dict for `containers.run()`; Phase C preview returns it as JSON. This is the single critical anti-drift mechanism.
  - Test: `test_vllm_resolved_command.py` — `build_launch_spec` returns same structure that `start()` passes to Docker SDK.

### Phase 2: Cascade Contract

**Goal**: Replace shallow `dict.update` cascade merge with deep merge for nested dicts, preserving last-in-wins at top level.

#### Tasks
- [x] **B1**: Create `switchyard/core/merge.py` with `_deep_merge` utility. Extract from `app.py:597+` to neutral module to avoid import cycle (`app.py` imports from `loader.py`). Update `app.py` and `loader.py` to import from new module.
  - Test: `test_config_resolution.py` — `_deep_merge` behaves identically to old `app.py` version
  - Test: `test_config_resolution.py` — partial `speculative_config` override at deployment level retains runtime-level keys (e.g., runtime provides `method`, deployment overrides `num_speculative_tokens`, result has both)
- [x] **B1.5**: Extend `_deep_merge` to support `null` as wipe signal. If override value is `None`/`null` and the base has a nested dict for that key, the key is removed from result (equivalent to Helm/Kustomize `null` semantics). Scalar `null` is rejected with `ValueError`.
  - Test: `test_config_resolution.py` — `speculative_config: null` at deployment level wipes all lower-layer entries
  - Test: `test_config_resolution.py` — scalar `null` raises `ValueError`
- [x] **B2**: Replace `merged.update(layer)` in `_merge_runtime_args` (loader.py) with `_deep_merge`. Apply to all four cascade layers including `extra_args` (which now deep-merges instead of replacing).
  - Test: `test_config_resolution.py` — partial `speculative_config` override at deployment level retains runtime-level keys (e.g., runtime provides `method`, deployment overrides `num_speculative_tokens`, result has both)
  - Test: `test_config_resolution.py` — `extra_args` in runtime.defaults and deployment.extra_args are deep-merged
- [x] **B2.5**: Verify cascade order is `model.defaults → runtime.defaults → model.runtime_defaults → deployment.runtime_overrides` (matches code, per PRD rationale). Add explicit docstring to `_merge_runtime_args` documenting the order and rationale.
  - Test: `test_config_resolution.py` — known field at each layer verifies last-in-wins; top-level `dtype` from runtime.defaults wins over model.defaults
- [x] **B3**: **Cascade provenance tracking** — `_merge_runtime_args` now returns `(merged, provenance)` where provenance maps each top-level key to its source layer name. On `ValidationError`, `resolve_deployment()` enriches the error with a `ValueError` naming the source layer (e.g., "Extra inputs are not permitted for field 'max_seqs' in deployment.runtime_overrides"). `_resolve_deployment()` in `app.py` catches both `PydanticValidationError` and `ValueError` and converts them to HTTP 400.
  - Test: `test_config_resolution.py` — typo error message contains source layer name

### Phase 3: Preview API

**Goal**: Expose `GET /api/deployments/{deployment}/preview` returning structured JSON container launch spec.

#### Tasks
- [x] **C1**: Add preview route to `_register_routes` (app.py). Resolves the deployment, reconciles with Docker, calls `adapter.build_preview_spec(resolved)`, and returns structured JSON.
  - Response shape: `image`, `command` (flat list: `["--host", "0.0.0.0", "--port", "8000", ...]` — **no** `"serve"` prefix, matches exact Docker SDK command), `environment` (dict), `mounts` (dict), `network`, `host_port`, `port_range`, `container_options` (normalized Docker SDK keys), `accelerator_ids`, `deployment_status`, `container_status`, `docker_error`
  - Port semantics: if reconciliation finds a managed container (loading/running/error), return actual host port; if no container exists, return `host_port: null`. No allocator mutation.
  - Environment includes adapter-injected vars: `VLLM_CPU_KVCACHE_SPACE` and `VLLM_CPU_NUM_OF_RESERVED_CPU` (CPU device only), `HF_TOKEN` (when `runtime.hf_token` is set), plus `resolved.container_environment`
  - `container_options` uses normalized Docker SDK keys (`ipc_mode`, not `ipc`); labels excluded (live in own `labels` field)
- [x] **C2**: Add list-aware sensitive field redactor. The existing `_mask_sensitive_args` (app.py) only masks dictionaries by key. Added `_redact_command_list()` companion helper that redacts flat CLI command lists by recognizing the `--flag value` pattern (e.g., `["--hf-token", "secret"]` → `["--hf-token", "***redacted***"]`). Applied to both `command` and `environment` in the preview response. Matching is case-insensitive.
  - Test: `test_app.py` — preview with `hf_token` in runtime_args shows `***redacted***` in both command list and environment dict
- [x] **C3**: Exclude Switchyard process-local settings (`.env`-sourced `SWITCHYARD_*` variables) from the preview. These are control-plane config, not deployment config.
  - Test: `test_app.py` — no `SWITCHYARD_*` keys appear in preview response
- [x] **C4**: Live smoke test under `tests/live/test_preview.py` exercising the preview endpoint against a running API. Verifies the preview command structure, sensitive field redaction, and SWITCHYARD_* exclusion. CPU/GPU smoke tests deferred to post-merge validation (require Docker + backend infrastructure).
  - Fixture: `live_backend_url` from `tests/live/conftest.py`
  - Test: `tests/live/test_preview.py` — preview endpoint returns 200, command contains expected flags, sensitive fields are redacted

---

## Dependencies

### Critical Path
1. A1–A7 (schema hardening) must complete before B1–B3 (cascade contract) because `extra: "forbid"` validation exercises the merged dict
2. A7 (adapter launch-spec extraction) is the single most important anti-drift task and unblocks C1–C4
2. B1–B3 must complete before C1–C4 (preview API) because preview depends on correct cascade merge output
3. All implementation must be under `switchyard-api/`

### Parallel Work Streams
- None — phases are sequential. Within each phase, tests can be written TDD-style alongside implementation.

---

## Technical Architecture Decisions

### Agreed Decisions
- **Shared merge utility**: `_deep_merge` extracted to `switchyard/core/merge.py` to avoid import cycle between `app.py` and `loader.py` (Codex item 1).
- **Validation at resolution**: `resolve_deployment()` calls `VLLMRuntimeConfig.model_validate()` before returning `ResolvedDeployment`, so typos surface before any Docker call (Codex item 2).
- **Adapter-owned launch spec**: `VLLMAdapter.build_launch_spec(resolved)` returns full structured launch dict consumed by both `start()` and preview API. Prevents drift between preview and actual launch (Codex item 3).
- **Preview port semantics**: Three-state — running → actual reconciled port; stopped → `null` with `port_range` context. No allocator mutation during read (Codex item 6).
- **List-aware redaction**: Separate helper for flat CLI command lists (flag/value pair pattern) in addition to dict-based `_mask_sensitive_args` (Codex item 4).
- **extra_args cascade**: Deep-merge through all layers; later layers overlay lower-layer `extra_args` entries (Codex item 7).
- **Null as wipe signal**: Setting a nested field to `null` at any layer fully resets it, consistent with Helm/Kustomize semantics (Codex item 8).
- **Exact launch spec parity**: Preview `command` matches exact Docker SDK command (no `ENTRYPOINT` prepended; no `"serve"` prefix) (Codex item 5).

---

## Risk Mitigation

### Risk 1: Deep merge breaks existing deployments relying on shallow wipe
- **Mitigation**: Audit `config.yaml` for deployments with nested dict overrides (only `speculative_config` in `qwen3.6-27b-fp8-vllm-trainbox` uses one). Tests cover both merge and wipe cases.
- **Owner**: Implementation
- **Timeline**: Phase 2, task B1

### Risk 2: `extra: "forbid"` rejects valid config using undocumented flags
- **Mitigation**: Operators using undocumented flags should be putting them in `extra_args`. Any rejection surfaces at resolution with clear error.
- **Owner**: Implementation
- **Timeline**: Phase 1, task A1

### Risk 3: Preview API exposes `hf_token` in plain text
- **Mitigation**: Reuse `_mask_sensitive_args` (app.py:425-442) on all preview response fields.
- **Owner**: Implementation
- **Timeline**: Phase 3, task C2

### Risk 4: Preview API drifts from actual launch command
- **Mitigation**: Preview and `start()` share `_build_cli_args`. Test both against the same `VLLMRuntimeConfig` fixtures.
- **Owner**: Implementation
- **Timeline**: Phase 3, task C4

### Risk 5: Cascade typo errors reference merged dict, not source layer
- **Mitigation**: Provenance tracking (task B3) attaches source layer name to error messages.
- **Owner**: Implementation
- **Timeline**: Phase 2, task B3

---

## Validation Plan

**Validation Commands**: See `AGENTS.md` Section "Quality Gates" for complete validation command list.

### Success Criteria Validation
- [x] Unknown top-level key in any cascade layer raises `ValidationError` with source layer name in error message (PRD Done criterion 1 + Risk 5)
- [x] `device` excluded from CLI args by field metadata, not string matching (PRD Done criterion 2)
- [x] Partial nested dict override retains lower-layer keys (PRD Done criterion 3)
- [x] `extra_args` dict values render as valid JSON; keys render as kebab-case (PRD Done criterion 4)
- [x] Preview endpoint returns complete container spec matching `start()` launch config (PRD Done criterion 5)
- [x] Sensitive fields (`hf_token`, `api_key`) redacted in preview response
- [x] All quality gates pass: `uv run pytest` (379), `uv run ruff check` (clean), `uv run mypy` (clean)
- [ ] CPU smoke test (tinyllama) and GPU smoke test (qwen3.6-27b-fp8) pass through hardened resolver in `tests/live/` — **deferred to post-merge** (requires Docker tunnel + backend container running)
- [x] No regressions in existing functionality (374 unit tests pass)

---

## Decision Log

- 2026-05-12 — Post-Phase 1 review findings addressed:
  - **Finding 1**: Reverted container_options merge order in `start()` — user options applied first, Switchyard-owned fields force-applied after. Added regression test.
  - **Finding 2**: Added `_resolve_deployment()` helper wrapping `resolve_deployment()` in all API routes. Converts `PydanticValidationError` → HTTP 400 with field-level detail. Added 2 API integration tests.
  - **Finding 3**: Split `LaunchSpec` (Docker SDK types) from `PreviewSpec` (JSON-serializable, Switchyard domain language). Added `build_preview_spec()` method.

- 2026-05-12 — Phase 2 cascade contract decisions (Agreed):
  - **extra_args in all layers**: Support `extra_args` keys in all four typed runtime-arg layers (model.defaults, runtime.defaults, model.runtime_defaults, deployment.runtime_overrides), deep-merged naturally by `_deep_merge`. `deployment.extra_args` is then deep-merged on top.
  - **Null as wipe**: `null` for a nested dict field wipes the entire block (key removed from result), matching Helm/Kustomize semantics. Scalar `null` is **rejected** with `ValueError` — operators should omit the key to inherit, or set a real value.
  - **Provenance granularity**: Track provenance at top-level field granularity only. Nested dict key provenance is unnecessary since Pydantic's own error messages already name the field path within `VLLMRuntimeConfig`.
  - **_deep_merge extraction**: Extracted from `app.py` to `switchyard/core/merge.py` to avoid import cycles. Both `app.py` and `loader.py` import from the neutral module.

- 2026-05-12 — Phase 3 preview API decisions (Agreed):
  - **port_range in PreviewSpec**: `port_range` included directly in `PreviewSpec` alongside `host_port`, providing port allocation context regardless of deployment status.
  - **container_options normalized**: Preview shows Docker SDK-normalized keys (`ipc_mode`, not `ipc`) so the preview matches actual launch behavior. Labels excluded (live in own `labels` field).
  - **Port semantics**: Actual port returned whenever `ReconcileResult.info` exists (loading/running/error). `host_port: null` only when no container exists (stopped).
  - **Status fields**: `deployment_status`, `container_status`, and `docker_error` included in preview response so operators understand the port's context.
  - **Case-insensitive redaction**: `_mask_sensitive_args` does case-insensitive substring matching (fixed `HF_TOKEN` not being caught).
  - **List-aware redaction**: `_redact_command_list()` recognizes `--flag value` pairs and redacts values for sensitive flag names.
  - **Common launch fields extraction**: `_compute_common_launch_fields()` shared by `build_launch_spec()` and `build_preview_spec()` to mitigate PRD Risk 4 (preview drift).
