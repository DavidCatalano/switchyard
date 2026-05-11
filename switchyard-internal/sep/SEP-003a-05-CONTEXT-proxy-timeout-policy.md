# Context Document

**Title**: Proxy Timeout Policy for LLM Requests
**ID**: SEP-003a-05-CONTEXT-proxy-timeout-policy
**Date**: 2026-05-10
**Author**: Codex
**PRD**: `SEP-003a-01-PRD-health-aware-status.md`

---

## Summary

SEP-003a smoke testing exposed a proxy timeout bug that is adjacent to, but not
identical with, deployment health status.

Switchyard currently proxies OpenAI-compatible chat requests with a hardcoded
`httpx.Client(timeout=10.0)` in `switchyard-api/src/switchyard/app.py`. That is
too brittle for local LLM inference:

- non-streaming long completions can take more than 10 seconds before the full
  response is ready;
- streaming responses can pause for more than 10 seconds between chunks during
  prefill, scheduler stalls, backend load, or network/tunnel jitter;
- streaming timeouts after headers are sent surface as noisy ASGI tracebacks
  because the HTTP response has already started.

This should be remediated before SEP-003a closeout because otherwise
Switchyard can correctly report a deployment as `running` but still fail normal
chat completions through its proxy.

---

## External Research

### HTTPX Timeout Semantics

HTTPX distinguishes connect, read, write, and pool timeouts. Its default model
is network-inactivity oriented. A scalar timeout applies bluntly across timeout
phases, including read inactivity while iterating streaming responses.

Reference: `https://www.python-httpx.org/advanced/timeouts/`

Implication for Switchyard: `timeout=10.0` is not an LLM-aware policy. Use
explicit `httpx.Timeout(...)` objects with separate connect/write/read/pool
settings.

### OpenAI Python SDK Defaults

The OpenAI Python SDK defaults requests to a 10-minute timeout and supports
granular `httpx.Timeout(...)` overrides.

Reference: `https://github.com/openai/openai-python#timeouts`

Implication for Switchyard: a 10-second proxy timeout is much stricter than a
major OpenAI-compatible client default. Switchyard should not impose a much
shorter timeout than common OpenAI-compatible clients unless the user explicitly
configures it.

### LiteLLM Proxy Policy

LiteLLM separates full-call timeout from streaming timeout. It supports
`timeout` for the complete response and `stream_timeout` for streaming behavior.

Reference: `https://docs.litellm.com.cn/docs/proxy/timeout`

Implication for Switchyard: separate non-streaming and streaming timeout
controls are a mature pattern. Switchyard should not use one scalar timeout for
both modes.

### Envoy Streaming Guidance

Envoy documents that normal route request timeouts are not compatible with
streaming responses and recommends stream idle timeout style controls for
streaming APIs.

Reference: `https://www.envoyproxy.io/docs/envoy/latest/faq/configuration/timeouts.html`

Implication for Switchyard: streaming should be governed by idle/progress
semantics, not a short whole-request timeout.

### vLLM OpenAI-Compatible Serving

vLLM exposes OpenAI-compatible Chat Completions and supports long-running text
generation and streaming responses.

Reference: `https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html`

Implication for Switchyard: the proxy must assume local model responses can be
long-lived and bursty, especially for large models and high `max_tokens`.

---

## Current Implementation Notes

Relevant files:

- `switchyard-api/src/switchyard/app.py`
- `switchyard-api/src/switchyard/config/models.py`
- `switchyard-api/.env`
- `switchyard-api/.env.example`
- `switchyard-api/tests/test_app_settings.py`
- `switchyard-api/tests/test_proxy.py`
- `switchyard-api/tests/test_errors.py`

Current proxy functions:

- `_blocking_proxy()` uses `httpx.Client(timeout=10.0)`.
- `_streaming_proxy()` uses `httpx.Client(timeout=10.0)`.
- `_streaming_proxy()` catches timeout/connect errors only while setting up the
  upstream stream. It does not catch `httpx.ReadTimeout` while yielding chunks
  from `response.iter_bytes()`.

Observed smoke-test behavior:

- `stream: false`, `max_tokens: 500` can succeed.
- `stream: false`, `max_tokens: 2000` returns `{"detail": "request timeout"}`
  after about 10 seconds.
- `stream: true`, `max_tokens: 2000` emits chunks, then times out mid-stream
  from `response.iter_bytes()` with an ASGI traceback.

This means the model is alive and producing tokens. The Switchyard proxy is
too impatient.

---

## Design Direction

