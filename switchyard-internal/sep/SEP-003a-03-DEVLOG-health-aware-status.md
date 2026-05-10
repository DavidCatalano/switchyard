# DEVLOG

**SEP**: SEP-003a
**Title**: Health-Aware Deployment Status Semantics

---

## 2026-05-10 — Initiation and Planning Review

PRD, PLAN, and DEVLOG drafted from existing CONTEXT research
(`SEP-003a-04-CONTEXT-health-aware-status.md`). Core problem: reconciliation
and bootstrap adopt Docker-running containers as `running` without verifying
backend health, violating the invariant that Switchyard `running` means
serving-ready.

**Key design decisions from planning review**:

- **Error is terminal**: Existing `error` state is preserved during
  reconciliation and does not trigger new health polling. Operator must
  explicitly unload + reload. Background health polling will not silently
  flip error back to running.
- **Load request rejections**: `running` → "already running", `loading` →
  "already starting", `error` → "in error state, unload first". No implicit
  restart to avoid race conditions.
- **`_ensure_health_task()` with event-loop safety**: `reconcile()` is sync
  and called from tests. The helper checks `asyncio.get_running_loop()`
  before creating tasks.
- **`ReconcileResult` observation shape**: Routes need `container_status`
  data path. `DeploymentInfo` alone is insufficient.
- **`OrphanDetector` stays as Docker discovery**: `bootstrap()` normalizes
  orphan `DeploymentInfo(status="running")` into `loading` + health poll.
  Keeps separation of concerns.
- **Configurable health timeout**: Deployment-level
  `health_timeout_seconds` override included in scope to address Qwen
  cold-start scenarios (300s default may be too short).
- **`container_status` on all endpoints**: List, detail, and status
  endpoints all expose `container_status` for consistency.
- **All decision log entries moved to Pending**: Awaiting user agreement
  before implementation begins.

**Artifacts**:
- `SEP-003a-01-PRD-health-aware-status.md` — statement of need and success criteria
- `SEP-003a-02-PLAN-health-aware-status.md` — 6-phase implementation plan
- `SEP-003a-03-DEVLOG-health-aware-status.md` — this file
- `SEP-003a-04-CONTEXT-health-aware-status.md` — research (pre-existing)

---

## Cold Start / Handoff

- PRD: `SEP-003a-01-PRD-health-aware-status.md`
- PLAN: `SEP-003a-02-PLAN-health-aware-status.md`
- CONTEXT: `SEP-003a-04-CONTEXT-health-aware-status.md`
- Key code: `switchyard-api/src/switchyard/core/lifecycle.py`
  (`_handle_running`, `bootstrap`, `_health_poll`)
- Key code: `switchyard-api/src/switchyard/core/orphan.py`
  (`OrphanDetector.scan()` — Docker discovery, unchanged)
- Key code: `switchyard-api/src/switchyard/app.py`
  (API routes — reconcile call sites, response shapes)
