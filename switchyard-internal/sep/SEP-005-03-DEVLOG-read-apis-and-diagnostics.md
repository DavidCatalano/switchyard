# Devlog

**Title**: Read APIs and Diagnostics
**ID**: SEP-005-03-DEVLOG-read-apis-and-diagnostics
**PRD**: TBA (SEP-005 not yet initiated)
**PLAN**: TBA

---

## Entries

- 2026-05-12 — SEP-004 merged. Created seed CONTEXT document (`SEP-005-04-CONTEXT-api-object-management.md`) and this DEVLOG for future session ramp-up.

---

## Cold Start / Handoff

### What SEP-005 Is

Expose read and diagnostic API surfaces over the object model. GitHub issue: #5.

**Scope**: list/detail views for hosts, runtimes, models, deployments; validation endpoints; dry-run capability; logs access; active model/deployment visibility; host resource details where manually configured data is sufficient.

**Planning reference**: `SEP-005-04-CONTEXT-api-object-management.md` (in `switchyard-internal/sep/`)

### Completed SEPs (for context)

| SEP | Summary |
|-----|---------|
| SEP-001 | MVP control plane: API, vLLM adapter, Docker lifecycle, health checks, OpenAI-compatible proxying |
| SEP-002 | Entity config model: `hosts`, `runtimes`, `models`, `deployments`; `.env` for process-local bootstrap |
| SEP-003 | Deployment lifecycle: deployment-resource routes, Docker reconciliation, store path resolution, cascade merges |
| SEP-003a | Health-aware status: `loading` with background polling, `ReconcileResult` with `container_status`/`docker_error`, configurable health timeout |
| SEP-004 | Runtime resolver hardening: `extra: "forbid"`, structural `switchyard_only` metadata, deep merge, `null` as wipe signal, provenance tracking, preview API (`GET /api/deployments/{deployment}/preview`), shared `_compute_common_launch_fields()` |

### Key Files to Read First

1. `README.md` — Project overview, core concepts, architecture
2. `AGENTS.md` — Development workflow, branching, commit conventions, quality gates
3. `switchyard-internal/process/PYTHON.md` — Python coding standards
4. `switchyard-internal/sep/SEP-005-04-CONTEXT-api-object-management.md` — SEP-005 planning reference
5. `switchyard-api/src/switchyard/config/models.py` — Entity models (Host, Runtime, Model, Deployment, ResolvedDeployment, VLLMRuntimeConfig)
6. `switchyard-api/src/switchyard/config/loader.py` — Config loading and resolution (`resolve_deployment`, `_merge_runtime_args`)
7. `switchyard-api/src/switchyard/app.py` — Current API routes (health, deployments, lifecycle, proxy, preview)
8. `switchyard-api/src/switchyard/adapters/vllm.py` — vLLM adapter (`build_launch_spec`, `build_preview_spec`, `_compute_common_launch_fields`)
9. `switchyard-api/src/switchyard/core/lifecycle.py` — Lifecycle manager, reconciliation, health polling
10. `switchyard-api/config.yaml` — Current config shape (hosts, runtimes, models, deployments)

### Key Decisions to Carry Forward

- `.env` owns process-local bootstrap settings. `config.yaml` owns durable managed entities.
- Docker labels are the authoritative marker for Switchyard-managed containers.
- `build_launch_spec()` is the single authoritative launch path. `build_preview_spec()` shares common fields via `_compute_common_launch_fields()`.
- Deep merge for nested dicts in cascade resolution; `null` as wipe signal.
- `extra: "forbid"` on VLLMRuntimeConfig; unknown flags go under `extra_args`.
- Preview API returns structured JSON container launch spec with sensitive field redaction.

### Development Workflow

- Track work on `sep/005` branch, never directly on `main`
- Standard track: PRD → CONTEXT → PLAN → Implementation
- Quality gates from `switchyard-api/`:
  ```bash
  uv run pytest
  uv run ruff check src tests --fix
  uv run mypy src/switchyard
  ```
- Conventional commits with scope `api` for switchyard-api/

### Starting Point

Read GitHub issue #5 and `SEP-005-04-CONTEXT-api-object-management.md`. Draft PRD following `switchyard-internal/process/DEV_TEMPLATE_PRD.md`.

### Handoff Section

This section routes readers to context; it does not replace the PRD, PLAN, or CONTEXT.

**Critical files**:
- `switchyard-internal/sep/SEP-005-04-CONTEXT-api-object-management.md` — planning reference
- `switchyard-api/src/switchyard/app.py` — current API routes (add new ones here)
- `switchyard-api/src/switchyard/config/models.py` — entity models to expose via read APIs
