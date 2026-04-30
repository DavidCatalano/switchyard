# Project Plan (PLAN)

**Title**: Switchyard MVP — Control Plane and Model Runtime Manager
**ID**: SEP-001-02-PLAN-mvp-control-plane
**Status**: Draft
**Date**: 2026-04-27
**Track**: Lightweight
**Spec**: `spec.md`

---

## Implementation Approach

Build the control plane as a FastAPI service with in-memory state, file-based YAML config, and a pluggable backend adapter interface. Docker SDK manages container lifecycle. httpx proxies inference requests to running backends. One concrete backend adapter (vLLM) is implemented as the first target; the interface is stable for adding koboldcpp, exllamav2, and SGLang later.

The project lives in `switchyard-api/` with a `src/switchyard/` package layout. All quality gates (`uv run pytest`, `uv run ruff`, `uv run mypy`) pass on the final deliverable.

---

## Inlined Requirements (from `spec.md`)

The control plane must:
- Load and validate YAML config at startup with three-level cascade: `global` → `runtime_defaults.{backend}` → `models.{name}.runtime` (fatal on invalid config)
- Support both `repo:` (HuggingFace) and `model:` (local filesystem path) as model source identifiers
- Pass arbitrary backend flags via `extra_args` catch-all, translated verbatim to CLI arguments
- Manage model lifecycle: load (async, 202), unload, status, list
- Route `POST /v1/chat/completions` to the correct backend container
- Proxy streaming responses transparently (SSE, no buffering)
- Support scoped backend passthrough (`POST /v1/backends/{model}/{path...}`)
- Detect and adopt orphan containers on startup
- Remove crashed containers on startup, explicit unloads on stop
- Allocate ports sequentially from base port 8000, skip in-use, release on stop
- Track deployment state in memory (model → deployment → port)
- Return specified HTTP error codes (404, 400, 503, 500, 504)
- Expose `BackendAdapter` protocol: start, stop, health, endpoint

Non-goals: autoscaling, GPU scheduling, database, UI, implicit model loading, config hot-reload, batching.

---

## Task Breakdown

### Phase 1: Scaffolding and Configuration

**Goal**: Project boots, config loads and validates, quality gates pass on empty project.

#### Tasks
- [X] **T1.1**: Scaffold `switchyard-api/` — `pyproject.toml` (uv, FastAPI, pydantic, pydantic-settings, pyyaml, docker, httpx, structlog, opentelemetry-api), `src/switchyard/` package layout, `tests/` directory
- [X] **T1.2**: Define Pydantic config models for YAML: `GlobalConfig`, `RuntimeDefaults` (dict keyed by backend name, each value is a backend-specific defaults model), `ModelConfig`, `RuntimeConfig` (supports both `repo:` and `model:` paths, plus `extra_args: dict[str, Any]` for arbitrary flag passthrough). `AppSettings` (pydantic-settings) for env vars. YAML and env merge into a single config surface.
- [X] **T1.3**: Implement config loader — YAML via pyyaml + `model_validate`, env vars via pydantic-settings. Resolves three-level cascade: `runtime_defaults.{backend}` provides base → per-model `runtime` overrides → `extra_args` supplies arbitrary flags. Fatal exit on invalid config.
- [X] **T1.4**: Configure structlog — JSON renderer for prod, console for dev. Add FastAPI middleware for request ID propagation and structured access logging.
- [X] **T1.5**: Wire opentelemetry-api hooks — tracing context propagation, metrics endpoint placeholder. No SDK lock-in at this layer.
- [X] **T1.6**: Tests: config loading (valid YAML, invalid YAML, missing fields, type coercion, env var overrides), structlog output format
- [X] **T1.7**: Tests: config cascade — `runtime_defaults` applied to per-model, per-model overrides take precedence, `extra_args` passed through verbatim, both `repo:` and `model:` accepted
- [X] **T1.8**: Minimal FastAPI app with `/health` endpoint; verify quality gates pass

### Phase 2: Core Infrastructure

**Goal**: Backend adapter interface, port allocator, and deployment state are wired and tested.