For this remediation, extend process-local `.env` settings rather than adding
object-level config under `hosts`, `runtimes`, or `deployments`.

Rationale:

- The bug is in the Switchyard process proxy behavior, not in vLLM launch
  arguments.
- `.env` already owns process-local bootstrap behavior such as API host/port,
  active host, and Docker host override.
- A future SEP can promote these settings to host/deployment-level config if
  there is a real need for object-level timeout policy.

Recommended fields on `AppSettings`:

```python
proxy_connect_timeout_seconds: float = 5.0
proxy_write_timeout_seconds: float = 30.0
proxy_pool_timeout_seconds: float = 30.0
proxy_non_stream_read_timeout_seconds: float = 600.0
proxy_stream_read_timeout_seconds: float | None = None
```

Environment variables:

```text
SWITCHYARD_PROXY_CONNECT_TIMEOUT_SECONDS=5
SWITCHYARD_PROXY_WRITE_TIMEOUT_SECONDS=30
SWITCHYARD_PROXY_POOL_TIMEOUT_SECONDS=30
SWITCHYARD_PROXY_NON_STREAM_READ_TIMEOUT_SECONDS=600
SWITCHYARD_PROXY_STREAM_READ_TIMEOUT_SECONDS=
```

Recommended semantics:

- `proxy_connect_timeout_seconds`: short failure window for backend connection
  establishment.
- `proxy_write_timeout_seconds`: time allowed to write the request body to the
  backend.
- `proxy_pool_timeout_seconds`: time allowed to acquire an HTTPX connection
  from the pool.
- `proxy_non_stream_read_timeout_seconds`: read timeout for non-streaming
  responses. This must be long enough for full generation.
- `proxy_stream_read_timeout_seconds`: per-read idle timeout for streaming
  chunks. `None` disables HTTPX read timeout for streaming responses.

Default streaming read timeout should be `None` for local inference unless the
implementation adds an application-level idle watchdog. If a finite default is
preferred, use at least `300.0`.

---

## Sequential Implementation Steps

### Step 1: Add Settings

Update `AppSettings` in `switchyard-api/src/switchyard/config/models.py`.

Add the five proxy timeout fields listed above. Use positive validation for
all non-`None` values. `proxy_stream_read_timeout_seconds` must allow `None`.

Do not add these to `RuntimeConfig`, `DeploymentConfig`, or
`ResolvedDeployment` in this remediation.

### Step 2: Update Environment Files

Update `switchyard-api/.env.example` with commented defaults and concise
descriptions.

Update `switchyard-api/.env` with active defaults unless the user has already
customized values. The expected local defaults are:

```text
SWITCHYARD_PROXY_CONNECT_TIMEOUT_SECONDS=5
SWITCHYARD_PROXY_WRITE_TIMEOUT_SECONDS=30
SWITCHYARD_PROXY_POOL_TIMEOUT_SECONDS=30
SWITCHYARD_PROXY_NON_STREAM_READ_TIMEOUT_SECONDS=600
SWITCHYARD_PROXY_STREAM_READ_TIMEOUT_SECONDS=
```

Empty `SWITCHYARD_PROXY_STREAM_READ_TIMEOUT_SECONDS` should parse as `None` if
possible. If pydantic-settings does not naturally handle empty string as
`None`, either:

- document that the variable should be omitted to use `None`, and leave it
  commented in `.env.example`; or
- add a field validator that converts `""` to `None`.

### Step 3: Update App Settings Tests

Update `switchyard-api/tests/test_app_settings.py`.

Required test changes:

- defaults include all proxy timeout fields;
- valid `.env` keys include all new `SWITCHYARD_PROXY_*` variables;
- `.env.example` key matching test passes;
- invalid zero/negative timeout values fail for all fields that are set;
- empty or omitted stream read timeout resolves to `None`.

### Step 4: Build Timeout Helpers in `app.py`

In `switchyard-api/src/switchyard/app.py`, add small helper functions near the
proxy helpers:

```python
def _proxy_non_stream_timeout(settings: AppSettings) -> httpx.Timeout:
    ...

def _proxy_stream_timeout(settings: AppSettings) -> httpx.Timeout:
    ...
```

Expected shape:

```python
httpx.Timeout(
    connect=settings.proxy_connect_timeout_seconds,
    write=settings.proxy_write_timeout_seconds,
    read=settings.proxy_non_stream_read_timeout_seconds,
    pool=settings.proxy_pool_timeout_seconds,
)
```

For streaming, use `settings.proxy_stream_read_timeout_seconds` for `read`.
Allow `read=None`.

