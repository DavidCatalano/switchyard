# Context Document

**Title**: Health-Aware Deployment Status Semantics
**ID**: SEP-003a-04-CONTEXT-health-aware-status
**Date**: 2026-05-04
**Author**: Codex
**PRD**: SEP-003a-01-PRD-health-aware-status.md

---

## Purpose

SEP-003 added request-time Docker reconciliation so Switchyard can recover
running Switchyard-managed containers after API restart and clear stale state
when containers disappear. The current implementation correctly repairs
process-local memory, but it exposes a semantic gap: Docker container state is
being treated as deployment serving readiness.

This is especially visible for large vLLM deployments such as Qwen 3.6 27B.
After container start, Docker may report `running` while vLLM is still loading
weights, initializing workers, allocating GPU memory, warming caches, or waiting
for its HTTP server to become healthy. During that window, `/api/deployments`
can report `running` even though `/v1/chat/completions` returns `503` or the
health poll later transitions to `error`.

This document frames the design considerations for a likely SEP-003a effort.
It intentionally stops before PLAN-level task breakdown.

---

## Internal Research

### Current Status Model

`switchyard-api/src/switchyard/core/adapter.py` defines the current deployment
status values:

- `running`
- `stopped`
- `loading`
- `error`

`DeploymentInfo.status` is therefore already a Switchyard lifecycle/serving
state, not a raw Docker state. The status name `running` should mean the
deployment can serve inference through Switchyard, not merely that a container
process exists.

### Current Start Path

`switchyard-api/src/switchyard/adapters/vllm.py` starts the Docker container and
returns `DeploymentInfo(status="loading")`.

`switchyard-api/src/switchyard/core/lifecycle.py` then records that loading
state and starts background health polling. Health polling calls the adapter's
health method and eventually updates the state to either `running` or `error`.

This path has the right basic shape for newly started deployments.

### Current Reconciliation Path

`LifecycleManager.reconcile()` checks Docker by Switchyard labels. When it finds
a Docker container with status `running`, `_handle_running()` either:

- preserves existing state and coerces non-`running` existing state to
  `running`; or
- adopts the container into memory as `DeploymentInfo(status="running")`.

This is the problematic behavior. Reconciliation is using Docker `running` as
proof of Switchyard `running`, bypassing backend readiness.

### Current API Surface

The relevant routes call reconciliation before returning state:

- `GET /api/deployments`
- `GET /api/deployments/{deployment}`
- `GET /api/deployments/{deployment}/status`
- `POST /api/deployments/{deployment}/load`
- `POST /api/deployments/{deployment}/unload`
- `POST /api/proxy/{deployment}/{path:path}`
- `GET /v1/models`
- `POST /v1/chat/completions`

That is correct structurally, but the reconciled state can be too optimistic.
Because `/v1/models` and proxy/chat routes rely on `status == "running"`, a
mis-adopted deployment can be advertised or routed before the backend is
actually ready.

---

## Design Philosophy

### Product Status Must Mean Serving Readiness

Switchyard is a model deployment control plane, not a Docker dashboard. The
primary status shown to users and Web UI consumers should answer:

> Can Switchyard route inference to this deployment successfully?

Docker status should be available as diagnostic detail, but it should not define
the deployment's product-level state.

### Container Existence Is Necessary But Not Sufficient

A Docker-running container proves only that:

- a container exists;
- the process has not exited; and
- a host port may be bound.

It does not prove that:

- the runtime HTTP server is listening;
- the model finished loading;
- accelerators were initialized correctly;
- the OpenAI-compatible endpoint is ready; or
- inference requests will succeed.

For large model runtimes, the gap between container start and serving readiness
is expected behavior, not an edge case.

### Reconciliation Should Repair Reality Without Overstating Readiness

Reconciliation should keep state truthful after API restart or external Docker
events. If it sees a running container without existing Switchyard state, it
should adopt the container as a known lifecycle object. It should not assume
that adoption equals serving readiness.

The conservative default should be:

```text
Docker running + health unknown/failed/transient = Switchyard loading
Docker running + backend health passes           = Switchyard running
Docker exited/dead/missing                       = Switchyard stopped or error
```

### Keep Docker State Separate From Serving State

Future status responses should distinguish at least two axes:

- **Deployment status**: `stopped`, `loading`, `running`, `error`
- **Container status**: `missing`, `created`, `running`, `exited`, `dead`,
  `paused`, `restarting`, or equivalent Docker-reported state

These should not be collapsed into one field. The Web UI will need both:

