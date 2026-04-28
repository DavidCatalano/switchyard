"""Minimal FastAPI application for Switchyard API.

Creates and configures the application instance with middleware,
logging, and essential endpoints.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI

from switchyard.config.loader import ConfigLoader
from switchyard.logging import RequestContextMiddleware, configure_logging


def create_app(config_overrides: dict[str, Any] | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config_overrides: Optional dict to merge into loaded config
            (useful for testing).

    Returns:
        Configured FastAPI application instance.
    """
    # Load configuration
    config = ConfigLoader.load()
    if config_overrides:
        config = config.model_validate(
            _deep_merge(config.model_dump(by_alias=True), config_overrides)
        )

    # Configure structured logging
    configure_logging(
        log_level=config.global_config.log_level,
        use_json=os.getenv("SWITCHYARD_ENV", "development") == "production",
    )

    # Create FastAPI app
    app = FastAPI(title="Switchyard API")

    # Add middleware
    app.add_middleware(RequestContextMiddleware)

    # Register endpoints
    _register_routes(app)

    return app


def _register_routes(app: FastAPI) -> None:
    """Register application routes."""

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
