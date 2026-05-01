# Epic — Switchyard Web UI

**Status**: Future
**Date**: 2026-04-30
**Related Consult**: `SEP-002-04-CONSULT-host-environment-config.md`
**GitHub Issue**: https://github.com/DavidCatalano/switchyard/issues/8

---

## Purpose

Build a web UI that makes Switchyard feel like a model deployment tuning tool,
not a general container manager. The UI should help users configure, launch,
compare, and inspect model runtime deployments across local and remote
inference hosts.

The initial user profile is a small number of high-control inference machines:
for example, a dual-GPU inference server and a Windows/WSL laptop with a
smaller GPU. The UI should make those machines understandable at a glance while
still exposing enough detail to tune each model/runtime combination.

---

## Product Direction

The primary object in the UI should be the deployment:

```text
model + runtime + host + placement + tuning overrides
```

Example deployment names:

```text
qwen3-27b-vllm-trainbox
qwen3-27b-sglang-trainbox
qwen3-27b-vllm-laptop
```

The UI should organize around practical workflows:

- See what is running and where.
- Launch or stop a deployment.
- Tune context length, memory policy, concurrency, and runtime behavior.
- Compare the same model across runtimes or hosts.
- Inspect generated runtime/container launch configuration before running.
- Test chat completions against a running deployment.

---

## Primary Views

### Deployments Dashboard

First screen. Dense, operational, and status-oriented.

Example row fields:

```text
qwen3-27b-vllm-trainbox   Running   vLLM    Trainbox   GPU 0,1   100K ctx   4 seqs
qwen3-27b-sglang-trainbox Stopped   SGLang  Trainbox   GPU 0,1   64K ctx
mistral-7b-kobold-laptop  Stopped   Kobold  Laptop     GPU 0     32K ctx
```

Expected actions:

```text
Load | Unload | Logs | Test Chat | Duplicate | Edit
```

Useful states:

- running
- stopped
- loading
- error
- unhealthy
- port conflict
- insufficient VRAM
- host unreachable

### Host View

Shows one machine/environment.

Key sections:

- Host identity and connection health.
- Docker/socket/backend reachability.
- GPU inventory with VRAM used/free.
- Active deployments and placement.
- Model stores and cache stores.
- Docker network and port range.
- Recent launch failures and health warnings.

This view should answer: "Can this host run the deployment I want?"

### Model Catalog

Shows portable logical model definitions.

Fields:

- Model name and family.
- Artifact store and path/repo.
- Capabilities: text, vision, reasoning, tools, embeddings.
- Default served model name.
- Default parsers/templates.
- Compatible runtimes.
- Known deployment profiles.

This view should avoid absolute host paths where possible. Models should refer
to named stores that each host resolves.

### Runtime Catalog

Shows backend engines and default launch behavior.

Examples:

- vLLM GPU
- vLLM CPU
- SGLang
- koboldcpp
- ExLlamaV2

Fields:

- Backend engine.
- Default image.
- Internal port.
- Known high-value settings.
- Default runtime parameters.
- Supported device modes.
- Advanced extra args.

### Deployment Editor

The deployment editor is the core tuning experience.

Suggested tabs:

```text
Overview
Placement
Runtime
Memory & Context
Capabilities
Container
Advanced
```

`Overview`:

- Deployment name.
- Model selector.
- Runtime selector.
- Host selector.
- Served model alias.
- Startup mode.

`Placement`:

- Host selector.
- GPU selector with VRAM visualization.
- Tensor parallel recommendation.
- Fit warnings.
- Port selection or allocation preview.

`Runtime`:

- Runtime image/tag.
- Typed high-value runtime parameters.
- Runtime-specific validation.
- Generated runtime command preview.

`Memory & Context`:

- Max context length.
- GPU memory utilization.
- KV cache dtype.
- Max concurrent sequences.
- Estimated memory pressure.

`Capabilities`:

- Reasoning enabled.
- Reasoning parser.
- Tool-call parser.
- Vision/multimodal enabled.
- Language-model-only toggle.
- Embeddings/pooling capability where applicable.

`Container`:

- Model store mount.
- Hugging Face cache mount.
- Environment variables.
- IPC/ulimits and advanced container settings.
- Generated Docker options preview.

`Advanced`:

- Validated runtime fields not shown elsewhere.
- Raw `extra_args`.
- Full generated launch preview.
- Diagnostics and dry-run validation.

### Test Chat View

Small but important validation workflow:

- Select running deployment.
- Send chat completion.
- Toggle streaming.
- Show latency, token counts, and backend status.
- Surface raw response for debugging.

---

## UX Principles

- Lead with model deployment concepts, not Docker mechanics.
- Expose Docker/container details as previews and advanced options.
- Make host fit and runtime constraints visible before launch.
- Prefer typed controls for high-value settings.
- Keep `extra_args` available but clearly advanced.
- Make duplication cheap: users will often clone a working deployment and tweak
  one axis such as runtime, host, context length, or GPU placement.

---

## Dependencies

This epic depends on a stronger API/object model:

- Hosts CRUD and health.
- Runtimes CRUD.
- Models CRUD.
- Deployments CRUD.
- Deployment lifecycle actions.
- Logs/status/diagnostics endpoints.

See `epic-api-object-management.md`.

---

## Open Questions

- Should the first UI be read-only plus lifecycle actions, or include full YAML
  editing immediately?
- Should the UI write to YAML files directly, or to an API-managed config store?
- Should launch previews be generated server-side so CLI/API/UI agree exactly?
- How much hardware discovery should be automatic versus user-entered?

---

## Success Criteria

- A user can see every configured deployment and its current state.
- A user can launch, unload, inspect logs, and test chat for a deployment.
- A user can understand host GPU/resource availability before launch.
- A user can create a new deployment by combining model, runtime, and host.
- Advanced users can inspect and override generated runtime/container settings.
