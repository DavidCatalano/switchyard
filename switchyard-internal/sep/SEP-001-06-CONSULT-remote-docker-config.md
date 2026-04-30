# Consult Document - Remote Docker Configuration

**Title**: Remote Docker and Backend Reachability Fixes
**ID**: SEP-001-06-CONSULT-remote-docker-config
**Date**: 2026-04-29
**Author**: Codex
**Related Plan**: `SEP-001-02-PLAN-mvp-control-plane.md`
**Related Context**: `SEP-001-04-CONTEXT-vllm-config.md`

---

## Purpose

Switchyard currently assumes the control plane, Docker daemon, and published
backend ports all live on the same host. That works for a local Docker daemon,
but it breaks when development happens on a local workstation while Docker runs
on a remote host such as Trainbox through a Docker CLI context.

The Docker CLI can resolve SSH-based Docker contexts, but the Python Docker SDK
does not automatically use the active CLI context. The SDK needs an explicit
socket, TCP endpoint, or process environment. Separately, Switchyard health
checks and proxies currently use `localhost:<port>`, which points at the Mac
when the API service runs locally, not the remote Docker host.

This consult recommends a small configuration layer that lets Switchyard work
with either local Docker or remote Docker using `.env` settings.

---

## Changes Already Made on `sep/001-codex`

Codex committed smoke-test hardening on branch `sep/001-codex`:

`b145e3e fix(api): tolerate slow vllm startup` - You are NOT to access these files without approval from the User.

The branch contains these changes:

- `switchyard-api/src/switchyard/core/lifecycle.py`: add bounded startup health polling. Transient health failures keep the deployment in `loading` until a startup timeout expires, instead of moving to `error` on the first failed poll.
- `switchyard-api/src/switchyard/adapters/vllm.py`: make vLLM bind explicitly to `0.0.0.0:8000` inside the container and map that internal port to Switchyard's allocated host port.
- `switchyard-api/tests/test_lifecycle.py`: add delayed-readiness coverage so a backend can report non-running before eventually reaching `running`.
- `switchyard-api/tests/test_vllm_adapter.py`: add coverage for the vLLM internal bind and Docker port mapping.

Validation run on that branch:

```bash
cd switchyard-api
uv run pytest -q tests/test_lifecycle.py tests/test_vllm_adapter.py
```

Result: 52 passed.

These changes should be included in the final implementation because they are
not remote-Docker-specific. They fix real startup behavior for vLLM and other
slow-loading runtimes.

---

## Recommended Configuration Model

Add explicit host configuration for two separate concerns:

1. Docker control endpoint: where the Python Docker SDK sends Docker API calls.
2. Backend connect endpoint: where Switchyard sends HTTP health checks and
   proxied inference requests to published container ports.

Recommended `.env` fields:

```bash
# Optional. If omitted, docker.from_env() keeps local Docker behavior.
SWITCHYARD_DOCKER_HOST=tcp://127.0.0.1:2375

# Optional. Defaults to localhost for local Docker.
# For remote Docker, set this to the remote host DNS name/IP or a local tunnel host.
SWITCHYARD_BACKEND_HOST=127.0.0.1

# Optional. Defaults to http.
SWITCHYARD_BACKEND_SCHEME=http

# Optional override for the Docker network used by containers.
SWITCHYARD_DOCKER_NETWORK=model-runtime

# Optional startup behavior.
SWITCHYARD_HEALTH_INTERVAL_SECONDS=2
SWITCHYARD_HEALTH_TIMEOUT_SECONDS=300
```

For a Mac using an SSH tunnel to a remote Docker socket:

```bash
ssh -L 2375:/var/run/docker.sock user@trainbox
SWITCHYARD_DOCKER_HOST=tcp://127.0.0.1:2375
SWITCHYARD_BACKEND_HOST=trainbox
```

If remote container ports are not directly reachable from the Mac, tunnel the
allocated backend port and set `SWITCHYARD_BACKEND_HOST=127.0.0.1`.

For local Docker, `.env` can omit these fields and retain current defaults.

---

## Recommended Code Changes

### 1. Load `.env` Configuration

Update `switchyard-api/src/switchyard/config/loader.py` so `AppSettings`
loads `.env` from `switchyard-api/.env` when present. Also provide
`switchyard-api/.env.example` with safe defaults and comments.

