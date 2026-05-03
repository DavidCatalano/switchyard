# Deployment Lifecycle and API Namespace Migration

**Title**: SEP-003 Deployment Lifecycle and API Namespace Migration
**ID**: SEP-003-02-PLAN-deployment-lifecycle
**Status**: Draft
**Date**: 2026-05-03

---

## Implementation Approach

SEP-003 has two objectives:

1. **API namespace cleanup** — move Switchyard-native control-plane routes under `/api/`, keep OpenAI-compatible routes under `/v1/`, keep `/health` at root. Move backend passthrough from `/v1/backends/` to `/api/proxy/` because it is not part of the OpenAI-compatible surface.
2. **Deployment lifecycle correctness** — replace body-based lifecycle routes with deployment-resource routes, add a deployment detail endpoint, reconcile in-memory state with Docker container state at request time, and handle stale/externally-removed containers gracefully.

Status vocabulary uses the existing enum: `running`, `loading`, `stopped`, `error`. No new status values are introduced.

Implementation follows TDD: tests first, then implementation. Route migration and reconciliation are interleaved so that new routes are always tested against working reconciliation logic.

### Current vs. Target Route Map

| Current Route | Target Route | Notes |
|---|---|---|
| `GET /health` | `GET /health` | No change |
| `POST /deployments/load` | `POST /api/deployments/{deployment}/load` | Body-based → path-based |
| `POST /deployments/unload` | `POST /api/deployments/{deployment}/unload` | Body-based → path-based |
| `GET /deployments` | `GET /api/deployments` | Prefix only |
| `GET /deployments/{deployment}/status` | `GET /api/deployments/{deployment}/status` | Prefix only |
| *(not exists)* | `GET /api/deployments/{deployment}` | New: detail endpoint |
| `POST /v1/backends/{deployment}/{path:path}` | `POST /api/proxy/{deployment}/{path:path}` | Move out of `/v1/` |
| `GET /v1/models` | `GET /v1/models` | No change |
| `POST /v1/chat/completions` | `POST /v1/chat/completions` | No change |

### Out of Scope

These routes are explicitly deferred to later SEPs and should not be
implemented as part of SEP-003:

| Route | Target SEP | Reason |
|---|---|---|
| `GET /api/hosts` | SEP-005 or SEP-007 | Host read APIs are separate from deployment lifecycle migration |
| `GET /api/hosts/{host}/status` | SEP-007 | Host status depends on host resource discovery |
| `GET /api/deployments/{deployment}/logs` | SEP-005 | Deployment diagnostics, not lifecycle route migration |
| `POST /api/deployments/{deployment}/dry-run` | SEP-005 | Launch preview requires adapter preview contract and redaction policy |
| `POST /api/deployments/{deployment}/validate` | SEP-005 | Static config validation is a diagnostics/read API concern |
| `POST /api/deployments/{deployment}/preflight` | SEP-005 or SEP-007 | Dynamic Docker/host checks overlap host discovery and may be slower/side-effect-adjacent |
| `GET /api/runtimes/{runtime}/schema` | SEP-004 | Runtime schema depends on typed runtime resolver hardening |

## Task Breakdown

### Phase 1: Tests — New Route Structure and Responses

**Goal**: Failing tests that define the new `/api/` route shape, response bodies, and lifecycle behaviors.

#### Tasks
- [ ] **T1.1**: Write API route tests for new `/api/deployments` list endpoint
  - File: `switchyard-api/tests/test_api.py` (new class `TestApiDeploymentRoutes`)
  - `GET /api/deployments` returns all configured deployments with status
  - Configured deployments with no lifecycle state get `status: "stopped"`
  - Active deployments reflect their actual in-memory status
  - Old `GET /deployments` no longer matches (returns 404)

- [ ] **T1.2**: Write API route tests for `GET /api/deployments/{deployment}` detail endpoint
  - Returns model, runtime, host references, placement, resolved store paths, runtime args at config layer, current status summary
  - Does NOT return full env/volumes/Docker options/command arrays
  - Returns 404 for unknown deployment name
  - Configured deployment with no lifecycle state returns `status: "stopped"`

