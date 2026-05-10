# Project Plan (PLAN)

**Title**: Health-Aware Deployment Status Semantics
**ID**: SEP-003a-02-PLAN-health-aware-status
**Status**: Draft
**Date**: 2026-05-10
**PRD**: SEP-003a-01-PRD-health-aware-status.md

---

## Implementation Approach

The core invariant to restore:

> Switchyard `running` means backend health has succeeded.

Four areas currently violate this by setting `DeploymentInfo.status =
"running"` from Docker container state alone. The fix addresses each:

1. **Reconciliation adoption**: `_handle_running()` adopts Docker-running
   containers as `loading` instead of `running`, and starts a background
   health task so the deployment naturally transitions to `running` or
   `error`.

2. **Reconciliation coercion**: `_handle_running()` preserves existing
   in-memory status instead of coercing `loading`/`error` to `running`.
   Existing `error` state does not trigger new health polling (error is
   terminal until user action).

3. **Bootstrap orphan adoption**: `OrphanDetector.scan()` continues to
   return Docker-level observations (`status="running"`). `bootstrap()`
   normalizes each adopted orphan into `DeploymentInfo(status="loading")`
   and starts a health poll, keeping `OrphanDetector` as low-level Docker
   discovery.

4. **Health timeout override**: Add a deployment-level
   `health_timeout_seconds` that overrides the default 300s, allowing
   large models to avoid premature `error` on cold starts.

API responses gain `container_status` to surface Docker state separately
from Switchyard serving state. The `health` field in status responses is
derived from deployment status rather than hardcoded to `"unknown"`.

Reconciliation returns a structured `ReconcileResult` carrying both the
`DeploymentInfo` and the observed Docker state, giving routes the data
path needed for `container_status`.

This follows **Option A** from the CONTEXT document: conservative adoption
with background health polling, no synchronous health probes on request
paths.

---

## Task Breakdown

### Phase 1: Core Invariant — Health-Aware Reconciliation

**Goal**: Reconciliation adopts Docker-running containers as `loading`,
preserves existing state, returns structured observations, and handles
async health tasks safely.

#### Tasks
- [ ] **T1.1**: Write failing tests for health-aware reconciliation
  - `test_reconcile_running_container_adopts_as_loading`: Verify adoption
    creates `status="loading"` instead of `running`
  - `test_reconcile_running_container_preserves_loading`: Verify existing
    `loading` state is not coerced to `running`
  - `test_reconcile_running_container_preserves_error`: Verify existing
    `error` state is not coerced to `running`
  - `test_reconcile_running_container_preserves_running`: Verify existing
    `running` state is preserved (no change)
  - `test_reconcile_adopted_starts_health_task`: Verify health polling
    is started for newly adopted containers
  - `test_reconcile_existing_no_duplicate_health_task`: Verify no
    duplicate health task when one already exists
  - `test_ensure_health_task_no_event_loop`: Verify `_ensure_health_task()`
    does not crash when no event loop is running (sync test context)

- [ ] **T1.2**: Refactor `_handle_running()` in `lifecycle.py`
  - Change adoption status from `"running"` to `"loading"`
  - Remove the coercion block that updates `loading`/`error` to `running`
  - After adopting state (only for newly adopted containers), call
    `_ensure_health_task()` to start health polling if no task exists
  - Existing `error` state is preserved and does NOT trigger a new health
    task (error is terminal)

- [ ] **T1.3**: Add `_ensure_health_task()` helper
  - New method on `LifecycleManager` that safely starts or verifies a
    health poll task:
    - Checks `self._health_tasks` for an existing non-cancelled task
    - If none exists and event loop is running: creates task via
      `asyncio.create_task(self._health_poll(name, info))`
    - If no event loop is running (sync test context): logs a warning,
      skips task creation
    - If task already exists: no-op
  - Use this from `_handle_running()` and `bootstrap()`

- [ ] **T1.4**: Add `ReconcileResult` observation shape
  - New dataclass or TypedDict:
    ```python
    ReconcileResult = {
      "info": DeploymentInfo | None,
      "container_status": str,  # "running", "missing", "exited", "dead", "unknown"
      "docker_error": str | None,
    }
    ```
  - `reconcile()` returns `ReconcileResult` instead of
    `DeploymentInfo | None`
  - API routes use `container_status` and `docker_error` for response
    fields and graceful degradation
  - Update all call sites (`load_model`, all API routes)

