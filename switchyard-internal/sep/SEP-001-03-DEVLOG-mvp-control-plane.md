# Devlog — SEP-001 MVP Control Plane

**Title**: MVP Control Plane
**ID**: SEP-001-03-DEVLOG-mvp-control-plane
**PRD**: N/A (internal tooling — spec-driven)

---

## Entries

- 2026-04-27 — Project initialized. Completed research into vLLM's CLI surface (~39 meaningful flags across 12 argument groups). Settled on a three-level cascade model for config (global → per-backend defaults → per-model overrides) rather than a flat tiered schema.
- 2026-04-28 — Phase 1 complete (scaffolding + configuration). Key decisions: `RuntimeDefaults` uses Pydantic `extra="allow"` so backend keys map directly from YAML. OTel integration depends only on `opentelemetry-api` — no SDK lock-in. Config loader performs additive `extra_args` merging so defaults and per-model flags coexist. 63 tests, all gates green.
- 2026-04-28 — Phase 2 complete. Delivered `BackendAdapter` ABC + `DeploymentInfo` frozen dataclass, `AdapterRegistry` with factory pattern, `PortAllocator` with sequential/skip-in-use/thread-safe allocation, and `DeploymentStateManager` with status transitions and port lookups. 105 tests total, all gates green.
- 2026-04-28 — Phase 3 complete. Delivered `LifecycleManager` (`load_model`, `unload_model`, background health polling, `bootstrap()` startup sequence), `OrphanDetector` with Docker container scan/adopt/remove using `Protocol`-based typing, and reserved port allocation (`PortAllocator.allocate(port=N)`). 133 tests total, all gates green.

---

## Handoff

**Next work**: Phase 4 — API endpoints + routing (T4.1–T4.9). Wire `LifecycleManager` and `DeploymentStateManager` into FastAPI routes under `_register_routes()` in `app.py`.

**Context to carry forward**:
- Branch: `sep/001` only. Never touch `main`.
- Quality gates: `cd switchyard-api && uv run pytest && uv run ruff check src tests --fix && uv run mypy src/switchyard`
- Dev server: `cd switchyard-api && SWITCHYARD_CONFIG_PATH=config.yaml uv run uvicorn switchyard.app:create_app --factory`
- Config cascade: `global` → `runtime_defaults.{backend}` → `models.{name}.runtime`
- `extra_args` merging is additive (per-model wins on key conflict) — don't regress
- OTel hooks use only `opentelemetry-api`; no `opentelemetry-sdk` imports anywhere
- `LifecycleManager.bootstrap()` must be called at startup (Docker verify → orphan adopt → auto-start)
- `auto_start` lives at `model_config.control.auto_start` (nested in `ControlConfig`)
- Status values: `"running" | "stopped" | "loading" | "error"`