#### Tasks
- [X] **T2.1**: Define `BackendAdapter` protocol (`start`, `stop`, `health`, `endpoint`) and `DeploymentInfo` dataclass
- [X] **T2.2**: Implement adapter registry (map backend name → adapter class, factory to instantiate)
- [X] **T2.3**: Implement port allocator (sequential from base, skip-in-use check, release on free)
- [X] **T2.4**: Implement deployment state manager (in-memory: model → deployment mapping, status tracking, port tracking)
- [X] **T2.5**: Tests: adapter registry (register, lookup, unknown backend), port allocator (allocation, skip-in-use, release), state manager (add, get, remove, status transitions)

### Phase 3: Lifecycle Management

**Goal**: Models can be loaded/unloaded, health checks run asynchronously, orphan detection works on startup.

#### Tasks
- [X] **T3.1**: Implement `LifecycleManager` — `load_model` (async container start via adapter, background health check task, status transitions loading → running/error)
- [X] **T3.2**: Implement `LifecycleManager.unload_model` (stop container, remove container, release port, update state)
- [X] **T3.3**: Implement `LifecycleManager.health_check` (on-demand via adapter, background task for post-load polling)
- [X] **T3.4**: Implement orphan detection — scan Docker for containers matching `{model}-{backend}-{instance}` pattern, adopt running orphans, remove crashed orphans
- [X] **T3.5**: Implement startup bootstrap sequence (load config → verify Docker → orphan detection → auto-start → listen)
- [X] **T3.6**: Tests: load/unload lifecycle, health check transitions, orphan detection (mock Docker client), startup sequence

### Phase 4: API and Routing

**Goal**: All API endpoints work, requests route to backends, streaming proxies correctly, error codes match spec.

#### Tasks
- [X] **T4.1**: Implement `POST /models/load` (202 async response, triggers lifecycle manager)
- [X] **T4.2**: Implement `POST /models/unload` (stops and removes container)
- [X] **T4.3**: Implement `GET /models` (returns list with status, backend, port, started_at)
- [X] **T4.4**: Implement `GET /models/{model}/status` (returns status enum)
- [X] **T4.5**: Implement `POST /v1/chat/completions` — extract model, verify running, proxy via httpx (blocking and streaming variants)
- [X] **T4.6**: Implement streaming proxy — transparent SSE forward via `StreamingResponse`, no buffering, timeout on initial connection only
- [X] **T4.7**: Implement `POST /v1/backends/{model}/{path...}` — scoped passthrough to backend-specific endpoints
- [X] **T4.8**: Implement error handling middleware/handlers per spec §13 (404, 400, 503, 500, 504)
- [X] **T4.9**: Tests: all lifecycle endpoints (happy path + error cases), chat completions proxy (mock backend), streaming proxy (mock SSE), passthrough, error code coverage

### Phase 5: First Backend Adapter (vLLM)

**Goal**: vLLM adapter works end-to-end — container starts, health checks pass, requests route successfully.

#### Tasks
- [X] **T5.1**: Implement `VLLMAdapter` — `start` (build CLI flags from named Pydantic fields + `extra_args` passthrough; docker run with image, port binding, env vars, resource limits), `stop`, `health` (GET /health), `endpoint` (return bound URL). Adapter translates known fields and passes `extra_args` verbatim — no branching on tier.
- [X] **T5.2**: Register vLLM adapter in adapter registry
- [X] **T5.3**: Graduated integration testing:
  - **T5.3a**: Docker lifecycle test with minimal HTTP container — validates `start`/`stop`/`health` against a real container (tiny `python:3-slim` serving `/health → 200`). Requires Docker, skipped if unavailable. No vLLM, no GPU.
  - **T5.3b**: CLI arg verification — assert `_build_cli_args` output matches CLI commands from `reference-then-delete/vLLM/docker-compose.yml`. Validates flag translation accuracy against real-world configs.
  - **T5.3c**: vLLM on CPU with tiny model — full adapter + vLLM on CPU (`--cpu` flag, `gpt2` or similar). Validates end-to-end stack. Skipped by default; enabled via `TEST_VLLM_CPU=1` env var. Requires Docker + model download.
- [ ] **T5.4**: Smoke test: full request cycle (load model → poll status → send chat completion → verify response) — manual/Docker-required

---

## Dependencies