- [ ] **T1.5**: Update `load_model()` to handle `loading` reconciliation
  results
  - Current logic: if `reconciled is not None`, raise `ValueError`
    ("already running"). After T1.2, reconciled can be `loading`.
  - New logic:
    - `running` → raise "already running" with container ID
    - `loading` → raise "already starting, not yet ready"
    - `error` → raise "in error state, unload first"
  - Write tests for each branch.

- [ ] **T1.6**: Update existing reconciliation tests to reflect new
  semantics
  - `test_reconcile_running_container_preserves_state`: change expected
    status from `"running"` to `"loading"` (coercion removed)
  - `test_reconcile_running_container_adopts_when_missing`: change
    expected status from `"running"` to `"loading"`
  - `test_reconcile_adopts_running_container_on_load` (integration):
    update assertions
  - Any other tests asserting `"running"` from reconciliation

#### Files Modified
- `switchyard-api/src/switchyard/core/lifecycle.py`
- `switchyard-api/tests/test_lifecycle.py`
- `switchyard-api/tests/test_api.py`

---

### Phase 2: Bootstrap Health Polling for Orphans

**Goal**: Bootstrap adopts orphans as `loading` with health polling,
matching the reconciliation behavior. `OrphanDetector` stays unchanged.

#### Tasks
- [ ] **T2.1**: Write failing test
  - `test_bootstrap_adopt_orphans_as_loading_with_health`: Verify
    orphans are adopted as `loading` and a health task is created

- [ ] **T2.2**: Update `bootstrap()` in `lifecycle.py`
  - Keep `OrphanDetector` unchanged — it remains low-level Docker
    discovery returning `DeploymentInfo(status="running")`
  - In the orphan adoption loop: for each adopted orphan, create a new
    `DeploymentInfo(status="loading")` before calling `self.state.add()`
  - After adding each orphan to state: call
    `self._ensure_health_task(model_name, orphan_loading)` to start
    health polling
  - Store the task in `self._health_tasks`

- [ ] **T2.3**: Update existing bootstrap tests
  - `test_bootstrap_adopt_orphans`: change expected status from
    `"running"` to `"loading"`
  - `test_bootstrap_adopted_skip_auto_start`: same change
  - Add assertion for health task presence

- [ ] **T2.4**: Verify `test_orphan.py` still passes
  - `OrphanDetector.scan()` still creates `status="running"` objects —
    this is Docker truth at discovery time. No semantic change needed
    in `OrphanDetector` itself.
  - Verify no tests in `test_orphan.py` depend on the adopted status
    being used as Switchyard serving state.

#### Files Modified
- `switchyard-api/src/switchyard/core/lifecycle.py`
- `switchyard-api/tests/test_bootstrap.py`
- `switchyard-api/tests/test_orphan.py` (verification, likely no changes)

---

### Phase 3: API Response — Diagnostic Fields

**Goal**: Surface Docker `container_status` and meaningful `health` in
all API responses, distinguishing container state from serving state.

#### Tasks
- [ ] **T3.1**: Update `GET /api/deployments` response shape
  - Add `container_status` field: from `ReconcileResult.container_status`
  - Add `health` field: derive from status
    - `"running"` -> `"healthy"`, `"loading"` -> `"starting"`,
      `"error"` -> `"unhealthy"`, `"stopped"` -> `"unknown"`
  - Write integration test verifying response shape

- [ ] **T3.2**: Update `GET /api/deployments/{deployment}/status` response
  - Replace hardcoded `"health": "unknown"` with derived value (same
    mapping as T3.1)
  - Add `container_status` field from `ReconcileResult`
  - Write test verifying response shape for running and loading states

- [ ] **T3.3**: Update `GET /api/deployments/{deployment}` (detail)
  - Add compact `container_status` field for consistency with list and
    status endpoints. Callers may expect the same diagnostic truth
    across all three endpoints.
  - Write test verifying response shape.

#### Files Modified
- `switchyard-api/src/switchyard/app.py`
- `switchyard-api/tests/test_api.py`

---

### Phase 4: Configurable Health Timeout

**Goal**: Allow deployments to override the default 300s health timeout,
preventing premature `error` on large-model cold starts.

#### Tasks
- [ ] **T4.1**: Add `health_timeout_seconds` to `ResolvedDeployment` as
  an optional deployment-level override. If absent, falls back to
  `_DEFAULT_HEALTH_TIMEOUT` (300s).
- [ ] **T4.2**: Pass the resolved timeout into `_health_poll()` so it
  uses the per-deployment value instead of the manager-level default.
