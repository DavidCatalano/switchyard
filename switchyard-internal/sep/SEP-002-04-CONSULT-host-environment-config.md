# Consultation Document — Host Environment Configuration

**Title**: Extending Config Cascade to Cover Container-Level Host Infrastructure
**ID**: SEP-002-04-CONSULT-host-environment-config
**Date**: 2026-04-30
**Consultant**: Codex
**PRD**: None (SEP-001 was Lightweight; SEP-002 not yet initiated)
**Predecessor**: None

---

## Question

How should Switchyard model the relationship between process-local bootstrap
settings, host machines, runtime engines, model sources, and concrete deployments so
that the system remains convenient for a single inference host while still
supporting multiple machines and runtime experiments later?

The immediate problem is host-level container infrastructure: volumes,
environment variables, Docker network settings, IPC, ulimits, accelerator
selection, and backend reachability. The broader design problem is that these
settings should not be scattered through model definitions or hardcoded in
adapter code.

---

## Context

### What We Built (SEP-001)

The MVP control plane (`SEP-001`) implements a three-level config cascade for vLLM CLI flags:

```
global → runtime_defaults.{backend} → models.{name}.runtime
```

This works well for parameters like `gpu_memory_utilization`, `tensor_parallel_size`, `kv_cache_dtype`, etc. These are all translated to `--kebab-case` CLI arguments by the `VLLMAdapter`.

**Current `global` section** (`GlobalConfig` in `switchyard-api/src/switchyard/config/models.py`):

```yaml
global:
  docker_network: model-runtime
  base_port: 8000
  log_level: info
  backend_host: localhost
  backend_scheme: http
```

**Current per-model `runtime` section** (`VLLMRuntimeConfig`): ~40 named Pydantic fields covering all Tier 1 and Tier 2 vLLM CLI flags, plus `extra_args` catch-all.

### The Gap: Container Infrastructure Settings

During the first real profile attempt (Qwen 3.6 27B FP8 on a dual-GPU host), we identified settings that have **nothing to do with vLLM CLI flags** but are required for the container to actually run:

**Volume mounts:**
```yaml
# Host path must be mounted into the container
/data/LLM/oobabooga:/data/LLM/oobabooga:ro
/data/LLM/huggingface:/data/LLM/huggingface:rw
```

**Environment variables:**
```yaml
HF_HOME: /data/LLM/huggingface
PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True
CUDA_VISIBLE_DEVICES: "0,1"
VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS: "1"
```

**Container-level Docker options:**
```yaml
ipc: host
ulimits:
  memlock: { soft: -1, hard: -1 }
device_requests:
  - driver: nvidia
    count: -1  # or specific devices
    capabilities: [gpu]
```

### Why This Is Non-Trivial

These settings are **host-dependent**, not model-dependent. Two example deployment machines:

| Setting | Trainbox (dual GPU) | Laptop (single GPU) |
|---------|-----------------|-------------------|
| `CUDA_VISIBLE_DEVICES` | `0,1` | `0` |
| `tensor_parallel_size` | `2` | `1` |
| Volume mounts | `/data/LLM/oobabooga:ro` | `/models:ro` |
| `HF_HOME` | `/data/LLM/huggingface` | `~/.cache/huggingface` |
| Model path | `/data/LLM/oobabooga/models/Qwen3.6-27B-FP8` | `/models/Qwen3.6-27B-FP8` |

The model's `runtime` settings (max context, reasoning parser, tool call parser, speculative config) are **identical** across machines. Only the host infrastructure differs.

Currently, the YAML config is effectively a single file. If someone deploys to a second machine, they'd need to edit many values scattered across `global`, `runtime_defaults`, and every model's `runtime` block.

### What CONTEXT Research Didn't Cover

`SEP-001-04-CONTEXT-vllm-config.md` was explicitly scoped to vLLM launch parameters (CLI flags). Container-level Docker concerns (volumes, env vars, IPC, ulimits, device requests) were outside its scope. This consult fills that gap.

---

## Consultant Response — Codex

The stronger framing is not "where do Docker options go?" It is an entity
model problem. The MVP currently has an implicit chain:

```text
model name -> backend -> runtime config -> container
```

That was enough for one host and one runtime. A durable foundation should model
the real relationship Switchyard will manage:

