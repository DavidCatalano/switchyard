# Devlog — SEP-001 MVP Control Plane

**Title**: MVP Control Plane
**ID**: SEP-001-03-DEVLOG-mvp-control-plane
**PRD**: N/A (internal tooling — spec-driven)

---

## Entries

- 2026-04-27 — Project initialized. Completed research into vLLM's CLI surface (~39 meaningful flags across 12 argument groups). Settled on a three-level cascade model for config (global → per-backend defaults → per-model overrides) rather than a flat tiered schema.
- 2026-04-28 — Phase 1 complete (scaffolding + configuration). Key decisions: `RuntimeDefaults` uses Pydantic `extra="allow"` so backend keys map directly from YAML. OTel integration depends only on `opentelemetry-api` — no SDK lock-in. Config loader performs additive `extra_args` merging so defaults and per-model flags coexist. 63 tests, all gates green. Next: Phase 2 (BackendAdapter protocol, adapter registry, port allocator, deployment state manager).
- 2026-04-28 — Phase 2 complete. Delivered `BackendAdapter` ABC + `DeploymentInfo` frozen dataclass (T2.1), `AdapterRegistry` with factory pattern (T2.2), `PortAllocator` with sequential/skip-in-use/thread-safe allocation (T2.3), and `DeploymentStateManager` with status transitions and port lookups (T2.4). 105 tests total, all gates green. T2.5 (combined test coverage) satisfied by individual test suites.
- 2026-04-28 — Phase 3 in progress. Delivered `LifecycleManager` with `load_model`/`unload_model`/background health polling (T3.1–T3.3, 15 tests), and `OrphanDetector` with Docker container scan/adopt/remove logic (T3.4, 6 tests). 126 tests total, all gates green. Remaining: T3.5 (startup bootstrap sequence), T3.6 (startup sequence tests — lifecycle + orphan tests already cover T3.1–T3.4).

---

## Handoff

**Next work**: Phase 3 completion (T3.5 — startup bootstrap sequence), then Phase 4 (API endpoints + routing).

**Mandatory reading** (in order):
1. `switchyard-internal/process/DEV.md` — workflow, branching, gates
2. `switchyard-internal/process/PYTHON.md` — typing, TDD rules, lint/type commands
3. `switchyard-internal/sep/SEP-001-02-PLAN-mvp-control-plane.md` — task breakdown; T3.5–T4.x descriptions
4. `spec.md` — lifecycle semantics, error codes, bootstrap sequence (§12)
5. `switchyard-api/src/switchyard/core/lifecycle.py` — `LifecycleManager` to bootstrap wires into
6. `switchyard-api/src/switchyard/core/orphan.py` — `OrphanDetector` to call during bootstrap
7. `switchyard-api/src/switchyard/app.py` — `create_app` factory; where bootstrap lives

**Context to carry forward**:
- Branch: `sep/001` only. Never touch `main`.
- Quality gates: `cd switchyard-api && uv run pytest && uv run ruff check src tests --fix && uv run mypy src/switchyard`
- Dev server: `cd switchyard-api && SWITCHYARD_CONFIG_PATH=config.yaml uv run uvicorn switchyard.app:create_app --factory`
- Config cascade: `global` → `runtime_defaults.{backend}` → `models.{name}.runtime`
- `extra_args` merging is additive (per-model wins on key conflict) — don't regress
- OTel hooks use only `opentelemetry-api`; no `opentelemetry-sdk` imports anywhere
- Phase 3 bootstrap sequence (§12 spec): load config → verify Docker → orphan detection → auto-start → listen
