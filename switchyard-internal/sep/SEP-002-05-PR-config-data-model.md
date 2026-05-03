# PR — SEP-002 Config Data Model Refactor

**Branch**: `sep/002`
**Base**: `main`

---

## Summary

Replaces the SEP-001 config shape (`global` → `runtime_defaults` → per-model `runtime`) with an entity-based model defining `hosts`, `runtimes`, `models`, and `deployments`. Process-local bootstrap settings move to `.env` via `AppSettings` (pydantic-settings). The loader resolves cross-entity references into a complete `ResolvedDeployment` data object wired into the adapter and lifecycle layers.

## Changes

### Entity Data Model and Config Loader
- New Pydantic models: `HostConfig`, `RuntimeConfig`, `ModelConfig`, `DeploymentConfig`, top-level `Config`
- `AppSettings` (pydantic-settings) reads `.env` for process-local bootstrap (`config_path`, `log_level`, `api_host`, `api_port`, `active_host`, `docker_host`) with `extra="forbid"`
- `ConfigLoader.load()` parses YAML into `Config`
- `resolve_deployment()` produces `ResolvedDeployment` with reference lookup, store resolution, cascade merges, and `.env` `docker_host` override

### Legacy Schema Removal
- `GlobalConfig`, `RuntimeDefaults`, `LegacyModelConfig` fully removed
- `VLLMRuntimeConfig` preserved as standalone typed validation class
- `config.yaml` rewritten with entity sections (hosts, runtimes, models, deployments)
- All code paths updated to new types

### Adapter and Lifecycle Wiring
- `BackendAdapter.start()` and `VLLMAdapter.start()` accept `ResolvedDeployment`
- Adapter uses resolved fields for CLI building, volume mounts, device requests, environment, container options, and Docker client
- Containers carry `switchyard.*` ownership labels (force-applied after user merge)
- Containers named `switchyard-{deployment_name}` for operational readability

### Closeout Hardening
- `LifecycleManager.load_model()` releases allocated port on adapter start failure (T5.1)
- `POST /deployments/load` returns structured JSON on `RuntimeError`; non-`RuntimeError` bugs surface as real server errors (T5.2)
- Makefile `docker-ps`/`docker-clean` filter by `label=switchyard.managed=true`; `DOCKER_NETWORK` variable removed (T5.4)
- `GET /v1/models` returns OpenAI-compatible list of running deployments, sorted by deployment ID (T5.5/T5.6)
- `.env` contract audited: only 6 supported keys, unknown keys fail (T5.7)
- TinyLlama CPU smoke verified: load, list, `/v1/models`, chat completion, unload (T5.11)

## Validation

- **Tests**: 238 passed, 1 skipped (Docker integration)
- **Lint**: `uv run ruff check src tests` — clean
- **Types**: `uv run mypy src/switchyard` — clean
- **Manual smoke**: TinyLlama CPU load/chat/unload through API verified

## Risks

- Breaking config change: no backward compatibility with SEP-001 shape. Small codebase, no external users.
