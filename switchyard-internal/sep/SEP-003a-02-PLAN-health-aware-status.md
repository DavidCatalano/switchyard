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
   containers as `loading` instead of `running`, and ensures a background
   health task exists for newly adopted and existing `loading` deployments.

2. **Reconciliation coercion**: `_handle_running()` preserves existing
   in-memory status instead of coercing `loading`/`error` to `running`.
   Existing `error` state does not trigger new health polling (error is
   terminal until user action).

3. **Bootstrap orphan adoption**: `OrphanDetector.scan()` continues to
   return Docker-level observations (`status="running"`). `bootstrap()`
   normalizes each adopted orphan into `DeploymentInfo(status="loading")`,
   populates metadata from resolved deployment config, and starts a health
   poll.

4. **Health timeout override**: Add a deployment-level
   `health_timeout_seconds` via first-class config cascade (not inside
   `runtime_args`), allowing large models to avoid premature `error`.

API responses gain `container_status` to surface Docker state separately
from Switchyard serving state. Detail and status responses also include
`docker_error` when present; the list endpoint remains compact and omits
`docker_error`. The `health` field is derived from deployment status.
Reconciliation returns a structured `ReconcileResult` carrying both the
`DeploymentInfo` and the observed Docker state. API routes stop swallowing
broad `Exception` around reconciliation.

This follows **Option A** from the CONTEXT document: conservative adoption
with background health polling, no synchronous health probes on request
paths.

---

## Testing Guidance

**Health-poll tests must set `manager._health_interval = 0.01`** (not the
default 2.0s) and use short `manager._health_timeout` values where needed.
Do not rely on the default 2s interval in tests — it makes tests slow and
potentially flaky. All new health-poll tests must set this explicitly.

---

## Implementation Order

Qwen should implement in this order. Do not start with API response fields;
they depend on the new reconciliation result shape.

1. Add `ReconcileResult` and update tests/call sites to use `result.info`.
2. Add `_ensure_health_task()`.
3. Change `_handle_running()` semantics to adopt as `loading`, preserve
   existing status, and restart missing health tasks for existing `loading`.
4. Update `load_model()` for `ReconcileResult` and terminal `error` policy.
5. Update bootstrap normalization and metadata.
6. Add first-class `health_timeout_seconds` resolution.
7. Update API routes and response shapes.
8. Add route-matrix and cross-cutting integration tests.

Continuous post-`running` health monitoring is out of scope. SEP-003a fixes
startup/adoption readiness semantics; health polling still stops once startup
reaches `running`.

---

## Task Breakdown

### Phase 1: Core Invariant — Health-Aware Reconciliation

**Goal**: Reconciliation adopts Docker-running containers as `loading`,
preserves existing state, returns structured observations, ensures health
tasks, and handles async task creation safely.

#### Tasks
- [x] **T1.1**: Write failing tests for health-aware reconciliation
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
  - `test_reconcile_existing_loading_starts_missing_health_task`: Verify
    an existing `loading` deployment with no health task gets one started
    (e.g. after sync reconcile skipped task creation due to no event loop)
  - `test_ensure_health_task_no_event_loop`: Verify
    `_ensure_health_task()` does not crash when no event loop is running
    (sync test context) — logs a warning and skips

- [x] **T1.2**: Refactor `_handle_running()` in `lifecycle.py`
  - Change adoption status from `"running"` to `"loading"`
  - Remove the coercion block that updates `loading`/`error` to `running`
  - Call `_ensure_health_task()` for:
    - Newly adopted containers (status is now `loading`)
    - Existing `loading` deployments with no active health task
  - Do NOT start a task for existing `running` or existing `error`
    (error is terminal)

- [x] **T1.3**: Add `_ensure_health_task()` helper
  - New method on `LifecycleManager`:
    ```python
    def _ensure_health_task(self, deployment_name: str, info: DeploymentInfo) -> None:
        """Start a health poll task if one doesn't already exist.

        Safe to call from sync context: if no event loop is running,
        logs a warning and returns without creating a task.
        """
    ```
  - Checks `self._health_tasks` for an existing non-cancelled task
  - If none exists and event loop is running (`asyncio.get_running_loop()`):
    creates task via `asyncio.create_task(self._health_poll(name, info))`
  - If no event loop is running (sync test context): logs a warning,
    returns without creating a task
  - If task already exists: no-op

