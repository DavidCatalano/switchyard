# Code Review: SEP-003a-05 Proxy Timeout Work

## Summary

The fix is fundamentally correct and resolved the streaming-stop bug. End-to-end smoke test confirms clean SSE termination with `data: [DONE]`. All 332 tests pass. There are, however, three substantive issues to fix before closeout, one scope question, and one pre-existing test-hygiene issue worth noting.

---

## What's correct (no action needed)

- **`AppSettings` additions** (`config/models.py:380-395`): five fields with `gt=0` validation; `_empty_stream_timeout_is_none` `mode="before"` validator correctly maps `""` → `None`. Matches the design doc.
- **Timeout helpers** (`app.py:493-510`): `_proxy_non_stream_timeout` and `_proxy_stream_timeout` build `httpx.Timeout(connect=, read=, write=, pool=)` from `AppSettings`. Correct shape.
- **`_streaming_proxy` migrated to async** (`app.py:535-588`): previously a sync function called from an async route — the `await _streaming_proxy(...)` change at `app.py:417` and the `httpx.Client`→`httpx.AsyncClient` swap are correct and silently fix a second latent bug.
- **Setup-time 504/503** behavior preserved (`app.py:546-559`).
- **`.env`** has the new keys with empty stream-read timeout. **`.env.example`** has commented documented defaults.
- **Test changes** in `tests/test_app_settings.py`, `tests/test_proxy.py`, `tests/conftest.py` are well-scoped and pass.
- **Remaining `httpx.Client` mocks** in `tests/test_errors.py:129,163`, `tests/test_api.py:994,1224`, and `tests/test_proxy.py:394,458` all sit on **non-streaming** code paths (`/api/proxy/.../v1/models`, `/v1/embeddings`, non-streaming chat). They're correct *today* — see Remediation 1 below for the caveat.

---

## Issue 1 — CRITICAL: `_blocking_proxy` blocks the event loop for up to 10 minutes

`chat_completions` is `async def`. Inside it, the non-streaming branch calls `_blocking_proxy` synchronously (`app.py:420`), which does `with httpx.Client(...): client.post(...)` — a **sync** call inside an async route.

Pre-fix this blocked the event loop for up to 10s. Now `proxy_non_stream_read_timeout_seconds=600` means a single long buffered completion can block the entire API process for **10 minutes**, queueing every other request (`/health`, `/api/deployments`, lifecycle, etc.). For local single-user use it's tolerable; for any concurrent use it's a serious regression in tail behavior and worse than what existed before the SEP-003a-05 work.

**Fix**: make `_blocking_proxy` async with `httpx.AsyncClient`, mirroring `_streaming_proxy`. Both code paths then look symmetric.

```python
async def _blocking_proxy(url: str, body: dict[str, Any]) -> JSONResponse:
    settings = AppSettings()
    try:
        async with httpx.AsyncClient(
            timeout=_proxy_non_stream_timeout(settings),
        ) as client:
            response = await client.post(url, json=body)
        return JSONResponse(
            status_code=response.status_code,
            content=response.json() if response.status_code != 204 else {},
        )
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={"detail": "request timeout"})
    except httpx.ConnectError:
        return JSONResponse(status_code=503, content={"detail": "backend unavailable"})
```

Then update the two call sites:
- `app.py:357`: `return await _blocking_proxy(...)`
- `app.py:420`: `return await _blocking_proxy(...)`

---

## Issue 2 — MEDIUM: mid-stream `httpx.TimeoutException` is unhandled

`_generate()` (`app.py:568-576`) has `try/finally` but no `except`. Today this never fires because the default `proxy_stream_read_timeout_seconds` is `None`. But the moment any user sets `SWITCHYARD_PROXY_STREAM_READ_TIMEOUT_SECONDS=<n>` and that read times out *after* response headers are sent, the exception escapes into the ASGI layer — exactly the noisy traceback Step 6 of the context doc explicitly calls out.

**Fix**: catch inside `_generate()`, log, swallow (headers are already sent so re-raising is pointless), and let `finally` close upstream.

```python
async def _generate() -> Any:
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    except httpx.TimeoutException:
        logger.warning("proxy: upstream stream timeout for %s", url, exc_info=True)
    finally:
        assert stream_ctx is not None
        assert client is not None
        await stream_ctx.__aexit__(None, None, None)
        await client.aclose()
```

This closes the latent footgun without behavior change under the default config.

---

## Issue 3 — MEDIUM: missing mid-stream timeout test

Step 7 #4 of the context doc requires a test that proves the mid-stream timeout doesn't surface an ASGI exception. It's not yet in `tests/test_proxy.py`. Pair it with Remediation 2.

