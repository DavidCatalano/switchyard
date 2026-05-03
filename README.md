# Switchyard

**A local control plane for launching, tuning, and routing requests to
containerized LLM runtimes.**

Switchyard helps manage local inference setups where model files, runtime
flags, GPU placement, cache mounts, Docker networking, ports, and backend health
otherwise end up spread across shell scripts and compose files.

The project is currently focused on vLLM first, with the architecture shaped to
support additional runtimes such as koboldcpp, exllamav2, and SGLang.

---

## Core Concepts

Switchyard is organized around four durable concepts:

- **Hosts** — machines or environments that can run inference containers.
- **Runtimes** — backend engines such as vLLM, SGLang, koboldcpp, or exllamav2.
- **Models** — logical model sources and model-family defaults.
- **Deployments** — concrete combinations of model, runtime, host, placement,
  and tuning overrides.

The important distinction is:

```text
model + runtime + host + placement + overrides = deployment
```

A model is portable. A deployment is the specific way that model is run on a
specific host with a specific runtime.

---

## What It Does

- Starts, stops, health-checks, and discovers backend containers through Docker.
- Routes OpenAI-compatible requests to the correct running backend.
- Wraps runtime engines behind pluggable backend adapters.
- Keeps high-value runtime settings typed while preserving escape hatches for
  backend-specific arguments.
- Separates process-local bootstrap settings from durable managed config.

---

## Architecture

```text
Client
  |
  v
Switchyard API
  |
  +-- Router             -> proxies /v1/* requests to backend containers
  +-- Lifecycle Manager  -> loads/unloads deployments and tracks status
  +-- Config Resolver    -> resolves hosts/runtimes/models/deployments
  +-- Adapter Registry   -> vLLM first, other runtimes later
```

Switchyard is a control and routing layer. The runtime containers still perform
the actual inference.

---

## Development

Use the Makefile as the entry point for local commands:

```bash
make help
```

Project workflow and engineering standards live in:

- `AGENTS.md`
- `switchyard-internal/process/PYTHON.md`

Python quality gates run from `switchyard-api/`:

```bash
uv run pytest
uv run ruff check src tests --fix
uv run mypy src/switchyard
```

---

## Project Structure

```text
switchyard/
├── AGENTS.md
├── Makefile
├── deploy/
│   ├── lxd-profiles/
│   └── systemd/
├── switchyard-api/
│   ├── config.yaml
│   ├── pyproject.toml
│   ├── src/switchyard/
│   └── tests/
├── switchyard-internal/
│   ├── planning/
│   ├── process/
│   └── sep/
└── README.md
```

---

## Backends

| Backend | Status |
|---------|--------|
| vLLM | First adapter |
| SGLang | Planned |
| koboldcpp | Planned |
| llama.cpp | Planned |

---

## Local Coding Model + Agent

Switchyard is an experiment in using a local model for software
development. The project is made with [pi.dev](https://pi.dev) and
[Qwen 3.6 27B](https://huggingface.co/Qwen/Qwen3.6-27B-FP8), with
architectural consultation from Codex.
