# Switchyard

**A control plane for managing local LLM inference runtimes.**

*This project is made with [pi.dev](https://pi.dev) and [Qwen 3.6 27B](https://huggingface.co/Qwen/Qwen3.6-27B-FP8)*

Switchyard is not an inference engine. It is an orchestration and routing layer that manages the lifecycle of containerized LLM backends (vLLM, koboldcpp, exllamav2, SGLang) behind a unified API surface. Clients interact with Switchyard; Switchyard manages the containers.

---

## What It Does

- **Lifecycle management** — start, stop, health-check, and discover backend containers via Docker SDK
- **Request routing** — proxy OpenAI-compatible `chat/completions` to the correct running backend, including transparent SSE streaming
- **Backend abstraction** — each runtime engine is wrapped by a pluggable adapter; the control plane never knows CLI flags or container internals
- **Orphan recovery** — on startup, detects and adopts containers still running from a previous session; removes crashed ones

## What It Does Not Do (MVP)

Autoscaling, GPU scheduling, persistent state, UI, config hot-reload, implicit model loading, batching.

---

## Architecture at a Glance

```
Client → Control API (FastAPI)
         ├─ Router          → routes /v1/* to the right backend container
         ├─ Lifecycle Mgr   → load/unload containers, health polls, orphan detection
         └─ Adapter Registry → vLLM, koboldcpp, exllamav2, SGLang (pluggable)
```

**Key idea: Model ≠ Deployment.** A model is a logical identifier (`qwen-32b`). A deployment is a running container serving that model through a specific backend. One model maps to one active deployment.

---

## How It Works

1. **Config** — YAML file defines which models are available, which backend each uses, and runtime parameters (image, resource limits, backend-specific args). Env vars overlay global settings (port, log level).
2. **Startup** — loads config, verifies Docker socket, scans for orphan containers (adopts running, removes crashed), auto-starts configured models, begins listening.
3. **Load a model** — `POST /models/load` returns 202 immediately. Backend adapter starts a container on a sequentially-allocated port (base 8000). Background health polling transitions status from `loading` → `running` or `error`.
4. **Inference** — `POST /v1/chat/completions` extracts the model name, verifies the deployment is running, and proxies the request. Streaming responses are forwarded transparently (SSE, no buffering).
5. **Unload** — `POST /models/unload` stops and removes the container, releases its port.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI + Uvicorn |
| Config | Pydantic + PyYAML + pydantic-settings |
| Container lifecycle | Docker SDK for Python |
| HTTP proxy | httpx (async) |
| Logging | structlog (JSON prod, console dev) |
| Observability hooks | opentelemetry-api (SDK-agnostic) |
| Testing | pytest + pytest-asyncio |
| Quality | ruff + mypy |
| Package manager | uv |

---

## Project Structure

```
switchyard/
├── switchyard-api/              # Python package (src/switchyard layout)
│   ├── pyproject.toml
│   ├── src/switchyard/          # Application code
│   └── tests/
├── switchyard-internal/         # Project governance & planning
│   ├── process/                 # DEV.md, PYTHON.md, templates
│   ├── sep/                     # SEP artifacts (PRD, PLAN, CONTEXT, etc.)
│   └── docs/v0/                 # Spec drafts
├── spec.md                      # Model Runtime Manager specification
└── README.md                    # This file
```

---

## Development

- **Workflow**: Follow `switchyard-internal/process/DEV.md` for phase sequencing, branching, commits, and PR conventions.
- **Python standards**: See `switchyard-internal/process/PYTHON.md` (Python 3.12, native type syntax, TDD).
- **Current effort**: SEP-001 — MVP control plane. See `switchyard-internal/sep/SEP-001-02-PLAN-mvp-control-plane.md` for the active task breakdown.

### Quality Gates (run from `switchyard-api/`)

```bash
uv run pytest                     # all tests
uv run ruff check src --fix       # lint & format
uv run mypy src/switchyard        # type check
```

---

## Backends

| Backend | Status |
|---------|--------|
| vLLM | First adapter (SEP-001) |
| koboldcpp | Planned |
| exllamav2 | Planned |
| SGLang | Planned |
