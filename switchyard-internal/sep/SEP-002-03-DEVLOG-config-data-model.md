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
- 2026-05-02 — Post-review test fixes restored adapter, proxy, error, and
  Docker-host coverage. 218 tests pass, 3 skipped, ruff/mypy clean.
- 2026-05-02 — Streaming proxy and resolved Docker-host coverage tightened. 219
  tests pass, 3 skipped, ruff/mypy clean.
- 2026-05-02 — Streaming proxy setup errors now return 503/504 before response
  start; added streaming connect/timeout tests. 221 tests pass, 3 skipped,
  ruff/mypy clean.

---

## Cold Start / Handoff

Phase 5 closeout remains: runtime cleanup, label-based Docker operations,
`GET /v1/models`, strict `.env` cleanup, final gates, and TinyLlama CPU smoke.

Pre-read:
- `SEP-002-01-PRD-config-data-model.md`
- `SEP-002-02-PLAN-config-data-model.md`
- `SEP-002-03-DEVLOG-config-data-model.md`
- `switchyard-api/config.yaml`
