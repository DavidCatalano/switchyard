# Devlog — SEP-002 Config Data Model Refactor

**Title**: Config Data Model Refactor
**ID**: SEP-002-03-DEVLOG-config-data-model
**PRD**: `SEP-002-01-PRD-config-data-model.md`
**PLAN**: `SEP-002-02-PLAN-config-data-model.md`

---

## Entries

- 2026-05-01 — PRD, CONSULT, and PLAN are drafted for the entity-based config
  refactor. Implementation has not started; use the PLAN checklist as the source
  of task progress.

---

## Cold Start / Handoff

Read `SEP-002-01-PRD-config-data-model.md`, then
`SEP-002-02-PLAN-config-data-model.md`. Use
`SEP-002-04-CONSULT-host-environment-config.md` only for rationale and concrete
schema examples; the PLAN is the implementation source of truth. Before coding,
inspect `switchyard-api/src/switchyard/config/` and relevant tests directly.

Carry forward: top-level entity name is `deployments`, not `placements`; if the
PRD still shows `placements` in an example, treat that as stale. SEP-002 owns the
config model and resolved deployment object only. Do not wire deployment
lifecycle behavior into adapters/routes; that belongs to SEP-003. Preserve
SEP-001 vLLM typed field validation and ensure Switchyard-internal fields such
as `placement`, `accelerator_ids`, `stores`, and `docker_host` are not emitted as
runtime CLI args.