- [x] **T1.4**: Add `ReconcileResult` dataclass
  - New dataclass in `switchyard-api/src/switchyard/core/lifecycle.py`:
    ```python
    @dataclass
    class ReconcileResult:
        """Structured result from reconcile().

        Attributes:
            info: DeploymentInfo if state exists (adopted or preserved),
                  None if container was missing/exited/dead.
            container_status: Docker container observation. Use raw Docker
                status strings when available (e.g. "running", "exited",
                "dead", "created", "paused", "restarting"). Use
                Switchyard-added "missing" when no managed container exists
                and "unknown" when Docker is unreachable.
            docker_error: Error message if Docker was unreachable,
                None otherwise.
        """
        info: DeploymentInfo | None = None
        container_status: str = "unknown"
        docker_error: str | None = None
    ```
  - `reconcile()` returns `ReconcileResult` instead of
    `DeploymentInfo | None`
  - On Docker unreachable: returns
    `ReconcileResult(info=<in-memory state if any>, container_status="unknown", docker_error="...")`
    — preserves in-memory state, does not clear it
  - On Docker reachable + container missing/exited/dead: behavior
    unchanged (clears state), but returns `ReconcileResult` with the
    observed `container_status`
  - `load_model()` inspects `result.docker_error`: if non-None, raises
    `RuntimeError` before attempting to start a container (fail fast —
    prevents duplicate containers when Docker is unreachable)
  - Read/status routes use `container_status` and `docker_error` for
    graceful degraded responses