- [ ] **T4.3**: Write tests verifying:
  - Default 300s is used when no override is set
  - Deployment override takes precedence
  - Short timeout triggers `error` correctly
  - Long timeout keeps `loading` beyond the default

#### Files Modified
- `switchyard-api/src/switchyard/core/lifecycle.py`
- `switchyard-api/src/switchyard/config/models.py` (if needed)
- `switchyard-api/tests/test_lifecycle.py`

---

### Phase 5: Integration and Cross-Cutting Tests

**Goal**: End-to-end validation that health-aware adoption works through
the full request lifecycle.

#### Tasks
- [ ] **T5.1**: Integration test — reconcile adopts as `loading`, health
  poll transitions to `running`
  - Mock Docker factory returns running container
  - Call reconcile, verify adoption as `loading` and health task exists
  - Mock adapter health to return `"running"`
  - Wait for health poll, verify status transitions to `running`

- [ ] **T5.2**: Integration test — reconcile adopts as `loading`, health
  poll transitions to `error`
  - Same setup, but adapter health returns `"error"`
  - Verify status transitions to `error` after timeout

- [ ] **T5.3**: Integration test — `/v1/models` excludes `loading`
  deployments
  - Verify only `running` deployments appear in OpenAI model list
  - `loading` and `error` deployments must not appear

- [ ] **T5.4**: Integration test — `POST /v1/chat/completions` rejects
  `loading` deployments
  - Deployment in `loading` state should return 400 ("not running")