**Fix**: add to `TestChatCompletionsProxy` in `tests/test_proxy.py`:

```python
def test_streaming_mid_stream_timeout_ends_cleanly(self) -> None:
    """Mid-stream httpx.ReadTimeout closes the response without ASGI traceback."""
    from switchyard.core.adapter import DeploymentInfo

    async def _chunks_then_timeout():
        yield b'data: {"choices": []}\n\n'
        raise httpx.ReadTimeout("stalled")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.aiter_bytes.return_value = _chunks_then_timeout()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    mock_client = MagicMock()
    mock_client.stream.return_value = mock_response
    mock_client.aclose = AsyncMock()

    app = create_app()
    info = DeploymentInfo(
        model_name="test-deployment",
        backend="vllm",
        port=9001,
        status="running",
        container_id="abc123",
        metadata={"backend_host": "127.0.0.1", "backend_scheme": "http"},
    )
    app.state.manager.state.add(info)

    with patch("switchyard.app.httpx.AsyncClient", return_value=mock_client):
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "test-deployment",
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

    assert resp.status_code == 200
    assert b"choices" in resp.content
    mock_response.__aexit__.assert_awaited_once_with(None, None, None)
    mock_client.aclose.assert_awaited_once()
```

If `TestClient` raises rather than swallowing the mid-stream exception (it can do either depending on Starlette version), the test will fail — which is exactly the regression signal we want.

---

## Issue 4 — SCOPE: unrelated `config.yaml` edits riding along

`git diff switchyard-api/config.yaml` shows:
- `backend_host: trainbox` → `10.0.0.10`
- `max_num_seqs: 4` → `2`
- `gpu_memory_utilization: 0.97` → `0.30`

These look like debugging tweaks for your specific machine — they have nothing to do with proxy timeouts.

**Decision needed**: either revert these before the SEP-003a-05 commit, or split them into their own commit with a clear message. They should not silently land under a proxy-timeout title.

```bash
# To revert:
git checkout -- switchyard-api/config.yaml
```

---

## Issue 5 — LOW (PRE-EXISTING, not introduced here): leaky `AppSettings` test patch

`conftest.py:32` patches `switchyard.config.models.AppSettings`, but `app.py` does `from switchyard.config.models import AppSettings`. The `switchyard.app.AppSettings` binding is a separate reference and is **not** affected by the patch. Tests still pass because the real `.env` defaults match the mock defaults. With the new proxy code, `AppSettings()` is now called once per request, so this fragile pattern is exercised more often.

**Recommendation**: not blocking for SEP-003a-05 closeout, but track for a future cleanup. The minimal fix is to add a second `patch("switchyard.app.AppSettings", return_value=mock)` next to the existing one.

---

## Issue 6 — LOW: `X-Switchyard-Proxy-Stream-Read-Timeout` response header

Not in the design doc, but useful for confirming policy in production (and already test-asserted at `tests/test_proxy.py:248`). Harmless, keep it.

---

## Action Plan — In Order

1. **Decide scope on `config.yaml`** (revert or split).
2. **Apply Remediation 1**: convert `_blocking_proxy` to async, add `await` at both call sites.
3. **Apply Remediation 2**: add `except httpx.TimeoutException` inside `_generate()`.
4. **Apply Remediation 3**: add the mid-stream timeout test.
5. **Update existing non-streaming proxy tests** to match the async signature:
   - `tests/test_proxy.py:128-185` (`test_non_streaming_proxy_success`)
   - `tests/test_proxy.py:394-456` (the remaining `httpx.Client` proxy passthrough test)
   - `tests/test_proxy.py:458-` (the second `httpx.Client` test)
   - `tests/test_errors.py:106-135` (`test_chat_completions_upstream_timeout`)
   - `tests/test_errors.py:137-` (`test_proxy_passthrough_upstream_error`)
   - `tests/test_api.py:994` and `tests/test_api.py:1224`
   
   Each becomes: `patch("switchyard.app.httpx.AsyncClient", ...)` with `AsyncMock` for `.post()`. The existing patterns from the streaming tests are the template.
6. **Re-run quality gates** from `switchyard-api/`:
   ```bash
   uv run pytest
   uv run ruff check src tests --fix
   uv run mypy src/switchyard
   ```
7. **Smoke**: rerun the streaming and non-streaming curls from the context doc and confirm clean `[DONE]` for streaming and full body for non-streaming.
8. **Commit** as `fix(api): make proxy fully async; harden mid-stream timeout` (or similar) and close SEP-003a.

Steps 2 and 5 are the biggest piece by volume — 7 tests to migrate. The pattern is mechanical: `Client`→`AsyncClient`, wrap `.post` with `AsyncMock`, await call sites.

Want me to execute this plan?