- [ ] **T1.3**: Write API route tests for `GET /api/deployments/{deployment}/status` under `/api/`
  - Returns live operational state for known deployments
  - Returns 404 for unknown deployment
  - Old path `GET /deployments/{deployment}/status` no longer matches

- [ ] **T1.4**: Write API route tests for `POST /api/deployments/{deployment}/load`
  - Path-based deployment ID (no request body)
  - Returns 202 with deployment info on success
  - Returns 404 for unknown deployment name
  - Old body-based `POST /deployments/load` no longer matches

- [ ] **T1.5**: Write API route tests for `POST /api/deployments/{deployment}/unload`
  - Path-based deployment ID (no request body)
  - Returns 200 with status `"stopped"`
  - Returns 404 for unknown deployment name
  - Idempotent: unloading a deployment with no container returns `stopped`

- [ ] **T1.6**: Write API route tests for `POST /api/proxy/{deployment}/{path:path}`
  - Proxy to healthy running deployment succeeds
  - Proxy to unknown deployment returns 404
  - Proxy to stopped deployment returns 400
  - Old `POST /v1/backends/{deployment}/{path:path}` no longer matches

- [ ] **T1.7**: Write tests verifying `/v1/models` and `/v1/chat/completions` are unchanged
  - Confirms OpenAI-compatible routes were not disturbed by migration

### Phase 2: Reconciliation Layer

**Goal**: Request-time Docker state reconciliation via the lifecycle layer and host-aware Docker SDK factory, using Switchyard labels as the authoritative container lookup.

#### Tasks
- [ ] **T2.1**: Add reconciliation method to `LifecycleManager`
  - File: `switchyard-api/src/switchyard/core/lifecycle.py`
  - Method shape should accept enough deployment context to use the correct
    Docker host, backend, internal port, and labels; a bare
    `reconcile(deployment_name: str)` is insufficient if it cannot resolve the
    target host/container context
  - Candidate method:
    `reconcile(deployment_name: str, resolved: ResolvedDeployment) -> DeploymentInfo | None`
  - Looks up Docker container by Switchyard labels (not name), using
    `switchyard.managed=true` and `switchyard.deployment={deployment_name}`
  - Four outcomes:
    - Container running and already in memory → preserve state, update status
      to `running` if needed, return `DeploymentInfo`
    - Container running and missing from memory → adopt it by rebuilding
      `DeploymentInfo` from Docker labels, port bindings, backend metadata, and
      resolved config; reserve the observed port in `PortAllocator`; return
      `DeploymentInfo`
    - Container exited/dead → clear in-memory state, cancel/remove any health
      task for the deployment, release port, return `None`
    - Container gone → clear in-memory state, cancel/remove any health task for
      the deployment, release port, return `None`
  - Uses a host-aware Docker client factory: `DockerClientFactory = Callable[[str | None], DockerClient]`. The factory resolves the correct Docker client from `resolved.docker_host` so multi-host behavior is preserved. A small helper in `switchyard.core.docker` provides client creation and label-based container lookup; `LifecycleManager` owns the state transitions.
  - `LifecycleManager.__init__` accepts an optional `docker_client_factory` parameter. Tests inject a mock factory; production uses the factory from `create_app()`.

- [ ] **T2.2**: Write unit tests for `LifecycleManager.reconcile()`
  - File: `switchyard-api/tests/test_lifecycle.py` (new class `TestReconcile`)
  - Test: reconcile running container → state preserved
  - Test: reconcile running labeled container after API restart → state adopted,
    observed port reserved, status `running`
  - Test: reconcile exited container → state cleared, port released
  - Test: reconcile dead container → state cleared, port released
  - Test: reconcile gone container → state cleared, port released
  - Test: reconcile stale deployment with health task → task cancelled/removed
  - Test: reconcile unknown deployment → no-op, no error

