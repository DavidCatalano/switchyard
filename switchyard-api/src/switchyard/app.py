"""Minimal FastAPI application for Switchyard API.

Creates and configures the application instance with middleware,
logging, and essential endpoints.

Uses entity-based config (SEP-002): hosts, runtimes, models, deployments.
Process-local bootstrap settings come from ``.env`` via ``AppSettings``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from switchyard.adapters.vllm import register_vllm
from switchyard.config.loader import ConfigLoader, resolve_deployment
from switchyard.config.models import AppSettings, Config
from switchyard.core.lifecycle import LifecycleManager
from switchyard.core.ports import PortAllocator
from switchyard.logging import RequestContextMiddleware, configure_logging


async def _validation_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """Normalize Pydantic validation errors into JSON responses."""
    return JSONResponse(
        status_code=400,
        content={"detail": exc.errors()[0].get("msg", "invalid request")},
    )


def _resolve_active_host(config: Config) -> tuple[str, str, str, list[int]]:
    """Resolve backend settings and port range from active host.

    Uses ``SWITCHYARD_ACTIVE_HOST`` from ``.env`` to pick the host.
    Raises if the configured active host is not found in config.
    Falls back to the first defined host only when
    ``SWITCHYARD_ACTIVE_HOST`` is not set.
    """
    settings = AppSettings()
    host_name = settings.active_host
    if host_name:
        if host_name not in config.hosts:
            raise ValueError(
                f"SWITCHYARD_ACTIVE_HOST={host_name!r} but host {host_name!r} "
                f"is not defined in config. Available hosts: {list(config.hosts)}"
            )
        host = config.hosts[host_name]
    elif config.hosts:
        host_name = next(iter(config.hosts))
        host = config.hosts[host_name]
    else:
        return "localhost", "http", "model-runtime", [8000, 8010]

    return (
        host.backend_host,
        host.backend_scheme,
        host.docker_network,
        host.port_range,
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

    # Configure structured logging from .env
    settings = AppSettings()
    log_level = settings.log_level or "info"
    configure_logging(
        log_level=log_level,
        use_json=os.getenv("SWITCHYARD_ENV", "development") == "production",
    )

    # Resolve host-level settings for lifecycle manager
    backend_host, backend_scheme, docker_network, port_range = (
        _resolve_active_host(config)
    )

    # Create FastAPI app
    app = FastAPI(title="Switchyard API")
    app.add_exception_handler(RequestValidationError, _validation_handler)  # type: ignore[arg-type]

    # Add middleware
    app.add_middleware(RequestContextMiddleware)

    # Store shared state
    app.state.config = config

    # Create lifecycle manager with port range from active host
    from switchyard.core.registry import AdapterRegistry

    registry = AdapterRegistry()
    register_vllm(registry)
    port_range_count = port_range[1] - port_range[0] + 1
    app.state.manager = LifecycleManager(
        registry=registry,
        port_allocator=PortAllocator(
            base_port=port_range[0], max_attempts=port_range_count,
        ),
        backend_host=backend_host,
        backend_scheme=backend_scheme,
        docker_network=docker_network,
    )

    # Register endpoints
    _register_routes(app)

    return app


class LoadModelRequest(BaseModel):
    """Request body for loading a deployment."""

    deployment: str


class UnloadModelRequest(BaseModel):
    """Request body for unloading a deployment."""

    deployment: str


def _register_routes(app: FastAPI) -> None:
    """Register application routes."""

    config: Config = app.state.config
    manager = app.state.manager

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    @app.post("/deployments/load", status_code=202)
    async def load_deployment(body: LoadModelRequest) -> dict[str, Any]:
        """Load a deployment (async).

        Resolves the deployment config and starts the container via the
        backend adapter. Returns immediately; poll status for progress.
        """
        deployment_name = body.deployment
        if deployment_name not in config.deployments:
            raise HTTPException(
                status_code=404,
                detail=f"deployment {deployment_name!r} not found in config",
            )

        # Resolve the deployment and start it via the lifecycle manager.
        resolved = resolve_deployment(config, deployment_name)

        try:
            info = await manager.load_model(deployment_name, resolved)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return {
            "deployment_name": info.model_name,
            "backend": info.backend,
            "port": info.port,
            "status": info.status,
            "container_id": info.container_id,
        }

    @app.post("/deployments/unload")
    async def unload_deployment(body: UnloadModelRequest) -> dict[str, str]:
        """Unload a deployment."""
        deployment_name = body.deployment
        try:
            await manager.unload_model(deployment_name)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"deployment {deployment_name!r} not found",
            )

        return {"deployment_name": deployment_name, "status": "stopped"}

    @app.get("/deployments")
    async def list_deployments() -> list[dict[str, Any]]:
        """List all deployments with status."""
        names = manager.state.list_deployments()
        return [
            {
                "deployment_name": info.model_name,
                "backend": info.backend,
                "port": info.port,
                "status": info.status,
                "started_at": info.started_at,
            }
            for info in (manager.state.get(n) for n in names)
        ]

    @app.get("/deployments/{deployment}/status")
    async def get_deployment_status(deployment: str) -> dict[str, str]:
        """Get status of a single deployment."""
        try:
            info = manager.state.get(deployment)
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"deployment {deployment!r} not found",
            )

        return {"deployment_name": info.model_name, "status": info.status}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        """OpenAI-compatible chat completions endpoint.

        Extracts deployment from request body (model field), verifies
        deployment is running, and proxies to the backend container.
        Supports both streaming and non-streaming responses.
        """
        body = await request.json()
        deployment_name = body.get("model", "")
        deployment = _get_running_deployment(deployment_name, manager)
        backend_url = _backend_url(deployment, config)

        streaming = body.get("stream", False)

        if streaming:
            return _streaming_proxy(backend_url + "/v1/chat/completions", body)
        return _blocking_proxy(backend_url + "/v1/chat/completions", body)

    @app.post("/v1/backends/{deployment}/{path:path}")
    async def backend_passthrough(
        request: Request, deployment: str, path: str,
    ) -> Any:
        """Scoped passthrough to backend-specific endpoints.

        Routes to backend container for deployment-specific API calls
        (embeddings, tool calls, etc.).
        """
        dep = _get_running_deployment(deployment, manager)
        backend_url = _backend_url(dep, config)
        body = await request.json()

        return _blocking_proxy(backend_url + f"/v1/{path}", body)


def _get_running_deployment(
    name: str, manager: LifecycleManager,
) -> Any:
    """Get a running deployment or raise appropriate HTTPException."""
    try:
        deployment = manager.state.get(name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"deployment {name!r} not found",
        )
    if deployment.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"deployment {name!r} is not running "
                    f"(status: {deployment.status!r})",
        )
    return deployment


def _backend_url(deployment: Any, config: Config) -> str:
    """Build the backend URL using configured host and scheme.

    Deployment metadata takes precedence (set by adapter at startup).
    Falls back to the active host's backend_host/backend_scheme.
    """
    host = deployment.metadata.get("backend_host")
    scheme = deployment.metadata.get("backend_scheme")
    if host is None or scheme is None:
        # Fallback to active host configuration
        active_host, active_scheme, _, _ = _resolve_active_host(config)
        host = host or active_host
        scheme = scheme or active_scheme
    return f"{scheme}://{host}:{deployment.port}"


def _blocking_proxy(url: str, body: dict[str, Any]) -> JSONResponse:
    """Make a blocking proxy request to a backend container."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=body)
        return JSONResponse(
            status_code=response.status_code,
            content=response.json() if response.status_code != 204 else {},
        )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"detail": "request timeout"},
        )
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"detail": "backend unavailable"},
        )


def _streaming_proxy(url: str, body: dict[str, Any]) -> Any:
    """Stream SSE response from backend to client transparently."""

    def _generate() -> Any:
        with httpx.Client(timeout=10.0) as client:
            try:
                with client.stream("POST", url, json=body) as response:
                    yield from response.iter_bytes()
            except httpx.TimeoutException:
                # Re-raise to let caller handle error status
                raise
            except httpx.ConnectError:
                raise

    try:
        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"detail": "request timeout"},
        )
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"detail": "backend unavailable"},
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