- [x] **T1.5**: Update `load_model()` to handle reconciliation results
  - Inspects `ReconcileResult` from `reconcile()`:
    - `result.docker_error is not None` → raise `RuntimeError` (fail
      fast; Docker unreachable means we can't safely start)
    - `result.info is None` → proceed with load (Docker is reachable
      and no managed container exists, so starting is safe)
    - `result.info.status == "running"` → raise `ValueError`
      ("already running" with container ID)
    - `result.info.status == "loading"` → raise `ValueError`
      ("already starting, not yet ready")
    - `result.info.status == "error"` → raise `ValueError`
      ("in error state, unload first")
  - Do NOT attempt implicit stop/restart for error state. Return
    `ValueError` with clear text. Existing `unload` path remains the
    explicit recovery operation.
  - Write tests for each branch.

- [x] **T1.6**: Update existing reconciliation tests to reflect new
  semantics
  - All tests that call `reconcile()` must switch from direct
    `result.status` / `result.port` access to `result.info.status` /
    `result.info.port` after asserting `result.info is not None`
  - `test_reconcile_running_container_preserves_state`: change expected
    status from `"running"` to `"loading"` (coercion removed)
  - `test_reconcile_running_container_adopts_when_missing`: change
    expected status from `"running"` to `"loading"`
  - `test_reconcile_adopts_running_container_on_load` (integration):
    update assertions
  - API tests likely needing updates include
    `test_list_adopts_running_container` and
    `test_detail_adopts_running_container`
  - Any other tests asserting `"running"` from reconciliation
  - All health-poll tests: set `manager._health_interval = 0.01`

#### Files Modified
- `switchyard-api/src/switchyard/core/lifecycle.py`
- `switchyard-api/tests/test_lifecycle.py`

---

### Phase 2: Bootstrap Health Polling for Orphans

**Goal**: Bootstrap adopts orphans as `loading` with health polling and
proper metadata, matching the reconciliation behavior. `OrphanDetector`
stays unchanged.

#### Tasks
- [ ] **T2.0**: Verify bootstrap/orphan production path before changing it
  - Confirm whether `LifecycleManager.bootstrap()` is invoked by
    production startup today.
  - Confirm that `OrphanDetector` still matches current SEP-003
    containers. Current vLLM containers are named `switchyard-{deployment}`
    and identified by labels, while `OrphanDetector` historically matches
    name patterns like `{model}-{backend}-{instance}`.
  - If bootstrap/orphan adoption is legacy or not wired into production,
    keep this phase limited to preserving existing tests and aligning
    semantics. Do not expand scope into a full orphan redesign.

- [ ] **T2.1**: Write failing test
  - `test_bootstrap_adopt_orphans_as_loading_with_health`: Verify
    orphans are adopted as `loading`, have a health task, and metadata
    is populated (backend_host, backend_scheme, served_model_name)

- [ ] **T2.2**: Update `bootstrap()` in `lifecycle.py`
  - Keep `OrphanDetector` unchanged — it remains low-level Docker
    discovery returning `DeploymentInfo(status="running")`
  - In the orphan adoption loop:
    1. Resolve the deployment config for the orphan's model name
    2. Create a new `DeploymentInfo(status="loading")` (overwrite status
       from orphan's `"running"`)
    3. Populate metadata using the same logic as reconciliation
       adoption — reuse `_build_adopted_metadata(resolved)` or equivalent
       to set `backend_host`, `backend_scheme`, and
       `served_model_name` from resolved runtime args
    4. Call `self.state.add(orphan_loading)`
    5. Call `self._ensure_health_task(model_name, orphan_loading)`

- [ ] **T2.3**: Update existing bootstrap tests
  - `test_bootstrap_adopt_orphans`: change expected status from
    `"running"` to `"loading"`
  - `test_bootstrap_adopted_skip_auto_start`: same change
  - Add assertion for health task presence

- [ ] **T2.4**: Verify `test_orphan.py` still passes
  - `OrphanDetector.scan()` still creates `status="running"` objects —
    this is Docker truth at discovery time. No semantic change needed.
  - Verify no tests depend on the adopted status being Switchyard
    serving state.

#### Files Modified
- `switchyard-api/src/switchyard/core/lifecycle.py`
- `switchyard-api/tests/test_bootstrap.py`
- `switchyard-api/tests/test_orphan.py` (verification, likely no changes)

---

### Phase 3: API Response — Diagnostic Fields and Error Handling

**Goal**: Surface Docker `container_status` and `docker_error` in API
responses, stop swallowing broad `Exception`, and handle
`ReconcileResult` properly.

#### Tasks
- [ ] **T3.1**: Replace broad `except Exception: pass` in API routes
  - In `switchyard-api/src/switchyard/app.py`, find all reconciliation
    blocks that do `try: manager.reconcile(...) except Exception: pass`
  - Replace with explicit `ReconcileResult` handling:
    - Config/resolve errors (e.g. `resolve_deployment()` fails) should
      still surface as exceptions — these are programming/config bugs
    - Docker connectivity issues are represented by
      `result.docker_error` / `result.container_status == "unknown"`
  - This makes config bugs and programming errors visible again

- [ ] **T3.2**: Update `GET /api/deployments` response shape
  - Add `container_status` field: from `ReconcileResult.container_status`
  - Add `health` field: derive from status
    - `"running"` -> `"healthy"`, `"loading"` -> `"starting"`,
      `"error"` -> `"unhealthy"`, `"stopped"` -> `"unknown"`
  - Do NOT include `docker_error` (list endpoint should be compact;
    detail/status provide that)
  - Write integration test verifying response shape

- [ ] **T3.3**: Update `GET /api/deployments/{deployment}/status` response
  - Replace hardcoded `"health": "unknown"` with derived value (same
    mapping as T3.2)
  - Add `container_status` field from `ReconcileResult`
  - Include `docker_error` when present (non-None) — this endpoint is
    for operator diagnosis
  - Write test verifying response shape for running, loading, and
    Docker-unreachable states

- [ ] **T3.4**: Update `GET /api/deployments/{deployment}` (detail)
  - Add `container_status` field for consistency with list and status
    endpoints
  - Include `docker_error` when present (non-None)
  - Write test verifying response shape

- [ ] **T3.5**: Update `POST /api/deployments/{deployment}/load`
  - With `ReconcileResult`, `load_model()` now properly raises
    `RuntimeError` on Docker unreachable. Verify the route handler
    returns structured 500 (already does via `except RuntimeError`).
  - Write test: Docker unreachable during load returns 500, does not
    start a duplicate container

#### Files Modified
- `switchyard-api/src/switchyard/app.py`
- `switchyard-api/tests/test_api.py`

---

### Phase 4: Configurable Health Timeout

**Goal**: Allow deployments to override the default 300s health timeout,
preventing premature `error` on large-model cold starts. Config cascade
uses first-class Switchyard fields, not `runtime_args`.

#### Tasks
- [ ] **T4.1**: Add `health_timeout_seconds` to config schema
  - `switchyard-api/src/switchyard/config/models.py`:
    - `DeploymentConfig`: add `health_timeout_seconds: float | None = None`
    - `RuntimeConfig`: add `health_timeout_seconds: float | None = None`
    - `ResolvedDeployment`: add `health_timeout_seconds: float` (final
      resolved value, never None)
    - Do NOT add `health_timeout_seconds` to `VLLMRuntimeConfig`
  - Cascade in `switchyard-api/src/switchyard/config/loader.py`:
    ```
    ResolvedDeployment.health_timeout_seconds =
        deployment.health_timeout_seconds          # deployment override
      or runtime.health_timeout_seconds            # runtime default
      or 300.0                                     # hard default
    ```
  - NOT placed inside `runtime.defaults` or `runtime_args` (those become
    vLLM CLI args and would be passed to vLLM incorrectly)

- [ ] **T4.2**: Use resolved timeout in `_health_poll()`
  - `LifecycleManager._health_poll()` uses
    `info.metadata.get("health_timeout_seconds", self._health_timeout)`
  - Pass the resolved timeout into metadata during `load_model()` and
    adoption/bootstrap normalization
  - Use metadata as the chosen transport for this SEP. Do not add a
    second `_health_poll()` parameter unless a later design explicitly
    revisits this.
  - Tests must verify `health_timeout_seconds` metadata exists on all
    three paths: fresh load, reconciliation adoption, and bootstrap
    normalization.

- [ ] **T4.3**: Write tests in `test_config_resolution.py`
  - Default 300s is used when neither deployment nor runtime sets it
  - Runtime-level default is used when deployment doesn't set it
  - Deployment-level override takes precedence over runtime default
  - Short timeout triggers `error` correctly in lifecycle
  - Long timeout keeps `loading` beyond the default

#### Files Modified
- `switchyard-api/src/switchyard/core/lifecycle.py`
- `switchyard-api/src/switchyard/config/models.py`
- `switchyard-api/src/switchyard/config/loader.py`
- `switchyard-api/tests/test_lifecycle.py`
- `switchyard-api/tests/test_config_resolution.py`

---

### Phase 5: Integration and Route-Matrix Tests

**Goal**: End-to-end validation across all API routes, with specific
Docker-unreachable behavior per route.

#### Tasks
- [ ] **T5.1**: Integration test — reconcile adopts as `loading`, health
  poll transitions to `running`
  - Mock Docker factory returns running container
  - Call reconcile, verify adoption as `loading` and health task exists
  - Mock adapter health to return `"running"`
  - Wait for health poll, verify status transitions to `running`
  - Set `manager._health_interval = 0.01`

- [ ] **T5.2**: Integration test — reconcile adopts as `loading`, health
  poll transitions to `error`
  - Same setup, adapter health returns `"error"`
  - Verify status transitions to `error` after timeout
  - Set `manager._health_interval = 0.01`

- [ ] **T5.3**: Integration test — `/v1/models` excludes `loading`
  deployments
  - Only `running` deployments appear; `loading` and `error` do not

- [ ] **T5.4**: Integration test — `POST /v1/chat/completions` rejects
  `loading` deployments
  - Deployment in `loading` state returns 400 ("not running")

- [ ] **T5.5**: Edge case — load request for `error` deployment
  - Deployment in `error` state, user triggers load
  - Verify rejected with "in error state, unload first"
  - No implicit stop/restart

- [ ] **T5.6**: Docker-unreachable route matrix — verify each route
  handles `ReconcileResult.docker_error` correctly:

  | Route | Expected Behavior |
  |-------|------------------|
  | `GET /api/deployments` | Preserve in-memory state, `container_status: "unknown"` |
  | `GET /api/deployments/{dep}` | Preserve in-memory state, `container_status: "unknown"`, include `docker_error` |
  | `GET /api/deployments/{dep}/status` | Preserve in-memory state, `container_status: "unknown"`, include `docker_error` |
  | `POST /api/deployments/{dep}/load` | Return structured 500; do not start container |
  | `POST /api/deployments/{dep}/unload` | If Docker is unreachable while stopping an in-memory deployment, return structured 500 and preserve state. Do not clear state unless stop succeeds or container is confirmed gone. |
  | `GET /v1/models` | Preserve current status gate; Docker error must not clear state |
  | `POST /v1/chat/completions` | Preserve current status gate; Docker error must not clear state |

  Write one test per route verifying the specific behavior.
  - Docker unreachable during read/proxy reconciliation must not clear or
    downgrade existing in-memory `running` state. `/v1/chat/completions`
    may still proxy if Switchyard already believed the deployment was
    running; Docker reachability and backend HTTP reachability are
    different failure domains.

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
   `ReconcileResult`) — blocks everything; the core invariant must be
   correct first
2. **T1.5/T1.6** (load_model changes + existing test updates) — depends
   on T1.4 (`ReconcileResult`)
3. **T2.1/T2.2** (bootstrap fix) — depends on T1.3
   (`_ensure_health_task`)
4. **T3.x** (API response fields + error handling) — depends on T1.4
   (`ReconcileResult`)
5. **T4.x** (configurable timeout) — depends on T1.2 (same
   `_health_poll` mechanism), can proceed in parallel with T3
6. **T5.x** (integration tests) — depends on T1-T4 all complete
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
| Start health task for newly adopted AND existing loading without task | Ensures health polling resumes after sync reconcile skips task creation. Existing `running` and `error` are excluded. | Pending |
| `error` is terminal until user action | `error` signals something is wrong and needs attention, not a transient startup phase. Background health polling should not silently flip it back to `running`. | Pending |
| Reject `loading` deployments on load request | Container is already running in Docker; restarting would be disruptive. User should wait or unload first. | Pending |
| Reject `error` deployments on load request (require unload first) | Avoid race conditions and implicit restart. Operator must explicitly unload before reloading. No hidden restart logic. | Pending |
| Add `container_status` to all API responses; `docker_error` to detail/status only | Operators need both axes (container state + serving state). List endpoint stays compact; detail/status provide full diagnostics. | Pending |
| Derive `health` from deployment status | Simpler than introducing a separate health state machine; sufficient for current needs. | Pending |
| No synchronous health probes on list/status routes | Avoid request-path latency spikes; background polling is sufficient. | Pending |
| `ReconcileResult.docker_error` is fatal for `load_model()` | Docker unreachable means we can't safely start a container. Fail fast to prevent duplicate containers. | Pending |
| `ReconcileResult.docker_error` is informational for read routes | Read routes preserve state and surface the error for diagnosis. Do not clear state blindly. | Pending |
| Add deployment-level `health_timeout_seconds` via first-class config | Qwen 27B on trainbox may need more than 300s for cold start. Not in `runtime_args` (those become vLLM CLI flags). | Pending |
| `OrphanDetector` stays as Docker discovery; `bootstrap()` normalizes | Keep separation of concerns: `OrphanDetector` reports Docker truth, `bootstrap()` applies Switchyard lifecycle semantics and populates metadata. | Pending |
| `_ensure_health_task()` with event-loop safety | `reconcile()` is sync and called from tests; must not crash when no event loop is running. | Pending |
| Remove broad `except Exception: pass` in API reconciliation | Broad exception swallowing hides config bugs, programming errors, and Docker state problems. | Pending |
| "Running means healthy" applies to startup only | Health polling stops once status becomes `running`. Continuous post-running monitoring is out of scope. | Pending |
| Docker-unreachable unload preserves state and returns 500 | Clearing state after a failed Docker stop would recreate the stale-state problem this SEP is trying to avoid. | Pending |
| No changes to `/v1/models` | Already correctly gates on `status == "running"`; fix makes the gate more truthful. | Pending |

---

## Risk Mitigation

### Risk 1: Health task lifecycle — duplicate or orphaned tasks
- **Mitigation**: `_ensure_health_task()` checks `self._health_tasks`
  before creating a task. If one exists and is not cancelled, it's a
  no-op. Idempotent.
- **Owner**: Implementation (T1.3)
- **Timeline**: Phase 1

### Risk 2: After API restart, `running` deployments briefly show `loading`
- **Mitigation**: Health poll interval is 2 seconds. Most deployments
  transition within one poll cycle. Conservative is better than
  overstating readiness.
- **Owner**: Product decision
- **Timeline**: Design decision

### Risk 3: Regression in `/v1/models` including non-ready deployments
- **Mitigation**: Integration test T5.3 explicitly validates the gate.
  The fix makes the gate *more* restrictive (only `running` appears,
  which now truly means healthy).
- **Owner**: Implementation (T5.3)
- **Timeline**: Phase 5

### Risk 4: `_ensure_health_task()` in sync reconciliation context
- **Mitigation**: Checks `asyncio.get_running_loop()` before
  `asyncio.create_task()`. If no loop is running (sync test context),
  logs a warning and skips. The health poll resumes on the next async
  reconcile call from a route handler, which will find the existing
  `loading` deployment and call `_ensure_health_task()` again (this time
  with a running loop).
- **Owner**: Implementation (T1.3)
- **Timeline**: Phase 1

### Risk 5: `error` terminal state blocks recovery
- **Mitigation**: Operator can always `unload` then `load` to restart.
  The error state is a signal for human attention, not an automatic
  recovery gate. This is intentional and documented.
- **Owner**: Product decision
- **Timeline**: Design decision

### Risk 6: Health-poll tests become slow or flaky
- **Mitigation**: All tests set `manager._health_interval = 0.01` and
  use short `manager._health_timeout`. Never rely on the default 2s
  interval in tests.
- **Owner**: Implementation (all phases)
- **Timeline**: Throughout

### Risk 7: Bootstrap/orphan path is legacy or not wired
- **Mitigation**: T2.0 verifies the production path before implementation.
  If orphan detection is legacy-only, changes stay limited to semantic
  compatibility and tests; no orphan redesign happens in SEP-003a.
- **Owner**: Implementation (T2.0)
- **Timeline**: Phase 2

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
- [ ] `load_model()` treats `docker_error` as fatal (T1.5)
- [ ] Read routes handle `docker_error` gracefully (T3.x)
- [ ] Broad `except Exception: pass` removed from API routes (T3.1)
- [ ] Bootstrap normalizes orphan metadata (T2.1, T2.2)
- [ ] Bootstrap/orphan production path verified before changes (T2.0)
- [ ] Integration tests validate full adoption -> health poll -> running flow (T5.1)
- [ ] Integration tests validate adoption -> health poll -> error flow (T5.2)
- [ ] `/v1/models` excludes non-`running` deployments (T5.3)
- [ ] `/v1/chat/completions` rejects non-`running` deployments (T5.4)
- [ ] `error` deployments rejected on load — require unload first (T5.5)
- [ ] Docker-unreachable route matrix verified per route (T5.6)
- [ ] Docker-unreachable unload returns 500 and preserves state (T5.6)
- [ ] Configurable health timeout works at deployment level (T4.1–T4.3)
- [ ] All health-poll tests use `_health_interval = 0.01` (all phases)
- [ ] All quality gates pass (ruff, mypy, pytest)
- [ ] No regressions in existing functionality

---

This plan serves as the single source of truth during implementation.
Update status as work progresses.
