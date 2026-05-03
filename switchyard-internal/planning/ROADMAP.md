# Switchyard Roadmap

## What Switchyard Is

Switchyard is a local inference control plane for model runtimes running on
hardware the user controls. It helps configure, launch, tune, inspect, and route
requests to model runtime containers without scattering operational details
across shell scripts, Docker Compose files, one-off SSH commands, and copied CLI
arguments.

The product center is model deployment ergonomics. Switchyard should make it
easy to define managed hosts, runtimes, models, and deployments; choose where
and how a model should run; inspect the generated launch configuration; start
and stop deployments; and call active deployments through an OpenAI-compatible
API.

The planned Web UI should build on that same model. It should help the user
manage configuration objects, compare deployment options, tune high-value
runtime settings, preview generated commands, inspect health/logs, and test chat
behavior without turning Switchyard into a generic container dashboard.

Switchyard is not intended to become a generic container orchestrator. Docker is
an implementation tool. The user-facing concepts should stay centered on
hosts, runtimes, models, deployments, accelerators, model stores, ports, health,
logs, and generated launch previews.

## Current Architecture

Switchyard is organized around four top-level managed entities:

- **Hosts**: machines or environments that can run inference containers. Hosts
  own Docker connectivity, backend reachability, port ranges, accelerator
  inventory, named stores, and container defaults.
- **Runtimes**: backend engines and launch defaults, such as vLLM, SGLang,
  koboldcpp, or llama.cpp.
- **Models**: logical model sources and model-family defaults. Models should be
  portable across hosts by referring to named stores rather than hardcoded host
  paths.
- **Deployments**: concrete lifecycle units: run this model with this runtime
  on this host with these placement, storage, runtime, and container overrides.

The key relationship is:

```text
model + runtime + host + placement + overrides = deployment
```

A resolved deployment is the assembled launch configuration produced by merging
the deployment's model, runtime, host, stores, defaults, and overrides. Backend
adapters translate resolved deployments into runtime/container operations.

Placement is a nested deployment concern that describes which host resources a
deployment should use. Today that mainly means selected accelerator IDs, such as
GPU `0`, GPU `1`, or both. Placement is part of a deployment because the same
model/runtime pair may run on different hardware slices; it is not a top-level
managed entity like hosts, runtimes, models, or deployments.

## Current State

### SEP-001 — MVP Control Plane

Complete. Established the first API control plane, vLLM adapter, Docker-backed
container lifecycle, health checks, OpenAI-compatible proxying, and smoke-test
foundation. Its config shape has been supplanted by SEP-002.

### SEP-002 — Config Data Model

Complete. Introduced the entity config model with `hosts`, `runtimes`,
`models`, and `deployments`; moved process bootstrap into `.env`; added
resolved deployment wiring; and verified the TinyLlama CPU smoke flow through
the OpenAI-compatible API surface.


## Planned Work

### SEP-003 — Deployment Resolution and Lifecycle

GitHub issue: #3

Finish the deployment lifecycle migration that SEP-002 largely enabled. The
resolver already produces adapter-ready deployments, resolves source/store
paths, merges runtime/model/deployment cascades, and translates placement into
container launch behavior. SEP-003 should focus on the remaining lifecycle and
API work:

- Replace body-based lifecycle routes with deployment-resource routes:
  `POST /deployments/{deployment}/load`,
  `POST /deployments/{deployment}/unload`, and
  `GET /deployments/{deployment}/status`.
- Improve deployment status detail where needed.
- Reconcile in-memory deployment state with actual Docker container state before
  rejecting loads or reporting status.
- Clear stale state and release allocated ports when a managed container is
  removed outside the API.
- Keep smoke tests aligned on deployment IDs.

### SEP-004 — Runtime Resolver Hardening

GitHub issue: #4

Harden runtime-specific launch behavior without chasing every backend CLI flag.
For vLLM, maintain a small typed core for high-value fields, require unknown
backend flags to live under `extra_args`, fail loudly on typos in typed layers,
and provide generated command previews for validation and operator confidence.

Planning reference: `SEP-001-04-CONTEXT-vllm-config.md`

### SEP-005 — Read APIs and Diagnostics

GitHub issue: #5

Expose read and diagnostic API surfaces over the object model: list/detail
views, validation, dry-run, logs, active model/deployment visibility, and host
resource details where manually configured data is sufficient.

Planning reference: `epic-api-object-management.md`

### SEP-006 — Config Write APIs

GitHub issue: #6

Add create/update/delete APIs for hosts, runtimes, models, and deployments.
This owns YAML write strategy, write-time validation, reference checks, cascade
validation, and config reload behavior.

### SEP-007 — Host Resource Discovery

GitHub issue: #7

Discover host facts instead of relying only on hand-entered configuration. This
owns Docker connectivity checks, GPU inventory, port availability, Docker
network existence, model store checks, and Windows/WSL host considerations.

### SEP-008 — Web UI MVP

GitHub issue: #8

Build the first deployment-oriented Web UI: deployments dashboard, host details,
deployment details, lifecycle actions, logs, dry-run preview, and test chat.

Planning reference: `epic-webui.md`

### SEP-009 — Control Plane Deployment

GitHub issue: not yet created

Deploy the Switchyard control plane application to an LXD container on the local
network and add a second Docker-backed host, `devbox`, alongside `trainbox`.

Planning reference: `deploy/systemd/README.md`


## Cold Start Reading

For a new coding session, read the active SEP artifacts first:

1. The active SEP PRD, PLAN, and DEVLOG.
2. Only the CONTEXT or CONSULT files named by the active DEVLOG handoff section.

For future roadmap decomposition, also read the relevant GitHub issue and any
planning reference named in that issue.

## Key Design Decisions

- `.env` owns process-local bootstrap settings needed to start the Switchyard
  process on a machine.
- `config.yaml` owns durable managed entities: hosts, runtimes, models, and
  deployments.
- Docker labels are the authoritative marker for Switchyard-managed containers;
  container names are for human readability.
