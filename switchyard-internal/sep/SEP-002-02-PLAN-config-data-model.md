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
- [ ] **T3.1**: Replace old config hierarchy (`GlobalConfig`, `RuntimeDefaults`, `ModelConfig`) with new entity models. Preserve `VLLMRuntimeConfig`'s typed field validation — move/rename it into the new runtime/deployment schema rather than discarding it
- [ ] **T3.2**: Remove old `config.yaml`; create new `config.yaml` with host/runtime/model/deployment sections. Do not create an empty config. Create a populated config that reflects the known-good compose setup, translated into the SEP-002 entity model. Use `SEP-002-04-CONSULT-host-environment-config.md` for structure, but use `reference-then-delete/vLLM/docker-compose.yml` for concrete trainbox operational values.
- [ ] **T3.3**: Update `config/loader.py` to load new entity models and `.env` settings via pydantic-settings
- [ ] **T3.4**: Update app bootstrap to read `.env` settings (log level, active host). Do not force the app layer to own process binding — API host/port remain uvicorn/Makefile concerns
- [ ] **T3.5**: Update `core/docker.py` to respect `.env` `docker_host` override above the host's canonical `docker_host`
- [ ] **T3.6**: Define `ResolvedDeployment` as the canonical config output. Do not wire it into adapter or lifecycle logic — that belongs to SEP-003. If a compile-time type adjustment is unavoidable, limit it to a signature-only change with no behavioral impact
- [ ] **T3.7**: Update `core/lifecycle.py` references to new config types (stub only, no lifecycle behavior changes)

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

---

## Dependencies

### Critical Path
1. T1.1–T1.7 (entity models are foundational; everything else depends on them)
2. T2.1–T2.8 (loader and resolution depend on models)
3. T3.1–T3.7 (code removal and updates depend on loader producing correct output)
4. T4.1–T4.8 (test migration depends on all new code being in place)

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
