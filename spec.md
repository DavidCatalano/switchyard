# Model Runtime Manager – Spec

## 1. Purpose

Define a minimal, extensible control plane that:

- Manages lifecycle of heterogeneous LLM runtimes (vLLM, koboldcpp, exllamav2, SGLang)
- Provides a unified API surface (OpenAI-compatible baseline)
- Routes requests to appropriate backend containers

This system is not an inference engine. It is a control and routing layer.

---

## 2. Non-Goals (MVP)

- No autoscaling or load balancing
- No GPU scheduling
- No database (file-based config only)
- No UI
- No implicit model loading
- No config hot-reload (restart required for config changes)
- No batching or performance optimization layer

---

## 3. Core Concepts

### 3.1 Model

Logical identifier used by clients.

Example:

- "qwen-32b"
- "mistral-7b-gguf"

### 3.2 Backend

Runtime engine responsible for serving a model.

Examples:

- vLLM
- koboldcpp
- exllamav2
- SGLang

### 3.3 Deployment (Runtime Instance)

A running container serving a model via a backend.

Model != Deployment

---

## 4. Architecture

```
[Client]
   |
[Control API]
   |
[Router + Lifecycle Manager]
   |
---------------------------------
|       |        |              |
[vLLM] [kobold] [exllama]   [sglang]
```

---

## 5. API Specification (MVP)

### 5.1 Model Lifecycle

POST /models/load

- Input: { model: string }
- Behavior:
  - Starts container via backend adapter
  - Returns immediately (202 Accepted)
  - Status transitions to `loading`, then `running` or `error` on health check
  - Health check runs asynchronously; poll via `GET /models/{model}/status`

POST /models/unload

- Input: { model: string }
- Behavior:
  - Stops container
  - Removes container immediately after stop

GET /models

- Returns list of configured models with status, backend, port, and `started_at`

GET /models/{model}/status

- Returns: `running` | `stopped` | `loading` | `error`

---

### 5.2 Inference

POST /v1/chat/completions

- OpenAI-compatible request
- Requires: model
- Behavior:
  - Routes to backend endpoint
  - Returns response (normalized only where necessary)

**Streaming:** Control plane acts as transparent proxy for SSE streams. Forwards `Transfer-Encoding: chunked` with no buffering. Timeout applies to initial connection only, not the stream lifetime.

---

### 5.3 Backend Passthrough (Scoped)

POST /v1/backends/{model}/{path...}

- Direct passthrough to backend-specific endpoint
- Used for:
  - responses API
  - embeddings
  - runtime-specific features

---

## 6. Configuration (YAML)

### 6.1 Structure

```
global:
  docker_network: model-runtime
  base_port: 8000
  log_level: info

runtime_defaults:
  vllm:
    gpu_memory_utilization: 0.92
    tensor_parallel_size: 2
    dtype: auto
    kv_cache_dtype: auto
    enable_prefix_caching: true
  koboldcpp:
    n_gpu_layers: -1
    ctx_size: 4096

models:
  qwen-32b:
    backend: vllm
    image: vllm/vllm-openai:latest

    control:
      auto_start: false

    resources:
      memory: 32g

    runtime:
      repo: Qwen/Qwen2-32B-Instruct
      # inherits gpu_memory_utilization, tensor_parallel_size, dtype, etc. from runtime_defaults.vllm

  llama-70b:
    backend: vllm
    image: vllm/vllm-openai:latest

    control:
      auto_start: false

    runtime:
      repo: meta-llama/Llama-3.1-70B-Instruct
      tensor_parallel_size: 4    # override runtime_defaults.vllm

  mistral-7b-gguf:
    backend: koboldcpp
    image: something/mistral-kobold:latest

    control:
      auto_start: false

    runtime:
      model_path: /models/mistral.gguf
      # inherits n_gpu_layers, ctx_size from runtime_defaults.koboldcpp
```

Config cascades from top to bottom: `global` → `runtime_defaults.{backend}` → `models.{name}.runtime`. Per-model values override defaults; defaults override global. An `extra_args` mapping on either the defaults or per-model level passes arbitrary backend-specific flags through verbatim.

The `image` field per model acts as a **version lock** — pinning `vllm/vllm-openai:v0.9.0` freezes the CLI flag contract for that model. The YAML config is the version lock file.

---

## 7. Backend Abstraction

Each backend must implement a common adapter interface.

### 7.1 Interface

```
class BackendAdapter:
    def start(self, model_config) -> DeploymentInfo
    def stop(self, deployment_id)
    def health(self, deployment_id) -> str
    def endpoint(self, deployment_id) -> str
```

### 7.2 Responsibilities

Adapters handle:

- Docker run/stop
- Environment variables
- Port assignment
- Health checks

Control plane must NOT:

- know CLI flags
- know container internals

---

## 8. Backend Contract

Each runtime container must expose:

- POST /v1/chat/completions (required)
- GET /health (required)

Optional:

- /v1/responses
- /v1/embeddings

Adapters are responsible for ensuring compatibility.

---

## 9. Routing Logic (MVP)

For /v1/chat/completions:

1. Extract model
2. Lookup config
3. Verify deployment running
4. Forward request to backend endpoint
5. Return response

If not running:

- return error (no implicit start in MVP)

---

## 10. Deployment Management

### 10.1 Container Naming

Format:

model-backend-instance

Example:

- qwen-32b-vllm-1

### 10.2 Port Strategy

- Base port: `8000` (configurable via `global.base_port` or environment variable)
- Allocation: sequential from base port, skip ports already in use
- Tracking: in-memory map of model → port
- Release: port returns to pool on container stop

### 10.3 Container Cleanup

- Containers removed immediately on explicit unload
- Crashed containers left in place for log inspection
- Cleanup of crashed containers occurs on next startup during orphan detection

---

## 11. State Management

MVP:

- in-memory only

Tracks:

- running deployments
- model → deployment mapping
- ports

No persistence required. A control plane restart loses in-memory state; orphan detection (below) recovers running containers.

## 12. Startup and Bootstrap

On startup the control plane:

1. Loads and validates YAML config
2. Verifies Docker socket accessibility
3. **Orphan detection** — scans for containers matching naming convention (`{model}-{backend}-{instance}`) that match configured models but are missing from in-memory state. Adopts them as active deployments (restores model → port mapping, marks as `running`).
4. Removes any crashed orphans left from previous sessions
5. Starts models with `auto_start: true`
6. Begins listening on API port

---

## 13. Error Handling

| Condition | Status |
|-----------|--------|
| Model not found in config | 404 |
| Model configured but not running | 400 |
| Backend unhealthy | 503 |
| Docker daemon unavailable | 503 |
| Container failed to start | 500 |
| Request timeout (initial connection) | 504 |
| Config validation error (startup) | fatal, service exits |

---

## 14. Design Principles

- API-first
- Backends are pluggable
- Control plane owns orchestration
- Runtime containers are dumb
- Normalize only where necessary
- Adapters wrap containers (no sidecars required)

---



