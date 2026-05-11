# Devlog

**Title**: Health-Aware Deployment Status Semantics
**ID**: SEP-003a-03-DEVLOG-health-aware-status
**PRD**: `SEP-003a-01-PRD-health-aware-status.md`
**PLAN**: `SEP-003a-02-PLAN-health-aware-status.md`

---

## Entries

- 2026-05-10 — SEP-003a planning is ready for implementation review. The
  accepted direction is conservative health-aware reconciliation: Docker
  `running` is not enough to mark a Switchyard deployment `running`.
  Adopted Docker-running containers should enter `loading` and transition
  through health polling.

- 2026-05-10 — The PLAN was revised to include the implementation
  breadcrumbs needed for a long coding session: `ReconcileResult`,
  `_ensure_health_task()`, Docker-unreachable route behavior, terminal
  `error` semantics, first-class `health_timeout_seconds`, and
  bootstrap/orphan scope verification.

- 2026-05-10 — Phase 1 coding complete. Three review findings addressed:
  1. `load_model()` metadata now starts from `deployment.metadata`
     (adapter-provided) and overlays `runtime_args`, preserving fields
     like vLLM `served_model_name`.
  2. `_ensure_health_task()` checks `task.done()` instead of
     `task.cancelled()` so completed/failed tasks are replaced.
  3. `reconcile()` try/except expanded to wrap `remove_container()`
     and status lookup, not just factory/lookup — any Docker API failure
     now returns `docker_error`.

- 2026-05-10 — Phase 2 review: `bootstrap()` confirmed not wired into
  production startup. T2 scope is minimal (semantics + tests only).
  Resolver callable passed into `bootstrap()` rather than direct import.
  Orphan skip-on-error decided. PLAN updated with findings.

- 2026-05-10 — Phase 2 coding complete. `bootstrap()` normalizes orphans
  to `loading` with metadata from resolver callable, skips orphans on
  config error. All bootstrap/orphan tests updated and passing.

- 2026-05-10 — Phase 2 review: three findings addressed:
  1. Bootstrap uses `resolved.backend` instead of `orphan.backend`;
     warns on mismatch. Tests register `vllm` backend so health tasks run.
  2. `resolve=None` now skips orphans with warning (incomplete adoption).
  3. Resolver catches only `KeyError`/`ValueError` (not broad `Exception`).

- 2026-05-10 — Phase 3 coding complete. API routes now surface
  `container_status` and `docker_error`. Broad `except Exception: pass`
  removed from all routes. `health` field derived from status.
  Docker-unreachable behavior verified per route.

- 2026-05-10 — Phase 4 coding complete. `health_timeout_seconds` added to
  `RuntimeConfig`, `DeploymentConfig`, and `ResolvedDeployment`. Cascade:
  deployment > runtime > 300s default. Guard in `_merge_runtime_args()`
  rejects placement in runtime args. `_build_adopted_metadata()` sets
  `health_timeout_seconds` in metadata. All three paths (load, reconcile,
  bootstrap) pass timeout to health poll.

---

## Cold Start / Handoff

Phase 1 is complete (T1.1–T1.6 checked). Phase 2 is complete (T2.0–T2.4 checked). Phase 3 is complete (T3.1–T3.5 checked). Phase 4 is complete (T4.1–T4.3 checked). Phase 5–6 remain.

Start by reading these SEP artifacts in order:

1. `SEP-003a-01-PRD-health-aware-status.md`
2. `SEP-003a-02-PLAN-health-aware-status.md`
3. `SEP-003a-04-CONTEXT-health-aware-status.md`

Then inspect these implementation files before editing:

- `switchyard-api/src/switchyard/core/lifecycle.py`
- `switchyard-api/src/switchyard/app.py`
- `switchyard-api/src/switchyard/config/models.py`
- `switchyard-api/src/switchyard/config/loader.py`
- `switchyard-api/src/switchyard/core/orphan.py`

Then inspect these test files:

- `switchyard-api/tests/test_lifecycle.py`
- `switchyard-api/tests/test_api.py`
- `switchyard-api/tests/test_bootstrap.py`
- `switchyard-api/tests/test_orphan.py`
- `switchyard-api/tests/test_config_resolution.py`
- `switchyard-api/tests/test_proxy.py`
- `switchyard-api/tests/test_errors.py`

Implementation must follow the PLAN order. Phase 1 (`ReconcileResult`,
`_ensure_health_task()`, `_handle_running()` semantics, `load_model()`
updates) is complete. Phase 2 (bootstrap normalization) is next.

Key decisions to preserve:

- Switchyard `running` means startup health has succeeded.
- Docker `running` alone maps to Switchyard `loading` during adoption.
- Existing `loading` state is preserved and should get a missing health task.
- Existing `error` state is terminal until user action; do not restart health
  polling for it.
- `load_model()` treats Docker-unreachable reconciliation as fatal and must
  not start a container.
- Read routes preserve in-memory state when Docker is unreachable and expose
  diagnostics instead of clearing state blindly.
- Docker-unreachable unload should return structured 500 and preserve state
  if an in-memory deployment cannot be stopped.
- `health_timeout_seconds` is first-class Switchyard config, not a vLLM flag
  and not part of `runtime_args`.
- Continuous post-`running` health monitoring is out of scope.

Quality gates from `switchyard-api/`:

```bash
uv run pytest
uv run ruff check src tests --fix
uv run mypy src/switchyard
```
