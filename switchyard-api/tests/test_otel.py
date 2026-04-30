"""Tests for OpenTelemetry integration hooks (T1.5)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def otel_app() -> FastAPI:
    """Minimal FastAPI app with OTel middleware."""
    from switchyard import otel

    app = FastAPI()
    otel.inject(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


class TestOtelMetrics:
    """Tests for OpenTelemetry metrics endpoint."""

    @pytest.mark.asyncio
    async def test_metrics_endpoint_available(self, otel_app: FastAPI) -> None:
        """/metrics endpoint returns 200."""
        transport = ASGITransport(app=otel_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_returns_text_plain(self, otel_app: FastAPI) -> None:
        """/metrics returns text/plain content type."""
        transport = ASGITransport(app=otel_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics")
        assert response.headers["content-type"].startswith("text/plain")


class TestOtelTracingHeaders:
    """Tests for tracing header propagation."""

    @pytest.mark.asyncio
    async def test_traceparent_round_trip(self, otel_app: FastAPI) -> None:
        """traceparent header is echoed in response (W3C propagation)."""
        trace_id = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        transport = ASGITransport(app=otel_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/health",
                headers={"traceparent": trace_id},
            )
        assert "traceparent" in response.headers
        assert response.headers["traceparent"] == trace_id


class TestOtelNoSdk:
    """Tests that OTel hooks work without the SDK installed."""

    def test_otel_module_imports_without_sdk(self) -> None:
        """otel module imports even with only opentelemetry-api."""
        from switchyard import otel
        assert otel is not None

    def test_inject_is_callable(self) -> None:
        """inject function is callable and accepts a FastAPI app."""
        from switchyard import otel

        app = FastAPI()
        # Should not raise even without SDK
        otel.inject(app)
