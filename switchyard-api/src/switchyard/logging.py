"""Structured logging with structlog.

Configures JSON renderer for production, console renderer for development.
Provides FastAPI middleware for request ID propagation and structured access
logging.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


def _json_renderer(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> str:
    """Render log event as JSON line."""
    import json

    return json.dumps(event_dict, default=str)


def _console_renderer(
    _logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> str:
    """Render log event as colored console line (dev mode)."""
    # Drop the logger and method_name from event_dict to avoid duplication
    event_dict.pop("logger", None)
    event_dict.pop("level", None)

    parts: list[str] = []
    for key, value in event_dict.items():
        if key == "message":
            continue
        parts.append(f"{key}={value}")

    prefix = f"[{method_name.upper()}] "
    event = event_dict.get("message", "")
    return f"{prefix}{event} {' '.join(parts)}" if parts else f"{prefix}{event}"


def configure_logging(*, log_level: str = "info", use_json: bool = True) -> None:
    """Configure structlog for the application.

    Args:
        log_level: Logging level string (debug, info, warning, error, critical).
        use_json: True for JSON output (production), False for console (dev).
    """
    renderer = _json_renderer if use_json else _console_renderer

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.UnicodeDecoder(),
        structlog.processors.EventRenamer(to="message"),
        renderer,
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper()),
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # Set root stdlib logger level so structlog filtering applies
    logging.getLogger().setLevel(getattr(logging, log_level.upper()))


class RequestContextMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that adds a request ID to every request.

    Generates a UUID per request, adds it to the response header as
    ``X-Request-ID``, and propagates it into structlog context vars so
    every log line for that request includes the ID.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = str(uuid4())

        # Propagate into structlog context for all log calls in this request
        structlog.contextvars.bind_contextvars(request_id=request_id)

        try:
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()