Do not commit a real `.env`.

### 2. Extend Runtime Settings

Add fields to `AppSettings`:

- `docker_host: str | None`
- `backend_host: str = "localhost"`
- `backend_scheme: str = "http"`
- `docker_network: str | None`
- `health_interval_seconds: float = 2.0`
- `health_timeout_seconds: float = 300.0`

Apply `docker_network` as an env override to `config.global_config.docker_network`.
Consider adding `backend_host` and `backend_scheme` to `GlobalConfig` so the
resolved application config has a single source of truth.

### 3. Centralize Docker Client Creation

Do not call `docker.from_env()` directly from adapters. Add a small Docker
client factory, for example under `switchyard-api/src/switchyard/core/docker.py`.

Responsibilities:

- Use `settings.docker_host` as `base_url` when set.
- Otherwise fall back to `docker.from_env()`.
- Preserve support for native `DOCKER_HOST` when the user chooses to set it.
- Provide one path used by adapters and bootstrap/orphan detection.

This makes local and remote Docker behavior explicit and testable.

### 4. Use Configured Backend Endpoint Host

Stop constructing backend URLs with hardcoded `localhost`.

Current assumptions to replace:

- `VLLMAdapter.health()` uses `http://localhost:{deployment.port}/health`.
- `VLLMAdapter.endpoint()` returns `http://localhost:{deployment.port}`.
- `switchyard-api/src/switchyard/app.py` builds proxy URLs with
  `http://localhost:{deployment.port}`.

Recommended direction:

- Add endpoint data to `DeploymentInfo.metadata`, such as `backend_host` and
  `backend_scheme`, or pass an endpoint resolver into adapters and route code.
- Ensure health checks and proxy calls use:
  `{backend_scheme}://{backend_host}:{deployment.port}`.
- Keep the container bind host as `0.0.0.0`; this is independent from the host
  used by the control plane to reach the published port.

### 5. Respect Configured Docker Network

`VLLMAdapter.start()` currently hardcodes `model-runtime`. Replace that with
`config.global_config.docker_network` or a resolved adapter setting. This matters
for remote hosts that already have a different model-serving network.

### 6. Include the Codex Startup Fixes

Recreate fixes mentioned above that exist in `sep/001-codex` changes:

- Bounded startup health polling.
- vLLM internal bind to `0.0.0.0:8000`.
- Tests for delayed readiness and internal port binding.

**REMEMBER: Do not access this branch's files without User permission**

---

## Suggested Test Coverage

Add unit tests for:

- `.env` fields override YAML/global defaults.
- Docker client factory uses explicit `SWITCHYARD_DOCKER_HOST`.
- Docker client factory falls back to `docker.from_env()` when unset.
- vLLM adapter uses configured Docker network instead of a hardcoded network.
- Health check endpoint uses configured `backend_host` and `backend_scheme`.
- Chat completions proxy uses configured `backend_host` and `backend_scheme`.
- Local default remains `http://localhost:<port>`.

Add one manual smoke-test path:

1. Start Switchyard locally with `.env` pointing Docker control to a remote host.
2. Load a tiny vLLM CPU model.
3. Poll `GET /models/{model}/status` until `running`.
4. Send `POST /v1/chat/completions`.
5. Unload the model.

---

## Open Design Questions

- Should `backend_host` be global only, or overridable per model? Recommendation:
  global for MVP. Per-model override is useful later for multi-host scheduling.
- Should the control plane run on the remote Docker host in production?
  Recommendation: Yes. Required to support both, but document that remote development requires
  the Docker API endpoint and published backend ports to be reachable from the
  control plane process.

---

## Acceptance Criteria

- Local Docker works with no `.env` beyond `SWITCHYARD_CONFIG_PATH` using pydantic-settigns defaults.
- Remote Docker works when `.env` supplies a Docker API endpoint and backend
  connect host.
- No code path assumes Docker-published backend ports are reachable on local
  `localhost` unless that is the configured default.
- vLLM startup remains in `loading` while the model is still initializing and
  only transitions to `error` after the configured startup timeout.
- Quality gates pass from `switchyard-api/`:

```bash
uv run pytest
uv run ruff check src tests --fix
uv run mypy src/switchyard
```
