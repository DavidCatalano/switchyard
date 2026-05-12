# Project Plan (PLAN)

**Title**: vLLM Runtime Resolver Hardening
**ID**: SEP-004-02-PLAN-vllm-runtime-resolver-hardening
**Status**: Draft
**Date**: 2026-05-11
**PRD**: `SEP-004-01-PRD-vllm-runtime-resolver-hardening.md`

---

## Implementation Approach

Execute the PRD's three phases in sequence: schema hardening (tighten `VLLMRuntimeConfig` and adapter CLI building), cascade contract (deep merge for nested dicts), preview API (structured JSON container spec endpoint). Each phase is self-contained and testable before the next begins.

Key existing code to work from:
- `switchyard-api/src/switchyard/config/models.py` — `VLLMRuntimeConfig` (line 398+), entity models
- `switchyard-api/src/switchyard/config/loader.py` — `_merge_runtime_args` (shallow merge), `resolve_deployment`
- `switchyard-api/src/switchyard/adapters/vllm.py` — `_build_cli_args` (line 118+), `start()` (line 170+)
- `switchyard-api/src/switchyard/app.py` — `_deep_merge` (line ~597, currently only used for config overrides), `_mask_sensitive_args` (line ~425)
- `switchyard-api/tests/test_vllm_resolved_command.py` — existing resolved→CLI test pattern
- `switchyard-api/tests/live/` — live smoke test fixtures (`live_docker`, `live_backend_url`)

---

## Task Breakdown

### Phase 1: Schema Hardening

**Goal**: Enforce the two-bucket strict contract on `VLLMRuntimeConfig` and fix adapter serialization bugs.

#### Tasks
- [ ] **A1**: Add `model_config = {"extra": "forbid"}` to `VLLMRuntimeConfig` (models.py). Top-level typos in YAML runtime args now reject with Pydantic validation error at merge time, before any Docker call.
  - Test: `test_config_resolution.py` — unknown key in `runtime_overrides` raises `ValidationError`
- [ ] **A2**: Replace hardcoded skip tuple in `_build_cli_args` (vllm.py:143) with structural skip derived from Pydantic field metadata. Mark `device` with `Field(json_schema_extra={"switchyard_only": True})` and derive skip set via `VLLMRuntimeConfig.model_fields[<name>].json_schema_extra`.
  - Confirm Pydantic v2 access path for `json_schema_extra` during implementation (forward note from consultant).
  - Test: `test_vllm_resolved_command.py` — `device` never appears in CLI args; adapter rejects no fields by string matching
- [ ] **A3**: Fix `extra_args` dict serialization. Dict values inside `extra_args` must use `json.dumps()` (matching named dict fields at vllm.py:154-156), not `str(value)`.
  - Test: `test_vllm_resolved_command.py` — `extra_args: {some_config: {"a": 1}}` renders as valid JSON
- [ ] **A4**: Normalize `extra_args` keys through `_field_to_flag` (snake-case → kebab-case) so they match vLLM's CLI convention. Currently `extra_args` keys render verbatim (vllm.py:162).
  - Test: `test_vllm_resolved_command.py` — `extra_args: {disable_log_requests: true}` renders as `--disable-log-requests`
- [ ] **A5**: Add `model_config = {"extra": "forbid"}` test for cascade path. Verify that a typo in `runtime.defaults`, `model.runtime_defaults`, or `deployment.runtime_overrides` is caught when the merged dict is validated through `VLLMRuntimeConfig.model_validate()`.
  - Test: `test_config_resolution.py` — typo in each cascade layer raises
