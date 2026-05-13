# Devlog

**Title**: vLLM Runtime Resolver Hardening
**ID**: SEP-004-03-DEVLOG-vllm-runtime-resolver-hardening
**PRD**: `SEP-004-01-PRD-vllm-runtime-resolver-hardening.md`
**PLAN**: `SEP-004-02-PLAN-vllm-runtime-resolver-hardening.md`

---

## Entries

- 2026-05-11 — Created field audit (`SEP-004-05-CONTEXT-runtime-field-audit.md`) classifying all 40 `VLLMRuntimeConfig` fields as CLI-rendering, Switchyard-only, or dual-purpose. Identified hardening gaps: no `extra: "forbid"`, shallow cascade merge, `extra_args` serialization bugs, fragile `device` skip.
- 2026-05-12 — Drafted PRD (3 phases: schema hardening, cascade contract, preview API). Drafted PLAN (14 tasks across 3 phases). Incorporated Codex review: import cycle fix (`_deep_merge` extraction), explicit validation at resolve time, adapter-owned `build_launch_spec()`, list-aware redactor, 8 agreed contract decisions.
- 2026-05-12 — Phase 1 complete (A1–A7). Added `extra: "forbid"` to `VLLMRuntimeConfig`, explicit `model_validate()` in `resolve_deployment()`, structural `switchyard_only` skip for `device`, `extra_args` JSON serialization + kebab-case normalization, `hf_token` dual-rendering comment, and `build_launch_spec()` extraction with `LaunchSpec` TypedDict. 351 tests pass, ruff/mypy clean. One existing test in `test_vllm_adapter.py` updated for kebab-case normalization.
- 2026-05-12 — Phase 2 complete (B1–B3). Extracted `_deep_merge` to `switchyard/core/merge.py`, replacing shallow `dict.update` in `_merge_runtime_args()`. Added null-as-wipe for nested dicts, rejected scalar nulls. Deep-merge `extra_args` from all layers including `deployment.extra_args`. Provenance tracking: `_merge_runtime_args()` returns `(merged, provenance)` map; `resolve_deployment()` enriches `ValidationError` into `ValueError` naming the source layer. 360 tests pass, ruff/mypy clean. Updated `_resolve_deployment()` in `app.py` to catch both `PydanticValidationError` and `ValueError`.
- 2026-05-12 — Phase 3 complete (C1–C4). Added `GET /api/deployments/{deployment}/preview` route. `build_preview_spec()` extended with `port_range`, `container_options` (normalized Docker SDK keys), `deployment_status`, `container_status`, `docker_error`. Added `_redact_command_list()` for flag/value pair redaction in CLI command lists. Fixed `_mask_sensitive_args` to do case-insensitive matching (caught `HF_TOKEN`). Excluded `SWITCHYARD_*` env vars from preview. Live smoke test in `tests/live/test_preview.py`. 374 tests pass, ruff/mypy clean.


---

## Cold Start / Handoff

Read in this order:

1. `SEP-004-01-PRD-vllm-runtime-resolver-hardening.md` — what and why
2. `SEP-004-02-PLAN-vllm-runtime-resolver-hardening.md` — tasks, agreed decisions, validation plan
3. `SEP-001-04-CONTEXT-vllm-config.md` — tier definitions, Trainbox validation, rationale for which fields are on `VLLMRuntimeConfig`
4. `SEP-004-05-CONTEXT-runtime-field-audit.md` — current field inventory, classifications, and risks (reference for Phase A)
5. Code files — **reference only, read per-task during TDD**, not mandatory pre-reading:
   - `switchyard-api/src/switchyard/config/models.py` (line 398+) — `VLLMRuntimeConfig` definition
   - `switchyard-api/src/switchyard/config/loader.py` — `_merge_runtime_args`, `resolve_deployment`
   - `switchyard-api/src/switchyard/adapters/vllm.py` — `_build_cli_args` (line 118+), `start()` (line 171+)
   - `switchyard-api/src/switchyard/app.py` — `_mask_sensitive_args` (line ~425), `_deep_merge` (line ~597, to be extracted)

Start with Phase 1, Task A1 (PLAN). Branch: `sep/004`.
