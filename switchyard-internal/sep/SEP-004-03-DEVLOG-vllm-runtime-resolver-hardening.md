# Devlog

**Title**: vLLM Runtime Resolver Hardening
**ID**: SEP-004-03-DEVLOG-vllm-runtime-resolver-hardening
**PRD**: `SEP-004-01-PRD-vllm-runtime-resolver-hardening.md`
**PLAN**: `SEP-004-02-PLAN-vllm-runtime-resolver-hardening.md`

---

## Entries

- 2026-05-11 — Created field audit (`SEP-004-05-CONTEXT-runtime-field-audit.md`) classifying all 40 `VLLMRuntimeConfig` fields as CLI-rendering, Switchyard-only, or dual-purpose. Identified hardening gaps: no `extra: "forbid"`, shallow cascade merge, `extra_args` serialization bugs, fragile `device` skip.
- 2026-05-12 — Drafted PRD (3 phases: schema hardening, cascade contract, preview API). Drafted PLAN (14 tasks across 3 phases). Incorporated Codex review: import cycle fix (`_deep_merge` extraction), explicit validation at resolve time, adapter-owned `build_launch_spec()`, list-aware redactor, 8 agreed contract decisions.

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