- [ ] **T5.5**: Edge case — load request for `error` deployment
  - Deployment in `error` state, user triggers load
  - Verify load is rejected with clear message ("in error state, unload
    first"). Operator must explicitly unload before reloading.

- [ ] **T5.6**: Edge case — reconcile with Docker unreachable
  - Docker factory raises `DockerException` during reconcile
  - Verify API routes degrade gracefully: preserve in-memory state,
    return `"container_status": "unknown"`, do not clear state blindly
  - Verify list, detail, and status endpoints all handle this correctly

#### Files Modified
- `switchyard-api/tests/test_api.py`
- `switchyard-api/tests/test_lifecycle.py`

---

### Phase 6: Final Review and Cleanup

**Goal**: Ensure all changes are consistent, tests green, docs updated.

#### Tasks
- [ ] **T6.1**: Run full test suite, verify no regressions
- [ ] **T6.2**: Run linter (`uv run ruff check src tests --fix`)
- [ ] **T6.3**: Run type checker (`uv run mypy src/switchyard`)
- [ ] **T6.4**: Update ROADMAP.md to reflect SEP-003a in progress
- [ ] **T6.5**: Mark completed tasks in this PLAN

---

## Dependencies

### Critical Path
1. **T1.2/T1.3/T1.4** (reconciliation fix + `_ensure_health_task` +
   `ReconcileResult`) — blocks everything else; the core invariant must
   be correct first
2. **T1.6** (update existing reconciliation tests) — depends on T1.2
3. **T2.1/T2.2** (bootstrap fix) — depends on T1.3 (`_ensure_health_task`)
4. **T3.1/T3.2/T3.3** (API response fields) — depends on T1.4
   (`ReconcileResult`), can proceed in parallel with T2
5. **T4.x** (configurable timeout) — depends on T1.2 (same
   `_health_poll` mechanism), can proceed in parallel with T2 and T3
6. **T5.x** (integration tests) — depends on T1, T2, T3, T4 all complete
7. **T6.x** (final review) — depends on all phases complete

### Parallel Work Streams
- T2 (bootstrap), T3 (API responses), and T4 (configurable timeout) can
  proceed concurrently after T1.2
- T5.x integration tests can be written in parallel with implementation,
  as long as the test fixtures exist

---

## Decision Log

| Decision | Reason | State |
|----------|--------|-------|
| Adopt Docker-running containers as `loading` (Option A) | Conservative, simple, preserves existing health polling model, no request-path latency | Pending |
| Preserve existing in-memory status during reconciliation | Docker state alone is insufficient to override a health-determined state | Pending |
| Start health task for newly adopted containers only | Without it, adopted `loading` state would never transition. Existing `error` does not get a new task (error is terminal). | Pending |
| `error` is terminal until user action | `error` signals something is wrong and needs attention, not a transient startup phase. Background health polling should not silently flip it back to `running`. | Pending |
| Reject `loading` deployments on load request | Container is already running in Docker; restarting would be disruptive. User should wait or unload first. | Pending |
| Reject `error` deployments on load request (require unload first) | Avoid race conditions and implicit restart. Operator must explicitly unload before reloading. | Pending |
| Add `container_status` and `health` to all API responses (list, detail, status) | Operators need both axes (container state + serving state) to diagnose issues. Consistency across endpoints avoids confusion. | Pending |
| Derive `health` from deployment status | Simpler than introducing a separate health state machine; sufficient for current needs. | Pending |
| No synchronous health probes on list/status routes | Avoid request-path latency spikes; background polling is sufficient. | Pending |
| Add deployment-level `health_timeout_seconds` override | Qwen 27B on trainbox may need more than 300s for cold start. Configurable timeout prevents premature `error`. | Pending |
| `OrphanDetector` stays as Docker discovery; `bootstrap()` normalizes | Keep the separation of concerns: `OrphanDetector` reports Docker truth, `bootstrap()` applies Switchyard lifecycle semantics. | Pending |
| Add `ReconcileResult` observation shape | Routes need `container_status` data path; `DeploymentInfo` alone is insufficient. | Pending |
| `_ensure_health_task()` with event-loop safety | `reconcile()` is sync and called from tests; must not crash when no event loop is running. | Pending |
| No changes to `/v1/models` | Already correctly gates on `status == "running"`; fix makes the gate more truthful. | Pending |

---

## Risk Mitigation

### Risk 1: Health task lifecycle — duplicate or orphaned tasks
- **Mitigation**: Before starting a new health task, check if one already
  exists in `self._health_tasks`. If it does, leave it alone. This is
  idempotent. `_ensure_health_task()` encapsulates this check.
- **Owner**: Implementation (T1.3)
- **Timeline**: Phase 1

### Risk 2: After API restart, `running` deployments briefly show `loading`
- **Mitigation**: Health poll interval is 2 seconds. Most deployments will
  transition within one poll cycle. This is acceptable tradeoff for
  correctness.
- **Owner**: Product decision
- **Timeline**: Design decision

### Risk 3: Regression in `/v1/models` including non-ready deployments
- **Mitigation**: Integration test T5.3 explicitly validates the gate.
  The fix makes the gate *more* restrictive (only `running` appears,
  which now truly means healthy).
- **Owner**: Implementation (T5.3)
- **Timeline**: Phase 5

### Risk 4: `_ensure_health_task()` in sync reconciliation context
- **Mitigation**: `_ensure_health_task()` checks `asyncio.get_running_loop()`
  before calling `asyncio.create_task()`. If no loop is running (e.g. sync
  test context), it logs a warning and skips task creation. The health poll
  will resume on the next async reconcile call from a route handler.
  Tests explicitly cover the no-loop case (T1.1).
- **Owner**: Implementation (T1.3)
- **Timeline**: Phase 1

### Risk 5: `error` terminal state blocks recovery
- **Mitigation**: Operator can always `unload` then `load` to restart.
  The error state is a signal for human attention, not an automatic
  recovery gate. This is intentional and documented.
- **Owner**: Product decision
- **Timeline**: Design decision

---

## Validation Plan

**Validation Commands**: See `AGENTS.md` Section "Quality Gates" for
complete validation command list.

```bash
cd switchyard-api
uv run pytest
uv run ruff check src tests --fix
uv run mypy src/switchyard
```

### Success Criteria Validation
- [ ] All existing tests pass with updated expectations
- [ ] New tests cover health-aware adoption (T1.1, T2.1)
- [ ] New tests verify coercion prevention (T1.1)
- [ ] New tests verify health task lifecycle (T1.1, T2.1)
- [ ] `_ensure_health_task()` handles no-event-loop safely (T1.1)
- [ ] `ReconcileResult` carries `container_status` and `docker_error` (T1.4)
- [ ] Integration tests validate full adoption -> health poll -> running flow (T5.1)
- [ ] Integration tests validate adoption -> health poll -> error flow (T5.2)
- [ ] `/v1/models` excludes non-`running` deployments (T5.3)
- [ ] `/v1/chat/completions` rejects non-`running` deployments (T5.4)
- [ ] `error` deployments are rejected on load — require unload first (T5.5)
- [ ] Docker unreachable degrades gracefully: preserves state, returns
  `container_status: "unknown"` (T5.6)
- [ ] Configurable health timeout works at deployment level (T4.1–T4.3)
- [ ] All quality gates pass (ruff, mypy, pytest)
- [ ] No regressions in existing functionality

---

This plan serves as the single source of truth during implementation.
Update status as work progresses.