### Step 5: Apply Helpers to Proxy Paths

Update `_blocking_proxy()`:

- instantiate `settings = AppSettings()`;
- create client with `_proxy_non_stream_timeout(settings)`;
- preserve existing 504 behavior for `httpx.TimeoutException`;
- preserve existing 503 behavior for `httpx.ConnectError`.

Update `_streaming_proxy()`:

- instantiate `settings = AppSettings()`;
- create client with `_proxy_stream_timeout(settings)`;
- preserve setup-time 504/503 behavior before response headers are sent.

Do not change OpenAI-compatible request/response payloads.

### Step 6: Handle Mid-Stream Exceptions

Update the `_generate()` function inside `_streaming_proxy()`.

Current behavior:

```python
yield from response.iter_bytes()
```

Problem: `httpx.ReadTimeout` can escape after the `StreamingResponse` has
already started, causing ASGI tracebacks.

Minimum acceptable behavior:

- catch `httpx.TimeoutException` inside `_generate()`;
- log a warning with `exc_info=True`;
- close the upstream response/client in `finally`;
- do not re-raise, because the response has already started.

Optional SSE error chunk:

If the backend response content type is SSE (`text/event-stream`), it is
reasonable to yield a final event before closing:

```text
data: {"error":{"message":"upstream stream timeout","type":"timeout"}}

```

Keep this optional and test-driven. A clean close without an ASGI traceback is
the minimum requirement.

### Step 7: Add Proxy Timeout Tests

Update `switchyard-api/tests/test_proxy.py` and/or
`switchyard-api/tests/test_errors.py`.

Required tests:

1. Non-streaming proxy uses configured long read timeout.
   - Patch `switchyard.app.httpx.Client`.
   - Assert the timeout object passed to the client has expected connect,
     write, read, and pool values.

2. Streaming proxy uses configured streaming read timeout.
   - Assert `read=None` or configured value is passed to HTTPX.

3. Streaming setup timeout still returns 504.
   - Existing tests likely cover this; update expectations only if needed.

4. Mid-stream timeout does not raise an ASGI exception.
   - Mock `response.iter_bytes()` to yield one chunk, then raise
     `httpx.ReadTimeout`.
   - Consume `StreamingResponse` through `TestClient`.
   - Assert no server exception is raised.
   - Assert the response contains the initial chunk and then ends cleanly, or
     contains the optional SSE error event if implemented.

5. Connect failure still returns 503 quickly.

### Step 8: Manual Smoke Test

After implementation, run Switchyard locally and test both non-streaming and
streaming chat completions through Switchyard.

Non-streaming:

```bash
curl -sS http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3.6-27b-fp8-vllm-trainbox",
    "messages": [
      {"role": "user", "content": "Think long and hard about five roleplay story ideas in the Star Wars universe."}
    ],
    "max_tokens": 2000,
    "temperature": 0,
    "stream": false
  }'
```

Streaming:

```bash
curl -N http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3.6-27b-fp8-vllm-trainbox",
    "messages": [
      {"role": "user", "content": "Think long and hard about five roleplay story ideas in the Star Wars universe."}
    ],
    "max_tokens": 2000,
    "temperature": 0,
    "stream": true
  }'
```

Expected result:

- non-streaming should not timeout at 10 seconds;
- streaming should not cut off after a 10-second gap;
- streaming should not emit an ASGI traceback if the upstream eventually times
  out or disconnects.

### Step 9: Quality Gates

Run from `switchyard-api/`:

```bash
uv run pytest
uv run ruff check src tests --fix
uv run mypy src/switchyard
```

---

## Acceptance Criteria

- `.env`, `.env.example`, and `AppSettings` expose process-level proxy timeout
  defaults.
- Non-streaming chat no longer uses a hardcoded 10-second timeout.
- Streaming chat no longer uses a hardcoded 10-second read timeout.
- Mid-stream timeout no longer produces an ASGI traceback.
- Connect failures still return 503.
- Setup/read timeouts before response start still return structured 504.
- Tests cover timeout construction and mid-stream timeout behavior.
- Full quality gates pass.

---

## Boundaries

In scope:

- process-level timeout settings through `.env`;
- Pydantic settings validation;
- proxy helper refactor in `app.py`;
- tests for non-streaming and streaming timeout behavior.

Out of scope:

- promoting timeout policy into `hosts`, `runtimes`, or `deployments`;
- changing vLLM launch arguments;
- changing deployment health semantics;
- adding logs endpoints;
- changing OpenAI-compatible request or response schemas.

