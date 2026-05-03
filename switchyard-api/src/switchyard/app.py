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

    # ── /api/ deployment lifecycle routes ──────────────────────────

    @app.get("/api/deployments")
    async def list_deployments() -> list[dict[str, Any]]:
        """List all configured deployments with current status."""
        results: list[dict[str, Any]] = []
        for dep_name, dep_cfg in config.deployments.items():
            try:
                info = manager.state.get(dep_name)
                status = info.status
            except KeyError:
                status = "stopped"
            results.append({
                "deployment_name": dep_name,
                "model": dep_cfg.model,
                "runtime": dep_cfg.runtime,
                "host": dep_cfg.host,
                "status": status,
            })
        return results

    @app.get("/api/deployments/{deployment}")
    async def get_deployment(deployment: str) -> dict[str, Any]:
        """Get deployment detail: configured intent + small status summary."""
        if deployment not in config.deployments:
            raise HTTPException(
                status_code=404,
                detail=f"deployment {deployment!r} not found in config",
            )
        dep_cfg = config.deployments[deployment]
        try:
            info = manager.state.get(deployment)
            status = info.status
        except KeyError:
            status = "stopped"

        # Resolve store paths for the detail response
        resolved = resolve_deployment(config, deployment)

        return {
            "deployment_name": deployment,
            "model": dep_cfg.model,
            "runtime": dep_cfg.runtime,
            "host": dep_cfg.host,
            "placement": {
                "accelerator_ids": dep_cfg.placement.accelerator_ids
                if dep_cfg.placement
                else [],
            },
            "model_host_path": resolved.model_host_path,
            "model_container_path": resolved.model_container_path,
            "runtime_args": _mask_sensitive_args(resolved.runtime_args),
            "status": status,
        }

    @app.get("/api/deployments/{deployment}/status")
    async def get_deployment_status(deployment: str) -> dict[str, Any]:
        """Get live operational status for a deployment."""
        if deployment not in config.deployments:
            raise HTTPException(
                status_code=404,
                detail=f"deployment {deployment!r} not found in config",
            )
        try:
            info = manager.state.get(deployment)
            return {
                "deployment_name": info.model_name,
                "status": info.status,
                "port": info.port,
                "container_id": info.container_id,
                "started_at": info.started_at.isoformat(),
            }
        except KeyError:
            return {
                "deployment_name": deployment,
                "status": "stopped",
            }

    @app.post("/api/deployments/{deployment}/load", status_code=202)
    async def load_deployment(deployment: str) -> dict[str, Any]:
        """Load a deployment (async).

        Resolves the deployment config and starts the container via the
        backend adapter. Returns immediately; poll status for progress.
        """
        if deployment not in config.deployments:
            raise HTTPException(
                status_code=404,
                detail=f"deployment {deployment!r} not found in config",
            )

        resolved = resolve_deployment(config, deployment)

        try:
            info = await manager.load_model(deployment, resolved)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to start deployment {deployment!r}: {exc}",
            )

        return {
            "deployment_name": info.model_name,
            "backend": info.backend,
            "port": info.port,
            "status": info.status,
            "container_id": info.container_id,
        }

    @app.post("/api/deployments/{deployment}/unload")
    async def unload_deployment(deployment: str) -> dict[str, str]:
        """Unload a deployment."""
        if deployment not in config.deployments:
            raise HTTPException(
                status_code=404,
                detail=f"deployment {deployment!r} not found in config",
            )
        try:
            await manager.unload_model(deployment)
        except KeyError:
            # Deployment not in state (already stopped/unloaded)
            return {"deployment_name": deployment, "status": "stopped"}

        return {"deployment_name": deployment, "status": "stopped"}

    @app.post("/api/proxy/{deployment}/{path:path}")
    async def proxy_passthrough(
        request: Request, deployment: str, path: str,
    ) -> Any:
        """Scoped passthrough to backend-specific endpoints.

        The path is forwarded literally to the backend.
        Example: /api/proxy/deployment/v1/embeddings -> /v1/embeddings
        """
        dep = _get_running_deployment(deployment, manager)
        backend_url = _backend_url(dep, config)
        body = await request.json()

        return _blocking_proxy(backend_url + f"/{path}", body)

    # ── /v1/ OpenAI-compatible inference routes ────────────────────

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        """OpenAI-compatible model discovery endpoint.

        Returns active (running) deployments as a list of OpenAI-compatible
        model objects. Only deployments with status "running" are included.
        """
        deployments = manager.state.list_deployments()
        models = [
            {
                "id": info.model_name,
                "object": "model",
                "created": int(info.started_at.timestamp()),
                "owned_by": "switchyard",
            }
            for info in sorted(deployments.values(), key=lambda item: item.model_name)
            if info.status == "running"
        ]
        return {"object": "list", "data": models}

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
        backend_body = dict(body)
        backend_body["model"] = deployment.metadata.get(
            "served_model_name", deployment_name
        )

        streaming = body.get("stream", False)

        if streaming:
            return _streaming_proxy(
                backend_url + "/v1/chat/completions", backend_body
            )
        return _blocking_proxy(
            backend_url + "/v1/chat/completions", backend_body
        )


def _mask_sensitive_args(args: dict[str, Any]) -> dict[str, Any]:
    """Mask sensitive runtime args (tokens, keys, secrets) in the detail response."""
    sensitive_keys = {"hf_token", "api_key", "secret_key", "password", "token"}
    masked: dict[str, Any] = {}
    for key, value in args.items():
        if key in sensitive_keys and value is not None:
            masked[key] = "***redacted***"
        else:
            masked[key] = value
    return masked


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
    client: httpx.Client | None = None
    stream_ctx: Any | None = None

    try:
        client = httpx.Client(timeout=10.0)
        stream_ctx = client.stream("POST", url, json=body)
        response = stream_ctx.__enter__()
    except httpx.TimeoutException:
        if client is not None:
            client.close()
        return JSONResponse(
            status_code=504,
            content={"detail": "request timeout"},
        )
    except httpx.ConnectError:
        if client is not None:
            client.close()
        return JSONResponse(
            status_code=503,
            content={"detail": "backend unavailable"},
        )

    def _generate() -> Any:
        try:
            yield from response.iter_bytes()
        finally:
            assert stream_ctx is not None
            assert client is not None
            stream_ctx.__exit__(None, None, None)
            client.close()

    headers = getattr(response, "headers", {}) or {}
    media_type = (
        headers.get("content-type", "text/event-stream")
        if isinstance(headers, dict)
        else "text/event-stream"
    )
    return StreamingResponse(
        _generate(),
        status_code=response.status_code,
        media_type=media_type,
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
