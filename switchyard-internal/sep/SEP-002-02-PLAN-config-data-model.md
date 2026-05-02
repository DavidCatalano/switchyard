# Project Plan (PLAN)

**Title**: Config Data Model Refactor
**ID**: SEP-002-02-PLAN-config-data-model
**Status**: Agreed
**Date**: 2026-04-30
**PRD**: `SEP-002-01-PRD-config-data-model.md`
**ADR(s)**: None

---

## Implementation Approach

Replace the SEP-001 config shape (`global` → `runtime_defaults` → per-model `runtime`) with an entity-based model defining `hosts`, `runtimes`, `models`, and `deployments`. The loader resolves references between entities into a complete configuration data object. Process-local bootstrap settings move to `.env`.

This PLAN delivers the data model and loader only. Lifecycle changes (wiring adapters, starting containers by deployment ID) belong to SEP-003.

The target config shape:

```yaml
hosts:
  trainbox: { ... }

runtimes:
  vllm: { ... }

models:
  qwen3-27b-fp8: { ... }

deployments:
  qwen3-27b-vllm-trainbox: { ... }
```

---

## Task Breakdown

### Phase 1: Pydantic Entity Models

**Goal**: New typed models for hosts, runtimes, models, and deployments replace the old `GlobalConfig`, `RuntimeDefaults`, and `ModelConfig` classes.

#### Tasks
- [x] **T1.1**: Define `HostConfig` — Docker connectivity (`docker_network`, `backend_host`, `backend_scheme`, `docker_host`), port range, named stores (model path + HF cache with host/container path + mode), accelerator inventory (id, type, vram), container defaults (environment dict, options dict). `docker_host` is the canonical managed definition; `.env` `SWITCHYARD_DOCKER_HOST` may override it for the active process.
- [x] **T1.2**: Define `RuntimeConfig` — backend name, container image, CLI flag defaults dict (backend-specific, open schema), container defaults (internal port)
- [x] **T1.3**: Define `ModelConfig` — source (store reference + path within store), model-family defaults (served_model_name, reasoning_parser, tool_call_parser, capabilities), model-specific runtime defaults
- [x] **T1.4**: Define `DeploymentConfig` — references to model/runtime/host by name, runtime overrides dict, storage overrides (alternative path within store), `placement.accelerator_ids` for GPU selection, container overrides (environment, options), `extra_args` escape hatch
- [x] **T1.5**: Define top-level `Config` model with `hosts: dict[str, HostConfig]`, `runtimes: dict[str, RuntimeConfig]`, `models: dict[str, ModelConfig]`, `deployments: dict[str, DeploymentConfig]`
- [x] **T1.6**: Define `AppSettings` (pydantic-settings) for `.env` bootstrap: `config_path`, `log_level`, `api_host`, `api_port`, `active_host`, `docker_host`. `.env` `docker_host` overrides the host's canonical `docker_host` for the active process.
- [x] **T1.7**: Tests: each entity model validates with valid data, rejects missing required fields, enforces constraints

### Phase 2: Config Loader and Reference Resolution

**Goal**: Loader reads YAML, validates cross-entity references, resolves store paths to host paths, and produces a configuration data object.

#### Tasks
- [x] **T2.1**: Implement YAML loader that parses `config.yaml` into the `Config` model (replace current loader)
- [x] **T2.2**: Implement reference resolution — given a deployment, resolve `model: qwen3-27b-fp8` → `ModelConfig`, `runtime: vllm` → `RuntimeConfig`, `host: trainbox` → `HostConfig`
- [x] **T2.3**: Implement store resolution — resolve `source.store: models` against host's `stores.models` to produce two outputs: the host path (for Docker volume mounting) and the container path (passed to the runtime as the model location)
- [x] **T2.4**: Implement cascade merge for runtime config: runtime defaults → model runtime defaults → deployment runtime overrides → `extra_args`
- [x] **T2.5**: Implement cascade merge for container config: host container defaults → deployment container overrides
- [x] **T2.6**: Produce `ResolvedDeployment` dataclass — the complete resolved configuration for a single deployment (all references resolved, paths computed, cascades merged)
- [x] **T2.7**: Tests: reference validation (valid references resolve, unknown model/runtime/host raises error), store resolution (named store produces both host path and container path), cascade merge (overrides take precedence at each layer, defaults fill gaps, merge order verified: runtime → model → deployment → extra_args), `.env` `SWITCHYARD_DOCKER_HOST` overrides `hosts.*.docker_host` for the active process
- [x] **T2.8**: Tests: deployment naming fixture — `qwen3-27b-vllm-trainbox` resolves cleanly with realistic data (all entity references, store resolution, cascade merge)

### Phase 3: Remove Legacy Schema and Update Code

**Goal**: Remove all traces of the old config shape and update dependent code to use the new entity model.