```text
Run this model, with this runtime, on this host, using this storage/GPU profile,
with these model-specific and deployment-specific optimizations.
```

The durable config should not retain a `global` group for SEP-002. The current
`global` fields are all better handled elsewhere:

- `docker_network` belongs to host configuration.
- `backend_host` and `backend_scheme` belong to host configuration.
- `base_port` becomes a host `port_range`.
- `log_level` belongs in `.env` as process-local bootstrap configuration.

`.env` should answer: "how does this Switchyard process start on this machine?"
`config.yaml` should answer: "what hosts, runtimes, models, and deployments
does Switchyard manage?"

Introduce explicit durable entities:

```text
hosts        = machines/environments Switchyard can target
runtimes     = backend engines and default launch behavior
models       = logical model sources and model-family defaults
deployments  = concrete "run this model with this runtime on this host" records
```

This keeps Switchyard focused on model runtime management rather than becoming
a general container manager. Docker details remain available, but the primary
configuration language is about hosts, accelerators, model stores, runtimes, and
deployment tuning.

### Process Bootstrap Configuration

Use `.env` for process-local startup settings:

```bash
SWITCHYARD_CONFIG_PATH=config.yaml
SWITCHYARD_LOG_LEVEL=info
SWITCHYARD_API_HOST=0.0.0.0
SWITCHYARD_API_PORT=8000
SWITCHYARD_ACTIVE_HOST=trainbox
SWITCHYARD_DOCKER_HOST=tcp://127.0.0.1:2375
```

`SWITCHYARD_DOCKER_HOST` can override or supply the Docker API endpoint for the
active local process. The canonical host definition still lives in `config.yaml`.

### Recommended Config Layout

Use a single durable config file for SEP-002:

```text
switchyard-api/config.yaml
```

The file has four top-level sections:

```yaml
hosts: {}
runtimes: {}
models: {}
deployments: {}
```

Do not split into `hosts.yaml`, `runtimes.yaml`, `models.yaml`, or
`deployments.yaml` for SEP-002. File splitting can be revisited in a later SEP
when config size or write behavior creates real pressure.

### Recommended Entity Model

```yaml
hosts:
  trainbox:
    backend_host: trainbox
    docker_host: tcp://127.0.0.1:2375
    docker_network: model-runtime
    port_range: [18000, 18100]

    accelerators:
      - id: "0"
        type: cuda
        vram_gb: 24
      - id: "1"
        type: cuda
        vram_gb: 24

    stores:
      models:
        host_path: /data/LLM/oobabooga/models
        container_path: /models
        mode: ro
      hf_cache:
        host_path: /data/LLM/huggingface
        container_path: /hf-cache
        mode: rw

    container_defaults:
      environment:
        HF_HOME: /hf-cache
        PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True
      options:
        ipc: host
        ulimits:
          memlock: { soft: -1, hard: -1 }

  laptop-5070ti:
    backend_host: laptop-hostname
    docker_network: model-runtime
    port_range: [19000, 19100]
    accelerators:
      - id: "0"
        type: cuda
        vram_gb: 12
    stores:
      models:
        host_path: D:/LLM/models
        container_path: /models
        mode: ro
```

`hosts` own the machine-specific facts: Docker connectivity, backend
reachability, port ranges, model/cache stores, accelerator inventory, and
container defaults. This prevents model definitions from owning absolute host
paths.

`runtimes` should remain first-class. Do not remove them. They are the home for
backend engine defaults and image choices:

```yaml
runtimes:
  vllm:
    backend: vllm
    image: vllm/vllm-openai:latest
    defaults:
      dtype: auto
      enable_prefix_caching: true
    container_defaults:
      internal_port: 8000

  vllm-cpu:
    backend: vllm
    image: vllm/vllm-openai-cpu:latest-x86_64
    defaults:
      device: cpu
      dtype: bfloat16

  sglang:
    backend: sglang
    image: lmsysorg/sglang:latest
```

`models` should describe logical model sources and portable model-family
defaults:

```yaml
models:
  qwen3-27b-fp8:
    source:
      store: models
      path: Qwen3.6-27B-FP8
    defaults:
      served_model_name: qwen3-27b
      reasoning_parser: qwen3
      tool_call_parser: qwen3_coder
      capabilities:
        text: true
        reasoning: true
        tools: true
        vision: false
```