- deployment status for user actions and routing readiness;
- container status for diagnostics and operator confidence.

---

## Candidate Status Semantics

### `stopped`

Use when no Switchyard-managed container exists for the configured deployment,
or after a successful unload.

### `loading`

Use when a container exists and is expected to become ready, but backend health
has not yet succeeded.

This includes:

- newly started containers;
- adopted containers after API restart with unknown readiness;
- Docker-running containers whose health endpoint is not ready yet;
- transient connection refused/timeouts during a configured startup window.

### `running`

Use only when backend health confirms readiness.

For vLLM today, that means the adapter health check returns `running`, currently
based on `GET /health` returning `200`.

### `error`

Use when startup exceeds the configured readiness window, backend health returns
a terminal failure signal, or the container remains alive but repeatedly fails
health beyond the tolerated startup period.

This state should preserve enough diagnostic metadata for an operator to
distinguish "still warming up" from "likely failed."

---

## Health-Aware Reconciliation Options

### Option A: Adopt Docker-Running Containers As `loading`

When reconciliation finds a Docker-running container that is missing from
Switchyard memory, create `DeploymentInfo(status="loading")`, reserve the
observed port, and start/ensure health polling.

Pros:

- conservative and simple;
- avoids advertising unready deployments via `/v1/models`;
- preserves current background health model;
- does not block list/status requests on a backend health probe.

Cons:

- after API restart, a deployment that is already healthy may temporarily show
  `loading` until the next health poll succeeds;
- requires health polling to be restarted for adopted deployments.

This is the recommended baseline.

### Option B: Reconcile With A Bounded Health Probe

When reconciliation sees Docker `running`, perform a short health probe before
returning:

- health success -> `running`;
- connection refused/timeout/non-200 -> `loading` if still inside startup
  grace window;
- repeated failure after startup timeout -> `error`.

Pros:

- status endpoints become fresher immediately;
- after API restart, already-ready deployments can be reclassified as
  `running` quickly.

Cons:

- every status/list/proxy request can incur backend health latency;
- list routes may become slow if many deployments exist;
- request-time health checks can amplify load during startup.

If used, health probes should be short, bounded, and possibly only performed by
detail/status routes, not every list route.

### Option C: Track Health Separately From Status

Keep `status` as the lifecycle state and add fields such as:

- `health`: `unknown`, `starting`, `healthy`, `unhealthy`
- `container_status`
- `last_health_checked_at`
- `last_health_error`
- `startup_elapsed_seconds`
- `startup_timeout_seconds`

Pros:

- gives the Web UI and operators better visibility;
- avoids overloading the status enum with too much meaning;
- creates room for SEP-005 diagnostics without forcing all details into
  `/api/deployments`.

Cons:

- larger response schema change;
- requires careful naming so users understand which field drives routing.

This should be paired with Option A or B.

---

## Recommended Direction

SEP-003a should make deployment status health-aware while keeping the scope
smaller than SEP-005 diagnostics.

Recommended design:

1. Preserve `DeploymentInfo.status` as the product-level serving lifecycle
   status.
2. Do not set `DeploymentInfo.status = "running"` from Docker state alone.
3. Adopt Docker-running containers as `loading` unless backend health has
   already succeeded.
4. Ensure adopted loading deployments have a health task, so they naturally
   transition to `running` or `error`.
5. Add diagnostic fields to status/detail responses showing Docker/container
   state separately from Switchyard serving state.
6. Keep `/v1/models` limited to deployments with Switchyard status `running`.
7. Keep `/v1/chat/completions` and `/api/proxy/...` gated on Switchyard status
   `running`.
8. Make health timeout configurable per runtime or deployment, because large
   models may legitimately need more than the current 300 seconds after a cold
   host reboot.

This keeps the Web UI-facing contract truthful: "running" means usable for
inference, not merely alive as a process.

---

## Suggested Response Shape

`GET /api/deployments` should remain compact but should stop hiding the
distinction between container and serving state.

Candidate list item:

```json
{
  "deployment_name": "qwen3.6-27b-fp8-vllm-trainbox",
  "model": "qwen3-27b-fp8",
  "runtime": "vllm",
  "host": "trainbox",
  "status": "loading",
  "health": "starting",
  "container_status": "running"
}
```

`GET /api/deployments/{deployment}/status` can be richer:

