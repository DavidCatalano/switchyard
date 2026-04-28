"""Minimal FastAPI application for Switchyard API.

Creates and configures the application instance with middleware,
logging, and essential endpoints.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from switchyard.config.loader import ConfigLoader
from switchyard.core.lifecycle import LifecycleManager
from switchyard.logging import RequestContextMiddleware, configure_logging


async def _validation_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """Normalize Pydantic validation errors into JSON responses."""
    return JSONResponse(
        status_code=400,
        content={"detail": exc.errors()[0].get("msg", "invalid request")},
    )


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
    app.add_exception_handler(RequestValidationError, _validation_handler)  # type: ignore[arg-type]

    # Add middleware
    app.add_middleware(RequestContextMiddleware)

    # Store shared state
    app.state.config = config
    app.state.manager = LifecycleManager()

    # Register endpoints
    _register_routes(app)

    return app


class LoadModelRequest(BaseModel):
    """Request body for loading a model."""

    model: str


class UnloadModelRequest(BaseModel):
    """Request body for unloading a model."""

    model: str


def _register_routes(app: FastAPI) -> None:
    """Register application routes."""

    config = app.state.config
    manager = app.state.manager

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    @app.post("/models/load", status_code=202)
    async def load_model(body: LoadModelRequest) -> dict[str, Any]:
        """Load a model deployment (async).

        Starts the container via the backend adapter and returns immediately.
        Poll `GET /models/{model}/status` for progress.
        """
        model_name = body.model
        if model_name not in config.models:
            raise HTTPException(
                status_code=404,
                detail=f"model {model_name!r} not found in config",
            )

        try:
            info = await manager.load_model(model_name, config.models[model_name])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return {
            "model_name": info.model_name,
            "backend": info.backend,
            "port": info.port,
            "status": info.status,
            "container_id": info.container_id,
        }

    @app.post("/models/unload")
    async def unload_model(body: UnloadModelRequest) -> dict[str, str]:
        """Unload a model deployment."""
        model_name = body.model
        try:
            await manager.unload_model(model_name)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"model {model_name!r} not found",
            )

        return {"model_name": model_name, "status": "stopped"}

    @app.get("/models")
    async def list_models() -> list[dict[str, Any]]:
        """List all model deployments with status."""
        names = manager.state.list_deployments()
        return [
            {
                "model_name": info.model_name,
                "backend": info.backend,
                "port": info.port,
                "status": info.status,
                "started_at": info.started_at,
            }
            for info in (manager.state.get(n) for n in names)
        ]

    @app.get("/models/{model}/status")
    async def get_model_status(model: str) -> dict[str, str]:
        """Get status of a single model deployment."""
        try:
            info = manager.state.get(model)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"model {model!r} not found")

        return {"model_name": info.model_name, "status": info.status}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        """OpenAI-compatible chat completions endpoint.

        Extracts model from request body, verifies deployment is running,
        and proxies to the backend container. Supports both streaming
        and non-streaming responses.
        """
        body = await request.json()
        model_name = body.get("model", "")
        deployment = _get_running_deployment(model_name, manager)
        backend_url = f"http://localhost:{deployment.port}"

        streaming = body.get("stream", False)

        if streaming:
            return _streaming_proxy(backend_url + "/v1/chat/completions", body)
        return _blocking_proxy(backend_url + "/v1/chat/completions", body)

    @app.post("/v1/backends/{model}/{path:path}")
    async def backend_passthrough(
        request: Request, model: str, path: str,
    ) -> Any:
        """Scoped passthrough to backend-specific endpoints.

        Routes to backend container for model-specific API calls
        (embeddings, tool calls, etc.).
        """
        deployment = _get_running_deployment(model, manager)
        backend_url = f"http://localhost:{deployment.port}/v1/{path}"
        body = await request.json()

        return _blocking_proxy(backend_url, body)


def _get_running_deployment(
    model_name: str, manager: LifecycleManager,
) -> Any:
    """Get a running deployment or raise appropriate HTTPException."""
    try:
        deployment = manager.state.get(model_name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"model {model_name!r} not found",
        )
    if deployment.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"model {model_name!r} is not running "
                    f"(status: {deployment.status!r})",
        )
    return deployment


def _blocking_proxy(url: str, body: dict[str, Any]) -> JSONResponse:
    """Make a blocking proxy request to a backend container."""
    with httpx.Client() as client:
        response = client.post(url, json=body)
    return JSONResponse(
        status_code=response.status_code,
        content=response.json() if response.status_code != 204 else {},
    )


def _streaming_proxy(url: str, body: dict[str, Any]) -> StreamingResponse:
    """Stream SSE response from backend to client transparently."""
    with httpx.Client() as client:
        response = client.post(url, json=body, timeout=5.0)

        def _generate() -> Any:
            yield from response.iter_bytes()

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