The model points to a named `store`, not an absolute host path. The selected
host resolves that store to a real host path and container mount.

`deployments` are the concrete unit that SEP-003 should use for lifecycle
operations. SEP-002 should load and validate deployment definitions and resolve
them into complete launch configuration objects.

```yaml
deployments:
  qwen3-27b-vllm-trainbox:
    model: qwen3-27b-fp8
    runtime: vllm
    host: trainbox
    runtime_overrides:
      tensor_parallel_size: 2
      gpu_memory_utilization: 0.97
      max_model_len: 100000
      kv_cache_dtype: fp8_e4m3
      max_num_seqs: 4
      disable_custom_all_reduce: true
      speculative_config:
        method: qwen3_next_mtp
        num_speculative_tokens: 2
    placement:
      accelerator_ids: ["0", "1"]

  qwen3-27b-sglang-trainbox:
    model: qwen3-27b-fp8
    runtime: sglang
    host: trainbox
    placement:
      accelerator_ids: ["0", "1"]

  qwen3-27b-vllm-laptop:
    model: qwen3-27b-fp8
    runtime: vllm
    host: laptop-5070ti
    runtime_overrides:
      tensor_parallel_size: 1
      gpu_memory_utilization: 0.88
      max_model_len: 32768
    placement:
      accelerator_ids: ["0"]
```

### Deployment Naming

Use:

```text
{model}-{runtime}-{host}
```

Examples:

```text
qwen3-27b-vllm-trainbox
qwen3-27b-sglang-trainbox
qwen3-27b-vllm-laptop
```

The model should lead because it is the primary object users think about. The
runtime comes next because it is the experiment axis. The host comes last
because it is the placement axis.

### Override Model

Avoid one generic `additional_args` bucket for everything. Use scoped overrides
so the UI and API can remain intuitive:

```yaml
deployments:
  qwen3-27b-vllm-trainbox-gpu1:
    model: qwen3-27b-fp8
    runtime: vllm
    host: trainbox

    placement:
      accelerator_ids: ["1"]

    storage_overrides:
      path: Qwen3.6-27B-FP8-alt

    runtime_overrides:
      tensor_parallel_size: 1
      max_model_len: 65536
      language_model_only: true

    container_overrides:
      environment:
        CUDA_VISIBLE_DEVICES: "1"

    extra_args:
      some-new-vllm-flag: value
```

Typed fields should cover high-value concepts the system understands.
`extra_args` remains an escape hatch for runtime-specific flags that Switchyard
does not model yet.

For GPU selection, prefer:

```yaml
placement:
  accelerator_ids: ["1"]
```

Switchyard should translate this to Docker/NVIDIA behavior. Users should not
need to think first in terms of `device_requests`, though advanced container
options can remain available.

Do not introduce semantic serving abstractions in SEP-002. Behavior such as
reasoning parser selection, tool-call parser selection, or multimodal disabling
should remain direct typed runtime fields where supported by the backend schema,
or `extra_args` where Switchyard does not yet model the backend flag.

### SEP-001 Configuration Is Supplanted

The SEP-001 config shape is supplanted by the SEP-002 schema. Do not preserve
backwards-compatible loading of the current `global` / `runtime_defaults` /
`models.<name>.runtime` shape.

Implementation work must refactor relevant tests, config models, loader logic,
examples, and docs to the new single-file entity model:

```text
switchyard-api/config.yaml
```

### Preserve SEP-001 vLLM Field Validation

The SEP-001 vLLM field validation remains valuable. Keep high-value command
parameters as typed Pydantic fields and keep unknown/new runtime flags in
`extra_args`.

The resolved runtime config should be assembled from layered inputs:

```text
runtime defaults
-> host runtime defaults
-> model runtime defaults
-> deployment runtime overrides
-> extra_args
```

The important invariant is that Switchyard-internal fields must not be
serialized blindly to CLI arguments. Fields such as `device`, `placement`,
`accelerator_ids`, and `stores` are Switchyard concepts; they must be consumed
by Switchyard and adapters, not emitted as `--device`, `--placement`, or
similar runtime flags.