- [ ] **T2.3**: Wire reconciliation into `load_model`
  - Before load: reconcile to clear stale state that could block a valid load
  - If reconciliation finds a running labeled container for the deployment,
    return/raise consistently with the existing "already deployed" behavior
    after adoption
  - If reconciliation finds exited/dead managed containers, ensure stale
    containers are removed before `adapter.start()` so Docker name conflicts do
    not block a valid load

- [ ] **T2.4**: Wire reconciliation into `unload_model`
  - Before unload: reconcile first
  - If container already gone/exited: return idempotent success (state already cleared)

- [ ] **T2.5**: Write lifecycle integration tests for reconciliation behavior
  - File: `switchyard-api/tests/test_lifecycle.py`
  - Test: API restart scenario → reconcile adopts running labeled container
  - Test: load → reconcile clears stale → load succeeds
  - Test: unload → reconcile finds gone container → returns without error
  - Test: load → reconcile finds exited container → removes/replaces stale
    managed container before starting a new one

### Phase 3: Route Migration in `app.py`

**Goal**: Replace existing routes with new `/api/`-prefixed routes, wired to reconciliation and lifecycle manager.

#### Tasks
- [ ] **T3.1**: Refactor route registration — remove `LoadModelRequest`/`UnloadModelRequest` body models
  - File: `switchyard-api/src/switchyard/app.py`
  - Remove body-based request models; deployment ID comes from path parameter

- [ ] **T3.2**: Implement `GET /api/deployments`
  - Returns all configured deployments from `config.deployments`
  - For each, look up in-memory state; derive `status: "stopped"` if absent
  - Attach current status to each entry
  - Call reconciliation before building the list

- [ ] **T3.3**: Implement `GET /api/deployments/{deployment}`
  - Validate deployment exists in config
  - Reconcile the deployment
  - Return configured/resolved intent: model, runtime, host, placement, resolved store paths, runtime args at config layer
  - Attach current status summary (or `stopped` if no state)

- [ ] **T3.4**: Implement `GET /api/deployments/{deployment}/status`
  - Validate deployment exists in config
  - Reconcile the deployment
  - Return live operational state: status, port, container_id, started_at, health

- [ ] **T3.5**: Implement `POST /api/deployments/{deployment}/load`
  - Validate deployment exists in config
  - Resolve deployment
  - Reconcile before load (clears stale state or adopts already-running state)
  - Call `manager.load_model()`
  - Return 202 with deployment info

- [ ] **T3.6**: Implement `POST /api/deployments/{deployment}/unload`
  - Validate deployment exists in config
  - Resolve deployment
  - Reconcile before unload
  - If container already gone, return idempotent `stopped`
  - Otherwise call `manager.unload_model()`

- [ ] **T3.7**: Implement `POST /api/proxy/{deployment}/{path:path}`
  - Reconcile the deployment before proxying
  - Resolve deployment ID → lifecycle state → container → backend URL → proxied path
  - Use existing proxy helpers (`_blocking_proxy`, `_streaming_proxy`)
  - Reject if deployment is not running

- [ ] **T3.8**: Remove old routes
  - `POST /deployments/load`, `POST /deployments/unload`
  - `GET /deployments`, `GET /deployments/{deployment}/status`
  - `POST /v1/backends/{deployment}/{path:path}`
  - Verify no other code paths reference old routes

- [ ] **T3.9**: Update `_get_running_deployment` and `_backend_url` helpers
  - Remove references to removed routes
  - Keep helpers for `/v1/chat/completions` (unchanged)

### Phase 4: Smoke Tests and Existing Test Updates

**Goal**: All existing tests pass against new routes. Smoke tests aligned on deployment IDs and new paths.

#### Tasks
- [ ] **T4.1**: Update `test_api.py` — migrate existing tests to new `/api/` paths
  - Old `TestDeploymentRoutes` class: update all paths to `/api/deployments/...`
  - Old `TestOpenAIProxy` backend tests: update to `/api/proxy/...`
  - Remove `LoadModelRequest`/`UnloadModelRequest` body usage; use path params
  - Prune tests that only validate the removed body-based lifecycle API shape;
    do not retain compatibility tests except explicit 404 assertions for
    removed routes

