# Devlog — SEP-002 Config Data Model Refactor

**Title**: Config Data Model Refactor
**ID**: SEP-002-03-DEVLOG-config-data-model
**PRD**: `SEP-002-01-PRD-config-data-model.md`
**PLAN**: `SEP-002-02-PLAN-config-data-model.md`

---

## Entries

- 2026-05-02 — Phase 1 complete. Entity models and constraint tests added.
  260 tests pass, 4 skipped, ruff/mypy clean.
- 2026-05-02 — Phase 2 complete (T2.1–T2.8). `ResolvedDeployment`,
  `ConfigLoader`, and `resolve_deployment()` added. 289 tests pass,
  ruff/mypy clean.
- 2026-05-02 — Hardening pass complete. Reference validation moved to load
  time, resolved deployment identity fields added, store path validation
  tightened, and AppSettings consolidated. 299 tests pass, ruff/mypy clean.
- 2026-05-02 — Windows path hardening complete. Relative model/storage paths
  reject Windows absolute paths and backslash traversal while host store paths
  remain unrestricted. 304 tests pass, ruff/mypy clean.
- 2026-05-02 — Phase 3 complete (T3.1–T3.7). Legacy config types removed;
  adapter/lifecycle wiring to `ResolvedDeployment` was accepted as a Phase 3
  scope expansion. Legacy test migration deferred to Phase 4.
- 2026-05-02 — Post-review fixes complete. `extra_args`, `store_mounts`,
  `docker_host`, active-host fallback, OpenAI passthrough, and port-range
  wiring were corrected. 117 tests pass, ruff/mypy clean.
- 2026-05-02 — Phase 4 complete (T4.1–T4.16). Legacy tests migrated to the
  entity/deployment model; two superseded config test files deleted. 209 tests
  pass, 3 Docker integration tests skipped, ruff/mypy clean.
- 2026-05-02 — Post-review findings fixes:
  - test_vllm_integration.py:94 — _build_cli_args() called with dict instead of
    VLLMRuntimeConfig; fixed with model_validate() in integration tests
  - test_vllm_adapter.py — added test_start_passes_docker_kwargs() covering
    T4.9 (volumes, devices, env), T4.15 (store_mounts), T4.16 (docker_host)
  - test_api.py — removed app.state.manager replacement after routes registered;
    now uses real captured manager's state intentionally
  - test_proxy.py — added positive proxy tests for non-streaming chat, streaming
    chat, and backend passthrough (T4.11 coverage restored)
  - test_errors.py — replaced mock manager with real manager state; added
    upstream timeout (504) and backend passthrough error tests
  - pyproject.toml — registered pytest.mark.integration marker
  218 tests pass, 3 skipped, ruff/mypy clean.
- 2026-05-02 — Second round of post-review findings fixes:
  - app.py — fixed _streaming_proxy() to use client.stream() inside generator
    context, so upstream response streams transparently instead of buffering
  - test_proxy.py — assert forwarded URL/body in non-streaming proxy, streaming
    proxy, and backend passthrough tests
  - test_vllm_adapter.py — added test_start_uses_docker_host_from_resolved()
    with no injected client, asserting DockerClient called with
    base_url=resolved.docker_host (T4.16 coverage complete)
  219 tests pass, 3 skipped, ruff/mypy clean.
- 2026-05-02 — Streaming proxy setup errors now return 503/504 before response
  start; added streaming connect/timeout tests. 221 tests pass, 3 skipped,
  ruff/mypy clean.

---

## Cold Start / Handoff

SEP-002 implementation phases are complete. Confirm review findings before
closeout.

Key artifacts:
- `SEP-002-01-PRD-config-data-model.md`
- `SEP-002-02-PLAN-config-data-model.md`
- `switchyard-api/config.yaml`
- `switchyard-api/src/switchyard/config/models.py`
- `switchyard-api/src/switchyard/config/loader.py`
- `switchyard-api/src/switchyard/adapters/vllm.py`
- `switchyard-api/src/switchyard/app.py`

Carry forward: the PRD still has a stale `placements` example; the top-level
entity is `deployments`.
