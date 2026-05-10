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

---

## Cold Start / Handoff

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

Implementation must follow the PLAN order. Do not begin with API response
fields; those depend on `ReconcileResult`. The first coding slice should be
the reconciliation result shape, then `_ensure_health_task()`, then
`_handle_running()` semantics.

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
