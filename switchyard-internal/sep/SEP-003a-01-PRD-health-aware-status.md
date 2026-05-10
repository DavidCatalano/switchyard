# Product Requirements Document (PRD)

**Title**: Health-Aware Deployment Status Semantics
**ID**: SEP-003a-01-PRD-health-aware-status
**Status**: Draft
**Date**: 2026-05-10
**CONTEXT**: SEP-003a-04-CONTEXT-health-aware-status.md

---

## Statement of Need

SEP-003 added request-time Docker reconciliation so Switchyard can recover
running containers after API restart and clear stale state when containers
disappear. The implementation correctly repairs process-local memory, but
exposes a semantic gap: Docker container state is treated as deployment
serving readiness.

After container start, Docker may report `running` while vLLM is still
loading weights, initializing workers, allocating GPU memory, warming caches,
or waiting for its HTTP server to become healthy. During that window,
`/api/deployments` reports `running` even though `/v1/chat/completions`
returns `503`.

This is especially visible for large vLLM deployments (e.g. Qwen 3.6 27B)
where the gap between container start and serving readiness is expected
behavior, not an edge case.

---

## Scope Clarification

**"Running means healthy" applies to startup readiness only.** SEP-003a
ensures that the transition from `loading` to `running` requires backend
health to succeed. Once a deployment is `running`, health polling stops.
Continuous post-running health monitoring is a future concern, not part of
this effort. The status `running` means "startup health succeeded," not
"currently and continuously passing health checks."

---

## Problem Statement

Three code paths incorrectly set `DeploymentInfo.status = "running"` based
solely on Docker container state:

1. **Reconciliation adoption** (`LifecycleManager._handle_running`): A
   Docker-running container missing from memory is adopted as `running`.

2. **Reconciliation coercion** (`LifecycleManager._handle_running`): An
   existing in-memory deployment with `loading` or `error` status is
   coerced to `running` when its Docker container is `running`.

3. **Bootstrap orphan adoption** (`OrphanDetector.scan` creates
   `DeploymentInfo(status="running")`; `LifecycleManager.bootstrap` adds
   it directly without normalizing to Switchyard lifecycle semantics):
   Running orphans are adopted as `running` with no health polling.

Additionally, API routes swallow broad `Exception` around reconciliation,
hiding config bugs, programming errors, and Docker state problems.

The invariant is broken:

> Switchyard `running` must mean backend health has succeeded.

---

## Success Criteria

1. Docker `running` containers adopted during reconciliation are recorded as
   `loading`, not `running`.
2. Existing in-memory state is preserved during reconciliation (no status
   coercion from Docker state alone).
3. Health polling is started for newly adopted containers AND for existing
   `loading` deployments that are missing a health task, so they naturally
   transition to `running` or `error`.
4. Existing `error` state is preserved during reconciliation and does not
   trigger new health polling (error is terminal until user action).
5. Bootstrap normalizes orphans from `OrphanDetector` into `loading` with
   health polling, keeping `OrphanDetector` as low-level Docker discovery.
   Bootstrap normalization populates metadata (backend_host, backend_scheme,
   served_model_name) from resolved deployment config.
6. Reconciliation returns a structured `ReconcileResult` with
   `container_status` and `docker_error`, giving API routes a concrete data
   path for diagnostic fields.
7. API list responses include `container_status` (but not full `docker_error`).
   API detail and status responses include both `container_status` and
   `docker_error` when present.
8. API routes stop swallowing broad `Exception` around reconciliation;
   Docker connectivity issues are represented by `ReconcileResult.docker_error`.
9. `load_model()` treats `ReconcileResult.docker_error` as fatal and raises
   `RuntimeError` before attempting to start a container.
10. `/v1/models` continues to gate on Switchyard `status == "running"`
    (already correct; must not regress).
11. Startup timeout is configurable via a first-class Switchyard config
    field with cascade: `DeploymentConfig.health_timeout_seconds` →
    `RuntimeConfig.health_timeout_seconds` → default 300s. Not placed
    inside `runtime.defaults`/`runtime_args` (those become vLLM CLI args).
12. All existing tests pass with updated expectations; new tests cover
    health-aware adoption, coercion prevention, health poll lifecycle,
    Docker-unreachable route matrix, and timeout cascade.

---

## Non-Goals

- Continuous post-running health monitoring. SEP-003a corrects startup/
  adoption readiness semantics; later diagnostics may add ongoing health
  checks.
- Logs endpoint (SEP-005).
- Dry-run or preflight endpoint (SEP-005).
- Detailed Docker command/volume/env previews (SEP-005).
- Host inventory and hardware discovery (SEP-007).
- Web UI implementation (SEP-008).
- Synchronous health probes on list/status routes (Option B from CONTEXT;
  deferred).
- Changes to `OrphanDetector.scan()` internals (kept as Docker discovery;
  normalization happens in `bootstrap()`).

---

## Risks

- After API restart, a deployment that is already healthy may briefly show
  `loading` until the next health poll succeeds (~2 seconds). This is
  acceptable: conservative is better than overstating readiness.
- Health polling for adopted containers introduces a background task during
  reconciliation. This is low-risk: the task model already exists and is
  proven.
- `error` is terminal until user action: deployments in `error` state will
  not auto-recover via health polling. Operator must explicitly unload and
  reload. This is intentional — `error` signals that something is wrong
  and needs attention, not a transient startup phase.
- Qwen 27B on trainbox may still hit the 300s default timeout without an
  explicit override. The configurable timeout addresses this, but the
  default is intentionally conservative to catch genuine failures early.
