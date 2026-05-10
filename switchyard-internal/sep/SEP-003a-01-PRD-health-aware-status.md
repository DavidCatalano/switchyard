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

The invariant is broken:

> Switchyard `running` must mean backend health has succeeded.

---

## Success Criteria

1. Docker `running` containers adopted during reconciliation are recorded as
   `loading`, not `running`.
2. Existing in-memory state is preserved during reconciliation (no status
   coercion from Docker state alone).
3. Health polling is started for newly adopted containers so they naturally
   transition to `running` or `error`.
4. Existing `error` state is preserved during reconciliation and does not
   trigger new health polling (error is terminal until user action).
5. Bootstrap normalizes orphans from `OrphanDetector` into `loading` with
   health polling, keeping `OrphanDetector` as low-level Docker discovery.
6. API list, detail, and status responses surface Docker `container_status`
   separately from Switchyard serving `status`.
7. `/v1/models` continues to gate on Switchyard `status == "running"`
   (already correct; must not regress).
8. When Docker is unreachable, reconciliation preserves in-memory state and
   reports `container_status: "unknown"` rather than clearing state blindly.
9. Startup timeout is configurable as a deployment-level override over a
   runtime default, preventing premature `error` on large-model cold starts.
10. All existing tests pass with updated expectations; new tests cover
    health-aware adoption, coercion prevention, health poll lifecycle,
    and Docker-unreachable degradation.

---

## Non-Goals

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
  not auto-recover via health polling. If the backend recovers on its own,
  the operator must unload and reload. This is intentional — `error` signals
  that something is wrong and needs attention, not a transient startup phase.
