# Epic — API Object Management

**Status**: Future
**Date**: 2026-04-30
**Related Consult**: `SEP-002-04-CONSULT-host-environment-config.md`
**GitHub Issue**: https://github.com/DavidCatalano/switchyard/issues/5

---

## Purpose

Define the API surface for managing Switchyard's durable configuration objects:
hosts, runtimes, models, and deployments. This is its own future epic because it
is larger than the initial YAML data-model refactor and will become the
foundation for the Web UI.

The current MVP API focuses on lifecycle actions around configured models. The
future API should expose object management plus lifecycle operations around
deployments.

---

## Entity Model

Durable config is split by entity type:

```text
switchyard-api/config/
  hosts.yaml
  runtimes.yaml
  models.yaml
  deployments.yaml
```

`.env` owns process-local Switchyard bootstrap settings such as API bind
settings, log level, config directory, and local Docker endpoint overrides.

```text
hosts        = machines/environments Switchyard can target
runtimes     = backend engines and default launch behavior
models       = logical model sources and model-family defaults
deployments  = concrete "run this model with this runtime on this host" records
```

Deployments are the primary lifecycle unit:

```text
deployment = model + runtime + host + placement + overrides
```

---

## Proposed API Surface

### Hosts

```text
GET    /hosts
POST   /hosts
GET    /hosts/{host}
PATCH  /hosts/{host}
DELETE /hosts/{host}
GET    /hosts/{host}/status
GET    /hosts/{host}/resources
```

`/hosts/{host}/resources` can eventually expose discovered GPUs, memory,
Docker reachability, port availability, and active deployments.

### Runtimes

```text
GET    /runtimes
POST   /runtimes
GET    /runtimes/{runtime}
PATCH  /runtimes/{runtime}
DELETE /runtimes/{runtime}
GET    /runtimes/{runtime}/schema
```

`/runtimes/{runtime}/schema` should expose validated high-value fields for the
UI. This keeps the UI aligned with backend-specific Pydantic models.

### Models

```text
GET    /models
POST   /models
GET    /models/{model}
PATCH  /models/{model}
DELETE /models/{model}
GET    /models/{model}/deployments
```

Models are catalog/config objects only. Model lifecycle endpoints from SEP-001
are supplanted by deployment lifecycle endpoints in SEP-002.

### Deployments

```text
GET    /deployments
POST   /deployments
GET    /deployments/{deployment}
PATCH  /deployments/{deployment}
DELETE /deployments/{deployment}

POST   /deployments/{deployment}/load
POST   /deployments/{deployment}/unload
GET    /deployments/{deployment}/status
GET    /deployments/{deployment}/logs
POST   /deployments/{deployment}/dry-run
POST   /deployments/{deployment}/validate
```

`dry-run` should return generated runtime CLI arguments, Docker options, mounts,
environment variables, port binding, and endpoint URLs without starting a
container.

`validate` should check references and common launch risks:

- host exists
- runtime exists
- model exists
- referenced stores exist on the selected host
- selected accelerator IDs exist
- port range can allocate
- typed runtime fields validate
- Switchyard-internal fields are not emitted as runtime CLI flags

### Inference

Continue OpenAI-compatible routing:

```text
POST /v1/chat/completions
```

The request `model` field should resolve to either:

- a deployment ID, or
- an alias mapped to a deployment.

Example:

```json
{
  "model": "qwen3-27b-vllm-trainbox",
  "messages": []
}
```

---

## Implementation Staging

### Stage 1: Internal Data Model

- Add Pydantic models for hosts, runtimes, models, and deployments.
- Resolve deployment config into adapter-ready launch config.
- Keep YAML as the source of truth.
- Replace the SEP-001 config shape with split YAML files under
  `switchyard-api/config/`.

### Stage 2: Read APIs and Lifecycle by Deployment

- Add read endpoints for hosts, runtimes, models, deployments.
- Add `POST /deployments/{deployment}/load`.
- Add `POST /deployments/{deployment}/unload`.
- Add `GET /deployments/{deployment}/status`.
- Remove SEP-001 model lifecycle routes from the target API.

### Stage 3: Write APIs

- Add create/update/delete for each object.
- Validate references and cascades on write.
- Decide whether writes update YAML directly or a future config store.

### Stage 4: Diagnostics

- Add logs endpoint.
- Add dry-run launch preview.
- Add host resource/status endpoint.

---

## Open Questions

- Should write APIs mutate YAML files, a future database, or an in-memory config
  with explicit export?
- Should deployment IDs be user-authored, generated, or both?
- Should OpenAI-compatible `model` route by deployment ID, served model name, or
  an explicit alias registry?
- How much host hardware discovery is part of the API versus entered manually?

---

## Success Criteria

- The lifecycle manager can load and unload by deployment ID.
- Hosts, runtimes, models, and deployments are inspectable through API endpoints.
- SEP-001 model lifecycle routes are replaced by deployment lifecycle routes.
- Generated launch configuration can be previewed before starting a container.
- The API exposes enough object metadata for the future Web UI to avoid parsing
  YAML directly.
