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

---

## Cold Start / Handoff

Read `SEP-002-02-PLAN-config-data-model.md`; Phase 2 is next. Use
`switchyard-api/src/switchyard/config/models.py` and
`switchyard-api/tests/test_entity_models.py` for the Phase 1 baseline.

Carry forward: the PRD still has a stale `placements` example; the top-level
entity is `deployments`.
