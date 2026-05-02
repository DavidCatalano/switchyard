# Devlog — SEP-002 Config Data Model Refactor

**Title**: Config Data Model Refactor
**ID**: SEP-002-03-DEVLOG-config-data-model
**PRD**: `SEP-002-01-PRD-config-data-model.md`
**PLAN**: `SEP-002-02-PLAN-config-data-model.md`

---

## Entries

- 2026-05-02 — Phase 1 complete. Entity models, constraint validators, and
  negative tests are in place; 260 tests pass, 4 skipped, ruff/mypy clean.
- 2026-05-02 — Phase 2 complete (T2.1–T2.8). Added `ResolvedDeployment` dataclass,
  `ConfigLoader.load_entity_config()`, and `resolve_deployment()` with full
  reference resolution, store path resolution, runtime cascade merge,
  container cascade merge, and .env docker_host override. 18 resolution tests.
  289 tests pass, ruff/mypy clean.
- 2026-05-02 — Hardening pass for 4 findings:
  - Cross-entity references now validated at load time (not resolve time)
  - ResolvedDeployment gains runtime_name, backend_host, backend_scheme, port_range
  - Store paths reject absolute paths and .. traversal; trailing slash normalization
  - AppSettings consolidated into models.py (was duplicated in loader.py)
  299 tests pass, ruff/mypy clean.
- 2026-05-02 — Windows path hardening. _require_safe_relative_path() now normalizes
  backslashes to slashes before checking, rejecting C:/ and C:\\ drive roots,
  ..\\escape, and foo\\..\\bar. Store host_path remains unrestricted (D:/LLM/models OK).
  304 tests pass, ruff/mypy clean.
- 2026-05-02 — Phase 3 complete (T3.1–T3.7). Legacy config types fully removed.
  loader.py refactored with resolve_deployment() producing ResolvedDeployment.
  app.py uses AppSettings() for .env, _resolve_active_host() for host selection,
  routes check config.deployments. BackendAdapter/VLLMAdapter accept
  ResolvedDeployment; adapter validates runtime_args as VLLMRuntimeConfig.
  lifecycle.py/orphan.py updated to new types. 114 entity-model tests pass.
  Legacy tests (~35 failures + 11 import errors) deferred to Phase 4.
- 2026-05-02 — Post-review fixes:
  - vllm.py:214 — model_config.image → resolved.image (hard crash)
  - vllm.py:192 — device_ids/capabilities type fix (mypy catch)
  - vllm.py:169 — volume mount model_host_path → model_container_path added
  - app.py:82 — port_range wired into PortAllocator from active host
  - app.py — restored OpenAI passthrough routes (/v1/chat/completions,
    /v1/backends/{deployment}/{path:path})
  - app.py — SWITCHYARD_ACTIVE_HOST now fails loudly if host not in config
  - lifecycle.py — _health_poll uses deployment_name consistently
  - PLAN revised honestly: T3.6 pulled forward adapter changes (not signature-only),
    Phase 3 Scope Adjustment section added, T4.9–T4.13 test tasks added
- 2026-05-02 — Second round of fixes from detailed review:
  - loader.py — extra_args now nested as runtime_args["extra_args"] to preserve
    the escape hatch (VLLMRuntimeConfig reads from that key). Typed layers merge
    as top-level fields.
  - vllm.py — adapter uses resolved.docker_host for Docker client creation when
    none injected (was silently falling through to .env fallback only).
  - models.py/loader.py — added store_mounts to ResolvedDeployment, populated
    from all host stores. Adapter mounts all stores (not just model path).
  - app.py — _backend_url() now falls back to active host config via
    _resolve_active_host() instead of hardcoded localhost/http.
  - app.py — updated stale comments (no longer claim legacy/signature-only behavior).
  - PLAN — revised scope note language ("accepted Phase 3 scope expansion"),
    added T4.14–T4.16 test tasks.
  - tests — updated test_extra_args_append, added test_store_mounts_includes_all_host_stores,
    test_extra_args_survive_as_vllm_config, test_extra_args_rendered_as_cli_flags,
    realistic fixture store_mounts assertions.
  117 tests pass, ruff/mypy clean.

---

## Cold Start / Handoff

Phase 4 (test migration) is next. Read `SEP-002-02-PLAN-config-data-model.md`
for T4.1–T4.16 task details (Phase 3 Scope Adjustment + T4.14–T4.16 added).

Key files for test migration:
- `switchyard-api/src/switchyard/config/models.py` — entity models (with store_mounts on ResolvedDeployment)
- `switchyard-api/src/switchyard/config/loader.py` — ConfigLoader + resolve_deployment (extra_args nested, store_mounts populated)
- `switchyard-api/src/switchyard/adapters/vllm.py` — adapter accepts ResolvedDeployment (docker_host, store_mounts, CLI args)
- `switchyard-api/src/switchyard/app.py` — active host selection, _backend_url() active host fallback, passthrough routes
- `switchyard-api/src/switchyard/core/lifecycle.py` — lifecycle uses Config/ResolvedDeployment
- `switchyard-api/tests/test_config_resolution.py` — Phase 2 tests + new extra_args/store_mounts tests (117 pass)
- `switchyard-api/tests/test_config_loader.py` — legacy loader tests (Phase 4 migrate)
- `switchyard-api/tests/test_app.py` — legacy app tests (Phase 4 migrate)

Carry forward: the PRD still has a stale `placements` example; the top-level
entity is `deployments`.
