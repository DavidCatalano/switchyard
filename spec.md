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
  - Waits for /health or returns immediately

POST /models/unload

- Input: { model: string }
- Behavior:
  - Stops container

GET /models

- Returns list of configured models and status

GET /models/{model}/status

- Returns: running | stopped | loading

---

### 5.2 Inference

POST /v1/chat/completions

- OpenAI-compatible request
- Requires: model
- Behavior:
  - Routes to backend endpoint
  - Returns raw or lightly normalized response

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
models:
  qwen-32b:
    backend: vllm

    control:
      auto_start: false
      health_timeout: 120

    runtime:
      repo: Qwen/Qwen2-32B-Instruct
      tensor_parallel_size: 2

  mistral-7b-gguf:
    backend: koboldcpp

    control:
      auto_start: false

    runtime:
      model_path: /models/mistral.gguf
      context_size: 8192
```

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

MVP:

- static or simple incrementing port allocator

Future:

- dynamic pool with reuse

---

## 11. State Management

MVP:

- in-memory only

Tracks:

- running deployments
- model -> deployment mapping
- ports

No persistence required.

---

## 12. Error Handling

- Model not found → 404
- Model not running → 400
- Backend not healthy → 502

---

## 13. Future Extensions (Not in MVP)

- Lazy loading on request
- LRU model eviction
- GPU-aware scheduling
- Multi-instance per model
- Streaming normalization
- Metrics + observability
- Web UI

---

## 14. Design Principles

- API-first
- Backends are pluggable
- Control plane owns orchestration
- Runtime containers are dumb
- Normalize only where necessary

---

## 15. Open Questions

- Should streaming be normalized or passed through?
- How to handle token limits inconsistencies?
- Should adapters wrap non-OpenAI runtimes or require sidecars?
- How to expose model metadata (context size, etc.)?