- [ ] **T4.2**: Update `test_proxy.py` — migrate backend passthrough tests to `/api/proxy/`
  - Update all `/v1/backends/` references to `/api/proxy/`

- [ ] **T4.3**: Update `test_app.py` if it references old routes

- [ ] **T4.4**: Verify `GET /v1/models` tests still pass unchanged

- [ ] **T4.5**: Verify `POST /v1/chat/completions` tests still pass unchanged

- [ ] **T4.6**: Update operational helpers and manual smoke references
  - File: `Makefile`
  - Update curl targets from old `/deployments/...` routes to new
    `/api/deployments/...` routes
  - Update any README or SEP manual smoke commands that still reference old
    lifecycle route paths

- [ ] **T4.7**: Run full test suite
  - `uv run pytest`
  - `uv run ruff check src tests --fix`
  - `uv run mypy src/switchyard`

## Dependencies

### Critical Path
1. T1 (tests) → define target behavior
2. T2 (reconciliation) → core correctness dependency for T3 routes
3. T3 (route migration) → depends on T1 failing tests and T2 reconciliation
4. T4 (test/helper updates) → depends on T3 being implemented

### Parallel Work Streams
- T1.1–T1.7 can be written in parallel (independent test classes)
- T2.1–T2.2 can proceed while T1 is being written (unit tests for reconciliation don't depend on routes)

## Risk Mitigation

### Risk 1: Docker SDK Dependency in Reconciliation
- **Description**: `LifecycleManager` currently does not import Docker SDK directly. Reconciliation needs to query containers by label.
- **Mitigation**: Keep Docker SDK access behind a host-aware factory typed as `Callable[[str | None], DockerClient]`. The factory lives in `switchyard.core.docker` and resolves the correct client from `resolved.docker_host`. A small helper in that module also provides label-based container lookup (`switchyard.managed=true`, `switchyard.deployment={id}`). `LifecycleManager` owns all state-transition policy; no Docker logic scatters into adapters. Tests inject a mock factory at the fixture level.
- **Owner**: Implementation
- **Timeline**: T2.1

### Risk 2: Breaking Existing Consumers
- **Description**: Old routes (`POST /deployments/load`, `GET /deployments`, `POST /v1/backends/...`) are removed. Any external scripts or tests referencing them break.
- **Mitigation**: All internal tests are updated in T4. The PLAN explicitly notes which routes are removed. No backward-compat redirects — clean break.
- **Owner**: Implementation
- **Timeline**: T3.8, T4

### Risk 3: Reconciliation Performance
- **Description**: Reconciling on every request adds Docker SDK overhead.
- **Mitigation**: Reconciliation is targeted (one deployment per call, or batch for list). Docker SDK `containers.list(filter=...)` with label filter is lightweight. This is management API, not high-throughput inference path.
- **Owner**: Implementation
- **Timeline**: T2.1

### Risk 4: Port Allocator State After Reconciliation Clears
- **Description**: When reconciliation releases a port, a concurrent load request might allocate the same port before the old state is fully cleared.
- **Mitigation**: Port release, health-task cancellation, and state removal are synchronous within reconciliation. `PortAllocator.release()` is already thread-safe. The lifecycle manager must not retain background tasks for cleared deployments.
- **Owner**: Implementation
- **Timeline**: T2.1

## Validation Plan

**Validation Commands**: See `AGENTS.md` Section "Quality Gates" for complete validation command list.

```bash
cd switchyard-api/
uv run pytest
uv run ruff check src tests --fix
uv run mypy src/switchyard
```

### Success Criteria Validation
- [ ] All tests pass: `uv run pytest` exits 0
- [ ] Linting clean: `uv run ruff check src tests` exits 0
- [ ] Type checking clean: `uv run mypy src/switchyard` exits 0
- [ ] No regressions: `GET /v1/models`, `POST /v1/chat/completions`, `GET /health` unchanged
- [ ] `GET /api/deployments` returns all configured deployments with derived status
- [ ] `GET /api/deployments/{deployment}` returns config detail + status summary (no dry-run data)
- [ ] `GET /api/deployments/{deployment}/status` returns live operational state
- [ ] `POST /api/deployments/{deployment}/load` loads by path ID, returns 202
- [ ] `POST /api/deployments/{deployment}/unload` unloads by path ID, idempotent on gone containers
- [ ] `POST /api/proxy/{deployment}/{path:path}` proxies through running deployment
- [ ] Old routes return 404 (`POST /deployments/load`, `GET /deployments`, `POST /v1/backends/...`)
- [ ] Reconciliation adopts running Switchyard-labeled containers when API memory is empty and reserves the observed port
- [ ] Reconciliation clears in-memory state and releases ports for gone/exited/dead containers
- [ ] Reconciliation cancels/removes stale health tasks for cleared deployments
- [ ] Reconciliation before load prevents stale state from blocking valid loads
- [ ] Stale exited/dead managed containers do not cause Docker name conflicts on load
- [ ] Makefile/manual smoke references use new `/api/deployments/...` lifecycle routes
- [ ] Ruff and mypy pass with no errors

---

## Decision Log

| ID | Decision | Reason | Status |
|---|---|---|---|
| D1 | Switchyard-native control-plane routes move under `/api/` | Separates first-party management API traffic from OpenAI-compatible inference routes and prevents route collisions with the future Web UI | Agreed |
| D2 | OpenAI-compatible inference routes remain under `/v1/` | Keeps `/v1/` reserved for client-facing OpenAI-compatible behavior such as model discovery and chat completions | Agreed |
| D3 | `/health` remains at root | Operational liveness endpoints are commonly exposed outside product API namespaces and do not belong to either `/api/` or `/v1/` | Agreed |
| D4 | Body-based lifecycle actions are replaced by deployment-resource routes with no compatibility aliases | Deployment is the lifecycle unit after SEP-002; a clean break avoids carrying obsolete API shape during early project development | Agreed |
| D5 | Backend passthrough moves from `/v1/backends/{deployment}/{path:path}` to `/api/proxy/{deployment}/{path:path}` | Passthrough is Switchyard management/proxy surface, not OpenAI-compatible API surface | Agreed |
| D6 | SEP-003 uses the existing status enum: `running`, `loading`, `stopped`, `error` | Avoids unnecessary state-model migration; configured deployments without live state derive `stopped` | Agreed |
| D7 | `GET /api/deployments` returns all configured deployments with current/derived status attached | The Web UI must show stopped deployments and offer lifecycle actions; active-only discovery remains `/v1/models` | Agreed |
| D8 | `GET /api/deployments/{deployment}` returns deployment detail plus a small status summary, not dry-run output | Detail answers what Switchyard thinks the deployment is; generated Docker options, commands, env, volumes, and redaction policy are deferred to future dry-run work | Agreed |
| D9 | Docker reconciliation runs at request time, not in a background reconciler | Keeps SEP-003 deterministic and scoped while correcting stale state before lifecycle/status/proxy operations | Agreed |
| D10 | Reconciliation uses Switchyard labels as the authoritative container marker | Container names are for human readability; labels identify Switchyard-managed containers across host/runtime naming changes | Agreed |
| D11 | Reconciliation auto-corrects stale external removal or exited/dead managed containers | Clearing in-memory state, releasing ports, and reporting `stopped` is less surprising than requiring manual state repair | Agreed |
| D12 | Request-time reconciliation is owned by `LifecycleManager` using a host-aware Docker client factory, not `BackendAdapter.reconcile()` | Reconciliation repairs Switchyard lifecycle state using cross-adapter labels, ports, and health-task cleanup. Keeping it in lifecycle avoids duplicating Docker label lookup and stale-state policy in every adapter. The factory resolves the correct Docker client from `ResolvedDeployment.docker_host` so multi-host behavior is preserved | Agreed |

---

This plan serves as the single source of truth during implementation. Update status as work progresses.
