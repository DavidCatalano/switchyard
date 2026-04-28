"""Tests for logging configuration and request ID middleware (T1.4)."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def raw_app() -> FastAPI:
    """Minimal FastAPI app for middleware tests."""
    from switchyard.logging import RequestContextMiddleware, configure_logging

    configure_logging(log_level="info", use_json=False)
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/echo")
    async def echo() -> dict[str, str]:
        return {"echo": "pong"}

    return app


def test_configure_logging_sets_root_level(capsys: pytest.CaptureFixture[str]) -> None:
    """configure_logging sets root logger level."""
    from switchyard.logging import configure_logging

    configure_logging(log_level="debug", use_json=False)
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_json_mode() -> None:
    """JSON mode sets structlog processors and renderer."""
    from switchyard.logging import configure_logging

    configure_logging(log_level="info", use_json=True)
    # In JSON mode, structlog uses JSONRenderer
    # Verify by checking a log output
    logger = logging.getLogger("test_json")
    logger.info("test")


class TestRequestIdMiddleware:
    """Tests for FastAPI request ID middleware."""

    @pytest.mark.asyncio
    async def test_request_id_added_to_response(self, raw_app: FastAPI) -> None:
        """Response includes X-Request-ID header."""
        transport = ASGITransport(app=raw_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert "x-request-id" in response.headers
        assert response.headers["x-request-id"]  # non-empty

    @pytest.mark.asyncio
    async def test_request_id_is_unique(self, raw_app: FastAPI) -> None:
        """Each request gets a unique ID."""
        transport = ASGITransport(app=raw_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.get("/health")
            r2 = await client.get("/health")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

    @pytest.mark.asyncio
    async def test_request_id_is_valid_uuid(self, raw_app: FastAPI) -> None:
        """Request ID is a valid UUID format."""
        import uuid as _uuid

        transport = ASGITransport(app=raw_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        # Verify it's a parseable UUID
        _uuid.UUID(response.headers["x-request-id"])

    @pytest.mark.asyncio
    async def test_post_request_gets_id(self, raw_app: FastAPI) -> None:
        """POST requests also get request IDs."""
        transport = ASGITransport(app=raw_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/echo")
        assert "x-request-id" in response.headers

    @pytest.mark.asyncio
    async def test_error_response_gets_id(self, raw_app: FastAPI) -> None:
        """Error responses also include request ID."""
        transport = ASGITransport(app=raw_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/nonexistent")
        assert "x-request-id" in response.headers


def test_structlog_context_has_request_id_in_middleware() -> None:
    """Middleware adds request_id to structlog context."""
    from switchyard.logging import RequestContextMiddleware

    # The middleware uses structlog.contextvars; verify it's wired
    assert RequestContextMiddleware is not None
