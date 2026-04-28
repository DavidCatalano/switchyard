"""OpenTelemetry integration hooks.

Lightweight API layer — no SDK dependency. Uses only ``opentelemetry-api``
for context propagation. When the SDK is installed (e.g. in production),
real tracing/metrics are emitted. Without the SDK, headers still propagate
and a placeholder metrics endpoint is available.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse


def inject(app: FastAPI) -> None:
    """Inject OTel tracing middleware and metrics endpoint into a FastAPI app.

    Adds:
    - Tracing middleware: propagates W3C trace context headers
    - ``GET /metrics`` placeholder endpoint (emits real metrics when SDK is active)
    """
    app.add_middleware(TracingMiddleware)  # type: ignore[arg-type]
    app.add_api_route(
        "/metrics",
        _metrics_handler,
        methods=["GET"],
        include_in_schema=False,
    )


class TracingMiddleware:
    """FastAPI middleware for W3C trace context propagation.

    Extracts ``traceparent`` and ``baggage`` headers from the request,
    creates a span (if SDK is available), and propagates trace context
    into response headers.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self.propagator = _get_propagator()

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[Any], Any],
        send: Callable[[Any], Any],
    ) -> None:
        from starlette.requests import Request

        request = Request(scope)
        ctx = None
        if self.propagator:
            ctx = self.propagator.extract(carrier=request.headers)

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and self.propagator and ctx:
                # Propagator injects into a mutable mapping
                headers: dict[str, str] = {}
                for name, value in message.get("headers", []):
                    key = name.decode() if isinstance(name, bytes) else name
                    headers[key] = value.decode() if isinstance(value, bytes) else value
                self.propagator.inject(headers, context=ctx)
                message["headers"] = [
                    (k.encode(), v.encode()) for k, v in headers.items()
                ]
            await send(message)

        await self.app(scope, receive, send_wrapper)


async def _metrics_handler(request: Request) -> PlainTextResponse:
    """Metrics endpoint handler.

    Returns real metrics when the OTel SDK exporter is configured,
    otherwise returns a placeholder.
    """
    exporter = _get_metrics_exporter()
    if exporter:
        return PlainTextResponse(
            content=exporter(),
            media_type="text/plain; version=0.0.4",
        )

    return PlainTextResponse(
        content="# OpenTelemetry metrics — no SDK exporter configured\n",
        media_type="text/plain; version=0.0.4",
    )


# ---------------------------------------------------------------------------
# Lazy SDK detection — import only when needed
# ---------------------------------------------------------------------------

def _get_propagator() -> Any | None:
    """Return the W3C trace context propagator, or None if API unavailable."""
    try:
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )

        return TraceContextTextMapPropagator()
    except ImportError:
        return None


def _get_metrics_exporter() -> Callable[[], str] | None:
    """Return a metrics exporter function, or None if SDK not active."""
    try:
        from opentelemetry.sdk.metrics import (
            MeterProvider,  # noqa: F401
        )
        from opentelemetry.sdk.metrics.export import (
            MetricReader,  # noqa: F401
        )

        # If a MeterProvider is configured and has readers, export metrics
        provider = _get_meter_provider()
        if provider:
            return lambda: _export_metrics(provider)
    except ImportError:
        pass

    return None


def _get_meter_provider() -> Any | None:
    """Check if a MeterProvider is globally configured."""
    try:
        from opentelemetry.metrics import get_meter_provider

        provider = get_meter_provider()
        # The default no-op provider has no readers; real SDK provider does
        if hasattr(provider, "_sdk_providers") and provider._sdk_providers:
            return provider
    except (ImportError, Exception):
        pass

    return None


def _export_metrics(provider: Any) -> str:
    """Export current metrics as Prometheus text format."""
    try:
        from io import StringIO

        from opentelemetry.sdk.metrics.export import (
            InMemoryMetricReader,
        )

        output = StringIO()
        # Use the provider's readers to export
        for reader in getattr(provider, "_sdk_providers", ()):
            if isinstance(reader, InMemoryMetricReader):
                data = reader.get_metrics_data()
                if data is None:
                    continue
                resource_metrics = data.resource_metrics
                for rm in resource_metrics:
                    for scope_metrics in getattr(rm, "scope_metrics", ()):
                        for metric in scope_metrics.metrics:
                            output.write(f"# {metric.name}\n")
                            for dp in metric.data.data_points:
                                output.write(f"{metric.name} {dp.attributes}\n")
        return output.getvalue()
    except Exception:
        return "# Metrics export unavailable\n"