#### Tasks
- [x] **T3.1**: Replace old config hierarchy (`GlobalConfig`, `RuntimeDefaults`, `ModelConfig`) with new entity models. Preserve `VLLMRuntimeConfig`'s typed field validation — moved as standalone class, `LegacyModelConfig`'s model/repo exclusivity validator removed (model path now comes from store resolution)
- [x] **T3.2**: Remove old `config.yaml`; create new `config.yaml` with host/runtime/model/deployment sections. Translated from `reference-then-delete/vLLM/docker-compose.yml` (trainbox: dual-GPU, Qwen3.6-27B-FP8, vLLM latest, docker_host `tcp://127.0.0.1:2375`, port_range [7113, 7213], internal_port 7113, ai_net, host/container paths mirror compose mounts, env vars, IPC, ulimits).
- [x] **T3.3**: Update `config/loader.py` to load new entity models and `.env` settings via pydantic-settings. Removed legacy cascade/merge utilities and `ConfigLoader.load()` implementation. Added `resolve_deployment()` that produces `ResolvedDeployment` with reference lookup, store resolution, cascade merges, and `.env` `docker_host` override
- [x] **T3.4**: Update app bootstrap to read `.env` settings via `AppSettings()` (log level, active host). Added `_resolve_active_host()` to derive backend/docker settings from the active host. Routes updated to check `config.deployments` instead of `config.models`. API host/port remain uvicorn/Makefile concerns
- [x] **T3.5**: `core/docker.py` already uses `AppSettings().docker_host` with fallback. No changes needed. Verified `.env` `SWITCHYARD_DOCKER_HOST` takes precedence over host canonical `docker_host`
- [x] **T3.6**: Define `ResolvedDeployment` as the canonical config output. Adapter protocol updated: `BackendAdapter.start()` and `VLLMAdapter.start()` accept `ResolvedDeployment`. Adapter validates `runtime_args` as `VLLMRuntimeConfig` for CLI building, reads model from resolved container path, mounts all host stores via `store_mounts`, uses `internal_port`, `docker_network`, `accelerator_ids`, `container_environment`, `container_options`, `docker_host`. Uses `resolved.docker_host` for Docker client when none injected. This pulled-forward behavior is documented in Phase 3 Scope Adjustment and covered by Phase 4 tests T4.9, T4.14–T4.16.
- [x] **T3.7**: Updated `core/lifecycle.py` to import and use `Config` and `ResolvedDeployment`. Replaced `LegacyModelConfig` references with `ResolvedDeployment` throughout `load_model()`, `unload_model()`, `get_status()`, `_wait_for_status()`. Auto-start loop removed (stub-only, behavioral change is SEP-003). Updated `core/orphan.py` to scan `config.deployments` instead of `config.models`

### Phase 4: Test Migration and Quality Gates

**Goal**: All existing tests pass against the new config model; no test or code path depends on the old shape.

#### Tasks
- [ ] **T4.1**: Migrate existing config loading tests to new entity model
- [ ] **T4.2**: Migrate existing cascade/override tests to new deployment resolution tests
- [ ] **T4.3**: Migrate adapter tests to use new config fixtures (adapter logic unchanged — SEP-002 does not modify adapter behavior). Include the opt-in CPU smoke test, which currently constructs `ModelConfig`/`VLLMRuntimeConfig` directly and must be migrated to the new resolved deployment shape
- [ ] **T4.4**: Migrate lifecycle tests to new config types (stubs)
- [ ] **T4.5**: Tests: vLLM typed field preservation — known fields still validate, invalid values still fail, unknown flags pass through `extra_args`
- [ ] **T4.6**: Tests: runtime command safety — Switchyard-internal fields (`placement`, `accelerator_ids`, `stores`, `docker_host`) are never emitted as vLLM CLI args
- [ ] **T4.7**: Run quality gates: `uv run pytest`, `uv run ruff check src tests --fix`, `uv run mypy src/switchyard`
- [ ] **T4.8**: Verify no remaining imports or references to old config types (`GlobalConfig`, `RuntimeDefaults`, etc.)
- [ ] **T4.9**: Tests: adapter launch from `ResolvedDeployment` — volume mounts (`model_host_path` → `model_container_path`), device requests (`accelerator_ids`, capabilities format), environment merge, container options, image, internal port
- [ ] **T4.10**: Tests: deployment route loading — `POST /deployments/load` resolves via `resolve_deployment()`, validates deployment exists in config, returns correct response
- [ ] **T4.11**: Tests: OpenAI passthrough preservation — `POST /v1/chat/completions` proxies to backend, streaming support, `POST /v1/backends/{deployment}/{path:path}` routes correctly
- [ ] **T4.12**: Tests: active host selection — `_resolve_active_host()` raises on unknown `SWITCHYARD_ACTIVE_HOST`, falls back to first host when unset, port range wired into `PortAllocator`
- [ ] **T4.13**: Tests: lifecycle state keyed by deployment name — `load_model`/`unload_model`/`get_status`/`_wait_for_status` use `deployment_name` consistently
- [ ] **T4.14**: Tests: extra_args escape hatch — `deployment.extra_args` survives resolution nested as `runtime_args["extra_args"]`, appears in `VLLMRuntimeConfig.extra_args`, and renders as CLI flags in adapter command
- [ ] **T4.15**: Tests: store mounts — resolved deployment includes all host stores in `store_mounts`, adapter mounts both `models` (ro) and `hf_cache` (rw) with correct modes
- [ ] **T4.16**: Tests: docker_host wiring — adapter uses `resolved.docker_host` when no injected Docker client exists, `_backend_url()` uses active host fallback when deployment metadata lacks host/scheme