```json
{
  "deployment_name": "qwen3.6-27b-fp8-vllm-trainbox",
  "status": "loading",
  "health": "starting",
  "container_status": "running",
  "port": 7113,
  "container_id": "abc123",
  "started_at": "2026-05-04T12:34:56Z",
  "startup_elapsed_seconds": 420,
  "startup_timeout_seconds": 900,
  "last_health_checked_at": "2026-05-04T12:41:56Z",
  "last_health_error": "connection refused"
}
```

The list endpoint should be optimized for scanning. The status/detail endpoint
should be optimized for operator diagnosis.

---

## Startup Timeout Considerations

The current lifecycle defaults include a 300 second health timeout. That may be
too short for cold boot plus large model initialization.

SEP-003a should decide where this belongs:

- runtime default, because vLLM large-model startup has known characteristics;
- deployment override, because Qwen 27B on trainbox may need a different budget
  than TinyLlama on CPU;
- host default, because cold GPU startup can vary by machine;
- or a cascade using the same philosophy as other deployment settings.

Recommended direction: add a deployment-overridable runtime default, e.g.
`health_timeout_seconds`, with a conservative default and explicit override for
large models.

This should be a typed, inspectable setting rather than an implicit magic
constant.

---

## Web UI Considerations

The Web UI will need to show progress and explain transient states without
pretending to know model-load percentages.

Useful operator states:

- configured but stopped;
- starting container;
- container running, runtime not healthy yet;
- serving and routable;
- startup timed out;
- container exited;
- Docker unreachable;
- stale state repaired by reconciliation.

The UI should avoid a fake progress bar unless a runtime exposes real progress.
Prefer elapsed time, current phase, last health result, and next action.

Potential actions:

- load;
- unload;
- retry health;
- view logs (SEP-005);
- view dry-run/preflight (SEP-005);
- inspect host/runtime details (SEP-006/SEP-007 depending final boundaries).

---

## Error Handling Considerations

Docker unreachable is distinct from deployment stopped or runtime unhealthy.
If Switchyard cannot inspect Docker during reconciliation, status responses
should avoid clearing state blindly and should surface an operational diagnostic
such as `health="unknown"` or `docker_status="unreachable"`.

For load operations, failing fast is appropriate because Switchyard cannot
safely decide whether it is starting a duplicate container.

For read operations, degrading gracefully may be preferable:

- preserve existing in-memory state;
- include a Docker connectivity warning in detail/status responses;
- avoid claiming stale state is verified.

SEP-003a should define this policy explicitly.

---

## Boundary With SEP-005 And SEP-006

SEP-003a should focus on truthful status semantics and health-aware
reconciliation.

Likely in scope:

- status meaning;
- reconciliation adoption status;
- health task restart for adopted containers;
- response fields needed to distinguish Docker and serving state;
- configurable startup timeout if needed to prevent premature `error`.

Likely out of scope:

- logs endpoint;
- dry-run or preflight endpoint;
- detailed Docker command/volume/env previews;
- host inventory and hardware discovery;
- Web UI implementation.

SEP-005 can build richer diagnostics on top of the corrected status model.
SEP-006/Web UI work should consume the corrected status model rather than
encoding Docker-specific heuristics in frontend state.

---

## Open Questions

1. Should list routes perform any backend health probe, or should they rely only
   on current in-memory status plus Docker reconciliation?
2. Should status/detail routes perform a short synchronous health probe for
   fresher results?
3. What startup timeout should Qwen 3.6 27B on trainbox use after cold reboot?
4. Should `error` mean terminal failure only, or can it also mean "startup
   exceeded timeout but container is still alive"?
5. Should Docker connectivity failures appear as a new diagnostic field, a
   health value, or an HTTP error depending on route?
6. Should `DeploymentInfo` remain the only state record, or should Switchyard add
   a separate runtime/container observation record?

---

## Risks

- Marking Docker-running containers as `running` makes routing and Web UI state
  lie during large model startup.
- Performing health probes on every list request could make the management API
  slow or flaky under load.
- Extending the status enum too aggressively could create UI churn and client
  ambiguity.
- Leaving startup timeout fixed at 300 seconds will likely keep misclassifying
  valid cold-start large-model loads as failures.
- Treating Docker unreachable as stopped could accidentally erase useful state
  during transient tunnel failures.

---

## Working Recommendation

The highest-value next move is to correct the invariant:

> Switchyard `running` means backend health has succeeded.

Everything else should follow from that. Reconciliation can adopt containers,
but adoption should create or preserve an honest `loading` state until health
proves readiness. Docker state should be visible, but it should not masquerade
as serving state.
