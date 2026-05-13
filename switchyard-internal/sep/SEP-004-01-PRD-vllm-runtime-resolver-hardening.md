# Product Requirements Document (PRD)

**Title**: vLLM Runtime Resolver Hardening
**ID**: SEP-004-01-PRD-vllm-runtime-resolver-hardening
**Status**: Complete
**Date**: 2026-05-11
**Related Docs**: `SEP-004-05-CONTEXT-runtime-field-audit.md`, `SEP-001-04-CONTEXT-vllm-config.md`
**Dev Track**: Standard

---

## 1. Background

SEP-002 introduced the entity config model and cascade-merged runtime resolution. SEP-003 completed the deployment lifecycle migration. Both resolved `VLLMRuntimeConfig` through a loose `dict[str, Any]` pipeline: runtime args merge as untyped dicts, the adapter validates at build time, and unknown YAML keys slip through silently.

The runtime resolver needs hardening before the Web UI (SEP-008) depends on it. The UI will render per-field inputs from a schema — if the resolver tolerates typos, passes Switchyard-internal fields to the CLI, or silently drops partial nested overrides, operators will lose trust in the generated launch configuration.

This work tightens the typed layer, enforces the two-bucket contract, and provides the visibility surface (preview API) that protects operators from cascade propagation surprises.

---

## 2. Scope

### Phase A: Schema Hardening — GH #4
- Add `model_config = {"extra": "forbid"}` to `VLLMRuntimeConfig` so top-level YAML typos reject at deployment resolution rather than adapter start, surfacing errors before any Docker call.
- Replace the hardcoded skip tuple in `_build_cli_args` with a structural guarantee that Switchyard-only fields (currently `device`) never serialize to CLI. Use Pydantic `Field(json_schema_extra={"switchyard_only": True})` per field so the adapter derives the skip set from model metadata and the eventual Web UI (SEP-008) can introspect operator-facing vs. internal fields for free from the JSON schema.
- Fix `extra_args` dict serialization: dict values inside `extra_args` must JSON-serialize (matching named dict fields), not fall through to `str(value)` which produces Python repr.
- Normalize `extra_args` keys through the same snake-case → kebab-case conversion as named fields so `--disable_log_requests` doesn't silently fail against vLLM.

### Phase B: Cascade Contract — GH #4
- Replace shallow `dict.update` cascade merge with deep merge for nested dicts (`speculative_config`, `limit_mm_per_prompt`). Partial deployment-level overrides must not wipe lower-layer nested values.
- `null` as wipe signal: setting a nested field to `null` at any layer fully resets it (equivalent to Helm/Kustomize `null` semantics). A higher layer setting `speculative_config: null` wipes all lower-layer entries.
- `extra_args` cascade: treat `extra_args` as a nested dict subject to the same deep-merge rules. `deployment.extra_args` overlays (and can wipe with `null`) lower-layer `extra_args` entries rather than replacing the entire dict.
- Maintain last-in-wins semantics at the top level: `model.defaults → runtime.defaults → model.runtime_defaults → deployment.runtime_overrides`. Rationale: `runtime.defaults` overrides `model.defaults` because runtime defaults describe how the engine wants to run; `model.runtime_defaults` exists as the explicit slot when a specific model needs to override runtime-level settings (currently unused but reserved).

### Phase C: Preview API — GH #4
- Expose a structured JSON endpoint returning the full container launch spec for a named deployment: image, command/args (rendered CLI flags), environment, mounts, network, port, accelerator placement. Equivalent to what would appear in a hand-written `docker-compose.yml`.
- Exact launch spec parity: the preview `command` field matches exactly what Docker SDK receives (e.g., `["--host", "0.0.0.0", "--port", "8000", ...]`). The image's `ENTRYPOINT` (e.g., `vllm serve`) is not prepended — it's implicit in the image.
- Port semantics: if deployment is running, return actual reconciled host port; if stopped, return `host_port: null` with `port_range` context. No mutating the allocator state during a read.
- Exclude Switchyard process-local settings (`.env`-sourced `SWITCHYARD_*` variables).
- Sensitive field redaction: `hf_token`, `api_key`, and other secret-shaped fields must be redacted in both `command` (flag/value pair pattern) and `environment` (dict key pattern).
- Endpoint shape: `GET /api/deployments/{deployment}/preview`.

### Non-Goals
- Other runtimes (SGLang, koboldcpp, llama.cpp).
- Config Write APIs (SEP-006) — no deployment creation/deletion events yet.
- Schema introspection endpoint (`GET /api/runtimes/{runtime}/schema`) — relevant but deferred; the preview API is the immediate visibility mechanism.
- `tensor_parallel_size` × `accelerator_ids` correlation validation (noted in audit, natural home is SEP-007).

### Dependencies
- SEP-002 (Config Data Model) — shipped.
- SEP-003 (Deployment Resolution and Lifecycle) — shipped.
- SEP-003a (Health-Aware Deployment Status) — complete.

---

## 3. Implementation Considerations

- The resolver stays stateless. `ResolvedDeployment` is rebuilt fresh per operation (load, reconcile, preview). No snapshotting or freeze-at-creation.
- `VLLMRuntimeConfig` is the canonical typed boundary. `resolve_deployment()` validates merged runtime args via `VLLMRuntimeConfig.model_validate()` before constructing `ResolvedDeployment`, so typos surface before any Docker call.
- The adapter owns launch construction. `VLLMAdapter` provides a `build_launch_spec(resolved)` method returning the full structured launch dict (command, env, mounts, network, device requests, labels, name). `start()` consumes this dict; the preview API returns it as JSON. This shared path prevents drift between preview and actual launch.

---

## 4. Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Deep merge changes break existing deployments that rely on shallow `update` wiping nested dicts | Medium | Audit existing `config.yaml` deployments for nested dict overrides; tests must cover both merge and wipe cases |
| `extra: "forbid"` rejects valid config in environments using undocumented flags | Low | Operators using undocumented flags should already be putting them in `extra_args`; any rejection surfaces at config load with clear error |
| Preview API exposes `hf_token` value in plain text | Medium | Reuse existing `_mask_sensitive_args` (app.py:425-442) to redact secret-shaped fields in preview responses; same mechanism used by `/api/deployments/{id}` |
| Cascade typo errors reference the merged dict, not the specific YAML layer that introduced the bad key | Low | PLAN should consider tracking provenance during merge so diagnostics name the source layer (e.g., "unknown key 'max_seqs' in deployment.runtime_overrides") |
| Preview API drifts from actual launch command if `_build_cli_args` changes | Medium | Preview and `start()` must share the same CLI-building path; test them against the same `VLLMRuntimeConfig` fixtures |

---

## 5. Validation & Done

**Quality gates:**
- `uv run pytest` — no regressions; new tests for typo rejection, `extra_args` serialization, deep merge, preview endpoint, CLI skip guarantees
- `uv run ruff check src tests --fix`
- `uv run mypy src/switchyard`
- CPU smoke test (tinyllama) and GPU smoke test (qwen3.6-27b-fp8) pass through the hardened resolver (in `tests/live/`)

**Done when:**
- A YAML runtime override with an unknown top-level key fails config load with a clear Pydantic validation error
- `device` and any future Switchyard-only fields are provably excluded from CLI args by structure, not by string matching
- A deployment with partial nested dict override (e.g., only `num_speculative_tokens`) produces a merged `speculative_config` that retains lower-layer keys
- `extra_args` dict values render as valid JSON on the CLI; `extra_args` keys render as kebab-case flags
- The preview endpoint returns a complete container spec that matches what `start()` would launch (same image, command, env, mounts, network, placement)