---

## Phase 3 Scope Adjustment

**Date**: 2026-05-02
**Reason**: Removing the legacy ``LegacyModelConfig`` launch config forced
adapter changes that go beyond signature-only adjustments.
``BackendAdapter.start()`` and ``VLLMAdapter.start()`` now accept
``ResolvedDeployment`` and use its fields for CLI building, volume mounts,
device requests, environment, and container options.

This is an accepted Phase 3 scope expansion caused by removing the legacy
launch config. The alternative — keeping the legacy types to shield the
adapter — would defeat the purpose of Phase 3.

**Impact on Phase 4**: Tests T4.9–T4.16 cover the pulled-forward adapter
behavior: volume mounts, device requests, extra_args escape hatch,
docker_host wiring, OpenAI passthrough, active host selection, and
lifecycle state keyed by deployment name.

---

## Dependencies

### Critical Path
1. T1.1–T1.7 (entity models are foundational; everything else depends on them)
2. T2.1–T2.8 (loader and resolution depend on models)
3. T3.1–T3.7 (code removal and updates depend on loader producing correct output)
4. T4.1–T4.13 (test migration depends on all new code being in place)

### Parallel Work Streams
- T1.1–T1.6 are independent entity models (can be developed in parallel)
- T3.3–T3.6 are independent code updates (all depend on T2 being complete)

---

## Risk Mitigation

### Risk: Breaking config change causes test failures that are hard to debug
- **Mitigation**: Phase 4 migrates tests systematically. Keep a minimal `config.yaml` with one placeholder deployment for smoke testing.
- **Owner**: Agent
- **Timeline**: Phase 3–4

### Risk: Cascade merge logic is subtle and error-prone
- **Mitigation**: Write failing tests first for each merge scenario (defaults only, overrides only, both). Verify against known-good output.
- **Owner**: Agent
- **Timeline**: Phase 2

### Risk: Accidentally absorbing SEP-003 scope (lifecycle changes) into this refactor
- **Mitigation**: SEP-002 defines `ResolvedDeployment` as the canonical resolved config output, but does not wire it into adapter start/stop/health behavior. If a compile-time type adjustment is unavoidable, limit it to a signature-only change with no behavioral impact. Explicit boundary check in Phase 3.
- **Owner**: Agent + User
- **Timeline**: Phase 3

---

## Validation Plan

**Validation Commands**: See `AGENTS.md` Section "Quality Gates" for complete validation command list.

### Success Criteria Validation
- [ ] A `config.yaml` with hosts, runtimes, models, and deployments loads and validates
- [ ] A deployment referencing a model, runtime, and host resolves into a complete `ResolvedDeployment` data object (no lifecycle changes)
- [ ] Reference validation catches broken cross-entity references (unknown host, missing runtime, etc.)
- [ ] `.env` supplies process-local bootstrap values; YAML config owns only entity definitions; `.env` `docker_host` overrides the host's canonical definition for the active process
- [ ] The legacy `global` / `runtime_defaults` / per-model `runtime` schema is fully removed; vLLM typed field validation is preserved in the new schema
- [ ] No test or code path depends on the old config shape
- [ ] All quality gates pass (pytest, ruff, mypy)
- [ ] `make test-vllm-cpu` still passes after config schema migration, using the new resolved deployment shape

---

## Decision Log

| # | Decision | Reason | Status | Date |
|---|----------|--------|--------|------|
| D1 | Single `config.yaml` for all entities; split into separate files later | Reduces complexity for single-host use case; splitting is a mechanical change driven by volume, not architecture | Draft | 2026-04-30 |
| D2 | No semantic serving abstractions (`reasoning.enabled`, `multimodal.enabled`) | Avoids adapter translation layer; typed fields map directly to backend flags, `extra_args` is the escape hatch | Draft | 2026-04-30 |
| D3 | SEP-002 produces `ResolvedDeployment` data object only; no lifecycle changes | Keeps scope contained; SEP-003 owns wiring adapters and starting containers by deployment ID | Draft | 2026-04-30 |
| D4 | No backward compatibility with SEP-001 config shape | Small codebase, no external users; maintaining dual loaders adds complexity with no benefit | Draft | 2026-04-30 |
| D5 | Preserve vLLM typed field validation from SEP-001 in the new schema | High-value parameters remain validated Pydantic fields; moving them is better than discarding and recreating | Draft | 2026-04-30 |
| D6 | `.env` `docker_host` overrides `hosts.*.docker_host` for the active process | Canonical host definition lives in YAML; process-level override via `.env` allows remote Docker development without editing managed config | Draft | 2026-04-30 |

---

This plan serves as the single source of truth during implementation. Update status as work progresses.