- [ ] **A6**: Document `hf_token` dual rendering in the adapter. Add a comment at vllm.py:201-202 noting that `hf_token` renders to both CLI (`--hf-token`) and env (`HF_TOKEN`) intentionally — vLLM reads from both paths (see audit Observation #2 in `SEP-004-05-CONTEXT-runtime-field-audit.md`).

### Phase 2: Cascade Contract

**Goal**: Replace shallow `dict.update` cascade merge with deep merge for nested dicts, preserving last-in-wins at top level.

#### Tasks
- [ ] **B1**: Replace `merged.update(layer)` in `_merge_runtime_args` (loader.py) with `_deep_merge`. Import `_deep_merge` directly from `app.py:597+` (default to import-direct; extract to shared utility only if a second caller emerges).
  - Test: `test_config_resolution.py` — partial `speculative_config` override at deployment level retains runtime-level keys (e.g., runtime provides `method`, deployment overrides `num_speculative_tokens`, result has both)
- [ ] **B2**: Verify cascade order is `model.defaults → runtime.defaults → model.runtime_defaults → deployment.runtime_overrides` (matches code, per PRD rationale). Add explicit docstring to `_merge_runtime_args` documenting the order and rationale.
  - Test: `test_config_resolution.py` — known field at each layer verifies last-in-wins; top-level `dtype` from runtime.defaults wins over model.defaults
- [ ] **B3**: **Cascade provenance tracking** — build a parallel `dict[str, str]` mapping each merged key to its source layer name. Surface in error messages when `extra: "forbid"` rejects a key, so diagnostics name the source layer (e.g., "unknown key 'max_seqs' in deployment.runtime_overrides").
  - Implementation: track provenance during merge loop; on `ValidationError`, walk the provenance map to attach layer context to the error message.
  - Test: `test_config_resolution.py` — typo error message contains source layer name

### Phase 3: Preview API

**Goal**: Expose `GET /api/deployments/{deployment}/preview` returning structured JSON container launch spec.

#### Tasks
- [ ] **C1**: Add preview route to `_register_routes` (app.py). Resolves the deployment, builds CLI args via `_build_cli_args`, and returns structured JSON.
  - Response shape: `image`, `command` (flat list: `["serve", "--host", "0.0.0.0", "--port", "8000", ...]`), `environment` (dict), `mounts` (dict), `network`, `host_port` (allocated or next-available), `accelerator_ids`, `container_options`
  - Environment must include adapter-injected vars: `VLLM_CPU_KVCACHE_SPACE` and `VLLM_CPU_NUM_OF_RESERVED_CPU` (CPU device only, vllm.py:270-271), `HF_TOKEN` (when `runtime.hf_token` is set, vllm.py:201-202), plus `resolved.container_environment`
  - Include hardcoded `--host 0.0.0.0` and `--port <internal_port>` prepended in `start()` (vllm.py:194-197) — these are not on `VLLMRuntimeConfig` but must appear in preview
- [ ] **C2**: Apply `_mask_sensitive_args` to the preview response. Reuse existing function (app.py:425-442) to redact `hf_token`, `api_key`, and other secret-shaped fields in both `command` and `environment`.
  - Test: `test_app.py` — preview with `hf_token` in runtime_args shows `***redacted***`
- [ ] **C3**: Exclude Switchyard process-local settings (`.env`-sourced `SWITCHYARD_*` variables) from the preview. These are control-plane config, not deployment config.
  - Test: `test_app.py` — no `SWITCHYARD_*` keys appear in preview response
- [ ] **C4**: Add CPU smoke test (tinyllama) and GPU smoke test (qwen3.6-27b-fp8) under `tests/live/` that exercise the preview endpoint against the running API. Verify the preview command matches what `start()` would launch.
  - Fixture: `live_backend_url` from `tests/live/conftest.py`
  - Test: `tests/live/test_preview.py` — preview endpoint returns 200, command contains expected flags, sensitive fields are redacted

---

## Dependencies

### Critical Path
1. A1–A6 (schema hardening) must complete before B1–B3 (cascade contract) because `extra: "forbid"` validation exercises the merged dict
2. B1–B3 must complete before C1–C4 (preview API) because preview depends on correct cascade merge output
3. All implementation must be under `switchyard-api/`

### Parallel Work Streams
- None — phases are sequential. Within each phase, tests can be written TDD-style alongside implementation.

---

## Technical Architecture Decisions

**IMPORTANT:** Per AGENTS.md, no architectural decisions are captured here during planning. Decisions that emerge during implementation will be recorded in the PR document.

Items to confirm during implementation (not decisions):
- Pydantic v2 `model_fields[<name>].json_schema_extra` access path for Switchyard-only field introspection

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
- [ ] Unknown top-level key in any cascade layer raises `ValidationError` with source layer name in error message (PRD Done criterion 1 + Risk 5)
- [ ] `device` excluded from CLI args by field metadata, not string matching (PRD Done criterion 2)
- [ ] Partial nested dict override retains lower-layer keys (PRD Done criterion 3)
- [ ] `extra_args` dict values render as valid JSON; keys render as kebab-case (PRD Done criterion 4)
- [ ] Preview endpoint returns complete container spec matching `start()` launch config (PRD Done criterion 5)
- [ ] Sensitive fields (`hf_token`, `api_key`) redacted in preview response
- [ ] All quality gates pass: `uv run pytest`, `uv run ruff check`, `uv run mypy`
- [ ] CPU smoke test (tinyllama) and GPU smoke test (qwen3.6-27b-fp8) pass through hardened resolver in `tests/live/`
- [ ] No regressions in existing functionality

---

## Decision Log

_No decisions logged yet. Decisions made during implementation will be recorded here._