### Recommended API Direction

The API object-management surface is substantial enough to treat as its own
future epic. It should expose CRUD for hosts, runtimes, models, and deployments,
plus lifecycle actions for deployments. See `epic-api-object-management.md`.

The SEP-001 model lifecycle API is supplanted by deployment lifecycle endpoints
in SEP-003. SEP-002 should not change lifecycle routes. It should produce the
resolved deployment configuration that SEP-003 will use when it migrates tests
and route code to deployment-based lifecycle operations.

### Open Questions

- When should config be split from one `config.yaml` into entity-specific files?
- Should config write APIs mutate YAML files directly, write to SQLite, or keep
  an in-memory desired state with explicit export?
- Should deployment IDs be user-authored, generated, or both?
- Should OpenAI-compatible `model` route by deployment ID, served model name, or
  an explicit alias registry?
- How much host hardware discovery belongs in SEP-002 versus a later host
  diagnostics effort?

### Recommended Web UI Direction

The web UI should be deployment-tuning oriented, not container-management
oriented. It should present hosts, accelerators, model stores, runtimes,
capabilities, memory/context settings, and generated launch previews. See
`epic-webui.md`.

### Sequencing

Track the broader work as separate SEP candidates in GitHub:

1. [SEP-002 — Config Data Model Refactor](https://github.com/DavidCatalano/switchyard/issues/2)
   - Foundation work for the entity config model.
   - Owns `.env` process bootstrap and the top-level `hosts`, `runtimes`,
     `models`, and `deployments` sections in `switchyard-api/config.yaml`.
   - Supplants the SEP-001 config shape.

2. [SEP-003 — Deployment Resolution and Lifecycle](https://github.com/DavidCatalano/switchyard/issues/3)
   - Makes deployments the runtime lifecycle unit.
   - Owns deployment resolution, source/store resolution, placement translation,
     deployment state, and deployment load/unload/status endpoints.
   - Supplants SEP-001 model lifecycle routes.

3. [SEP-004 — vLLM Runtime Resolver Hardening](https://github.com/DavidCatalano/switchyard/issues/4)
   - Keeps vLLM robust under the new runtime/deployment model.
   - Owns typed vLLM fields, `extra_args`, CPU/GPU runtime handling, generated
     vLLM command previews, and CPU/GPU smoke tests.

4. [SEP-005 — Read APIs and Diagnostics](https://github.com/DavidCatalano/switchyard/issues/5)
   - Exposes read and diagnostic API surfaces over the new object model.
   - Owns list/detail endpoints, dry-run, validate, logs, and host resources.
   - See `epic-api-object-management.md`.

5. [SEP-006 — Config Write APIs](https://github.com/DavidCatalano/switchyard/issues/6)
   - Adds create/update/delete APIs for hosts, runtimes, models, and deployments.
   - Owns YAML write strategy, write-time validation, and config reload behavior.

6. [SEP-007 — Host Resource Discovery](https://github.com/DavidCatalano/switchyard/issues/7)
   - Discovers host facts instead of relying only on hand-entered config.
   - Owns Docker checks, GPU inventory, port availability, Docker network checks,
     model store checks, and Windows/WSL considerations.

7. [SEP-008 — Web UI MVP](https://github.com/DavidCatalano/switchyard/issues/8)
   - Builds the first deployment-oriented web UI.
   - Owns dashboard, host details, deployment details, lifecycle actions, logs,
     dry-run preview, and test chat.
   - See `epic-webui.md`.

### Updated Acceptance Criteria

- `.env` owns process-local Switchyard bootstrap settings.
- Durable config is a single `switchyard-api/config.yaml` with `hosts`,
  `runtimes`, `models`, and `deployments` top-level sections.
- Host-specific Docker, storage, accelerator, and port settings live under
  explicit `hosts`.
- Runtime engines remain first-class and preserve typed high-value parameter
  validation from SEP-001.
- Models describe portable model sources and model-family defaults without
  absolute host paths.
- Deployments bind `{model, runtime, host}` and own concrete tuning overrides.
- A deployment can be resolved to a complete launch configuration object.
- Lifecycle API migration is deferred to SEP-003.
- Advanced Docker options remain possible without making the primary UX feel
  like a general container manager.
