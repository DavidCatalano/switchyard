# Product Requirements Document (PRD)

**Title**: Config Data Model Refactor
**ID**: SEP-002-01-PRD-config-data-model
**Status**: Complete
**Date**: 2026-04-30
**Related Docs**: `SEP-002-04-CONSULT-host-environment-config.md`, `SEP-001-04-CONTEXT-vllm-config.md`
**GitHub Issue**: https://github.com/DavidCatalano/switchyard/issues/2
**Dev Track**: Standard

---

## 1. Background

SEP-001 delivered an MVP control plane with a config model (`global` → `runtime_defaults` → per-model `runtime`) designed around vLLM CLI flags. When attempting our first real model profile, we discovered that container-level infrastructure — volumes, environment variables, IPC, ulimits, accelerator selection — has no place in this model. These settings are host-dependent, not model-dependent, and are required before any model can load.

The current model also conflates model identity with deployment context, making it impractical to manage multiple hosts or experiment with alternative runtimes without scattering machine-specific values throughout the config.

This refactor replaces the legacy config shape with a durable entity model that separates hosts, runtimes, models, and deployments.

---

## 2. Scope

### Phase A: Entity Data Model and Config Loader — GH #2
- Introduce explicit entity types: hosts, runtimes, models, deployments
- Hosts define machine-specific infrastructure (Docker connectivity, port ranges, model stores, accelerator inventory, container defaults)
- Runtimes define backend engine defaults and image choices
- Models describe logical model sources via named stores, not absolute host paths
- Deployments bind `{model, runtime, host}` and own concrete tuning overrides
- Implement Pydantic models and a config loader for the new entity schema
- Implement reference validation (deployments resolve to existing models, runtimes, and hosts)
- Move process-local bootstrap settings to `.env` (`SWITCHYARD_CONFIG_PATH`, `SWITCHYARD_LOG_LEVEL`, `SWITCHYARD_API_HOST`, `SWITCHYARD_API_PORT`, `SWITCHYARD_ACTIVE_HOST`, `SWITCHYARD_DOCKER_HOST`)
- Remove the legacy `global` / `runtime_defaults` / `models.<name>.runtime` schema
- Update existing tests, examples, and docs to the new model

**Boundary with SEP-003**: SEP-002 produces the entity config model, resolved deployment object, adapter wiring needed to launch resolved deployments, and MVP smoke-test hardening. Broader lifecycle orchestration, auto-start behavior, and public CRUD management APIs remain SEP-003/future scope.

### Non-Goals
- Public CRUD API for hosts, runtimes, models, or deployments (GH #5, #6)
- Web UI (GH #8)
- Hardware discovery / host diagnostics (GH #7)
- Semantic serving abstractions (e.g., `reasoning.enabled`, `multimodal.enabled`); typed fields map directly to backend flags plus `extra_args`
- SQLite or other persistent config stores
- Migration tooling for legacy configs

### Target Config Shape

The top-level `config.yaml` structure:

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

Each section contains its respective entity definitions. References between entities use entity names (e.g., a deployment references `model: qwen3-27b-fp8`, `runtime: vllm`, `host: trainbox`).

### Dependencies
- SEP-001 control plane codebase and existing vLLM field validation logic
- Python 3.12, uv, Pydantic (already in place)

---

## 3. Implementation Considerations

- The entity model must remain convenient for single-host use while supporting multi-host without code changes
- Named stores abstract host paths away from model definitions, enabling portable model configs
- vLLM field validation from SEP-001 is preserved — high-value parameters remain typed Pydantic fields, `extra_args` remains the escape hatch
- Config will remain a single file during implementation; file splitting is deferred until volume demands it
- This is a breaking config change — no backward compatibility with the SEP-001 shape is required

---

## 4. Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Breaking config change disrupts local development | Medium | Small, contained codebase; no external users |
| Entity resolution complexity introduces subtle loader bugs | Medium | Reference validation at load time; comprehensive unit tests for resolution paths |
| Over-engineering for single-host use case | Medium | Keep single-file option; defer file splitting; measure config size before splitting |

---

## 5. Validation & Done

**Quality gates:**
- `uv run pytest` — all tests pass
- `uv run ruff check src tests --fix` — no lint errors
- `uv run mypy src/switchyard` — no type errors

**Done when:**
- A config with hosts, runtimes, models, and deployments loads and validates successfully
- A deployment referencing a model, runtime, and host resolves all references into a complete configuration data object used by the adapter launch path
- Reference validation catches broken cross-entity references (missing host, unknown runtime, etc.)
- `.env` supplies process-local bootstrap values (`SWITCHYARD_CONFIG_PATH`, `SWITCHYARD_LOG_LEVEL`, `SWITCHYARD_API_HOST`, `SWITCHYARD_API_PORT`, `SWITCHYARD_ACTIVE_HOST`, `SWITCHYARD_DOCKER_HOST`); YAML config owns only hosts, runtimes, models, and deployments
- The legacy `global` / `runtime_defaults` / per-model `runtime` schema is fully removed
- No test or code path depends on the old config shape