### Production
- `fastapi` — API framework, async support, streaming responses
- `uvicorn` — ASGI server
- `pydantic` — config and request/response models
- `pydantic-settings` — environment variable config (ports, API keys, log level)
- `pyyaml` — YAML config parsing
- `docker` — Docker SDK for container lifecycle
- `httpx` — async HTTP client for backend proxying
- `structlog` — structured logging (JSON for prod, console for dev)
- `opentelemetry-api` — tracing/metrics hooks (lightweight, SDK-agnostic)

### Development
- `pytest` — test runner
- `pytest-asyncio` — async test support
- `ruff` — linting and formatting
- `mypy` — type checking
- `types-pyyaml` — type stubs for pyyaml
- `opentelemetry-sdk` — local development tracing/metrics (dev only)

### Critical Path
1. T1.1 → T1.2 → T1.3 (scaffolding and config are foundational)
2. T2.1 → T2.2 → T2.3 → T2.4 (core infra must exist before lifecycle)
3. T3.1 → T3.2 → T3.4 → T3.5 (lifecycle depends on core infra)
4. T4.x (API depends on lifecycle + state)
5. T5.x (vLLM adapter depends on adapter interface from T2.1)

### Parallel Work Streams
- Phase 2 tasks can partially overlap (registry, allocator, state are independent)
- Phase 4 endpoint tasks are independent of each other (all depend on Phase 3)

---

## Risk Mitigation

### Risk: Docker integration tests are environment-dependent
- **Mitigation**: Unit tests mock the Docker SDK. Integration tests require Docker and are skipped with a clear message if unavailable.
- **Owner**: Agent + user
- **Timeline**: Phase 3–5

### Risk: Streaming proxy complexity
- **Mitigation**: Use httpx async streaming with FastAPI `StreamingResponse`. Keep it thin — read chunks from upstream, yield to downstream. No parsing, no transformation.
- **Owner**: Agent
- **Timeline**: Phase 4

### Risk: Port allocation conflicts in CI
- **Mitigation**: Port allocator checks actual socket availability before claiming. Tests use ephemeral ports where possible.
- **Owner**: Agent
- **Timeline**: Phase 2

---

## Validation Plan

### Quality Gates (from `DEV.md`)
- [ ] `uv run pytest` — all tests pass
- [ ] `uv run ruff check src --fix` — no lint errors
- [ ] `uv run mypy src/switchyard` — no type errors

### Success Criteria
- [ ] Config loads and validates from YAML (valid and invalid cases)
- [ ] Models can be loaded (202), polled for status, and unloaded via API
- [ ] Orphan detection adopts running containers and removes crashed ones on startup
- [ ] Chat completions route to correct backend container
- [ ] Streaming responses proxy transparently with no buffering
- [ ] Backend passthrough reaches backend-specific endpoints
- [ ] Error responses match spec §13 status codes
- [ ] vLLM adapter starts, health-checks, and serves requests in integration test

---

## Decision Log

| # | Decision | Reason | Status | Date |
|---|----------|--------|--------|------|
| D1 | Three-level config cascade (`global` → `runtime_defaults.{backend}` → `models.{name}.runtime`) | Keeps schema extensible as new backends are added; avoids top-level pollution (`vllm_defaults`, `koboldcpp_defaults`, etc.). Peer-reviewed. | Agreed | 2026-04-28 |
| D2 | Tiers are documentation-only; all named fields and `extra_args` become CLI flags identically | Reduces adapter complexity; new vLLM flags usable immediately via `extra_args`, promoted to named fields later with zero code change. Peer-reviewed. | Agreed | 2026-04-28 |
| D3 | `image` field acts as version lock — pinned tag freezes CLI flag surface for that model | Mitigates flag deprecation risk (vLLM renames/changes semantics across versions). YAML becomes the version lock file. Peer-reviewed. | Agreed | 2026-04-28 |
| D4 | `BackendAdapter` uses `abc.ABC` rather than `typing.Protocol` | Provides runtime enforcement (cannot instantiate incomplete subclasses) while still supporting static type checking. Simpler than runtime Protocol checks. | Agreed | 2026-04-28 |
| D5 | `DeploymentInfo` is a frozen dataclass | Immutability prevents accidental state mutation across async boundaries; status updates create new instances. | Agreed | 2026-04-28 |

---

This plan serves as the single source of truth during implementation. Update status as work progresses.
