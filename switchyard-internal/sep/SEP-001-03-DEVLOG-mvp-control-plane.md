# Devlog — SEP-001 MVP Control Plane

**Title**: MVP Control Plane
**ID**: SEP-001-03-DEVLOG-mvp-control-plane
**PRD**: N/A (internal tooling — spec-driven)

---

## Entries

- 2026-04-27 — Project initialized. Completed research into vLLM's CLI surface (~39 meaningful flags across 12 argument groups). Settled on a three-level cascade model for config (global → per-backend defaults → per-model overrides) rather than a flat tiered schema.
- 2026-04-28 — Phase 1 complete (scaffolding + configuration). Key decisions: `RuntimeDefaults` uses Pydantic `extra="allow"` so backend keys map directly from YAML. OTel integration depends only on `opentelemetry-api` — no SDK lock-in. Config loader performs additive `extra_args` merging so defaults and per-model flags coexist. 63 tests, all gates green. Next: Phase 2 (BackendAdapter protocol, adapter registry, port allocator, deployment state manager).

---

## Handoff

**Next work**: Phase 2 — Core Infrastructure (T2.1–T2.5). Define `BackendAdapter` protocol, adapter registry, port allocator, and in-memory deployment state manager. TDD mandatory.

**Mandatory reading** (in order):
1. `switchyard-internal/process/DEV.md` — workflow, branching, gates
2. `switchyard-internal/process/PYTHON.md` — typing, TDD rules, lint/type commands
3. `switchyard-internal/sep/SEP-001-02-PLAN-mvp-control-plane.md` — task breakdown; T2.1–T2.5 descriptions
4. `spec.md` — lifecycle semantics, error codes
5. `switchyard-api/src/switchyard/config/models.py` — existing Pydantic models the adapter will reference
6. `switchyard-api/src/switchyard/app.py` — `create_app` factory; where new components eventually wire in

**Context to carry forward**:
- Branch: `sep/001` only. Never touch `main`.
- Quality gates: `cd switchyard-api && uv run pytest && uv run ruff check src tests --fix && uv run mypy src/switchyard`
- Dev server: `cd switchyard-api && SWITCHYARD_CONFIG_PATH=config.yaml uv run uvicorn switchyard.app:create_app --factory`
- Config cascade: `global` → `runtime_defaults.{backend}` → `models.{name}.runtime`
- `extra_args` merging is additive (per-model wins on key conflict) — don't regress
- OTel hooks use only `opentelemetry-api`; no `opentelemetry-sdk` imports anywhere
- The `switchyard-internal/sep/SEP-001-04-CONTEXT-vllm-config.md` and `SEP-001-05-CONSULT-future-vllm-flag-audit.md` are **not** needed for Phase 2 — they were research artifacts already baked into the config models.
