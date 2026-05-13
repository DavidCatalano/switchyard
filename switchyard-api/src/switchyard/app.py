"""Minimal FastAPI application for Switchyard API.

Creates and configures the application instance with middleware,
logging, and essential endpoints.

Uses entity-based config (SEP-002): hosts, runtimes, models, deployments.
Process-local bootstrap settings come from ``.env`` via ``AppSettings``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError as PydanticValidationError

from switchyard.adapters.vllm import register_vllm
from switchyard.config.loader import ConfigLoader, resolve_deployment
from switchyard.config.models import AppSettings, Config
from switchyard.core.lifecycle import LifecycleManager
from switchyard.core.merge import deep_merge
from switchyard.core.ports import PortAllocator
from switchyard.logging import RequestContextMiddleware, configure_logging

logger = logging.getLogger(__name__)


async def _validation_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """Normalize Pydantic validation errors into JSON responses."""
    return JSONResponse(
        status_code=400,
        content={"detail": exc.errors()[0].get("msg", "invalid request")},
    )


async def _resolver_handler(
    request: Request, exc: PydanticValidationError,
) -> JSONResponse:
    """Handle ValidationError from config resolution.

    When resolve_deployment() validates merged runtime args through
    VLLMRuntimeConfig, any typos surface as ValidationError. This handler
    catches them and returns a structured 400 instead of a 500.
    """
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = " ".join(str(p) for p in first.get("loc", []))
        msg = first.get("msg", "config validation error")
        detail = f"{msg} (field: {loc})" if loc else msg
    else:
        detail = "config validation error"
    return JSONResponse(
        status_code=400,
        content={"detail": detail},
    )


def _resolve_deployment(
    config: Config,
    deployment: str,
) -> Any:
    """Resolve deployment, converting resolution errors to HTTP 400.

    Wraps ``resolve_deployment()`` so that Pydantic validation errors
    from runtime config typos and ``ValueError`` from merge issues
    surface as clear 400 responses rather than 500 errors.
    """
    try:
        return resolve_deployment(config, deployment)
    except PydanticValidationError as exc:
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = " ".join(str(p) for p in first.get("loc", []))
            msg = first.get("msg", "config validation error")
            detail = f"{msg} (field: {loc})" if loc else msg
        else:
            detail = "config validation error"
        raise HTTPException(status_code=400, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
            deep_merge(config.model_dump(by_alias=True), config_overrides)
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
    app.add_exception_handler(PydanticValidationError, _resolver_handler)  # type: ignore[arg-type]

    # Add middleware
    app.add_middleware(RequestContextMiddleware)

    # Store shared state
    app.state.config = config

    # Create lifecycle manager with port range from active host
    from switchyard.core.docker import create_default_factory
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
        docker_client_factory=create_default_factory(),
    )

    # Register endpoints
    _register_routes(app)

    return app


def _register_routes(app: FastAPI) -> None:
    """Register application routes."""

    config: Config = app.state.config
    manager = app.state.manager

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    # ── /api/ deployment lifecycle routes ──────────────────────────

    def _derive_health(status: str) -> str:
        """Derive health string from deployment status."""
        return {
            "running": "healthy",
            "loading": "starting",
            "error": "unhealthy",
            "stopped": "unknown",
        }.get(status, "unknown")

    @app.get("/api/deployments")
    async def list_deployments() -> list[dict[str, Any]]:
        """List all configured deployments with current status.

        Reconciles each deployment with Docker before reporting status:
        adopts running containers, clears stale in-memory state.
        """
        results: list[dict[str, Any]] = []
        for dep_name, dep_cfg in config.deployments.items():
            resolved = _resolve_deployment(config, dep_name)
            result = manager.reconcile(dep_name, resolved)

            if result.info is not None:
                status = result.info.status
            else:
                status = "stopped"
            results.append({
                "deployment_name": dep_name,
                "model": dep_cfg.model,
                "runtime": dep_cfg.runtime,
                "host": dep_cfg.host,
                "status": status,
                "health": _derive_health(status),
                "container_status": result.container_status,
            })
        return results

    @app.get("/api/deployments/{deployment}")
    async def get_deployment(deployment: str) -> dict[str, Any]:
        """Get deployment detail: configured intent + small status summary.

        Reconciles with Docker before reporting to adopt running containers
        or clear stale state.
        """
        if deployment not in config.deployments:
            raise HTTPException(
                status_code=404,
                detail=f"deployment {deployment!r} not found in config",
            )
        dep_cfg = config.deployments[deployment]

        # Resolve and reconcile
        resolved = _resolve_deployment(config, deployment)
        result = manager.reconcile(deployment, resolved)

        if result.info is not None:
            status = result.info.status
        else:
            status = "stopped"

        resp: dict[str, Any] = {
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
            "health": _derive_health(status),
            "container_status": result.container_status,
        }
        if result.docker_error is not None:
            resp["docker_error"] = result.docker_error
        return resp

    @app.get("/api/deployments/{deployment}/status")
    async def get_deployment_status(deployment: str) -> dict[str, Any]:
        """Get live operational status for a deployment."""
        if deployment not in config.deployments:
            raise HTTPException(
                status_code=404,
                detail=f"deployment {deployment!r} not found in config",
            )

        # Resolve and reconcile
        resolved = _resolve_deployment(config, deployment)
        result = manager.reconcile(deployment, resolved)

        if result.info is not None:
            info = result.info
            status = info.status
            resp: dict[str, Any] = {
                "deployment_name": info.model_name,
                "status": status,
                "health": _derive_health(status),
                "port": info.port,
                "container_id": info.container_id,
                "started_at": info.started_at.isoformat(),
                "container_status": result.container_status,
            }
        else:
            resp = {
                "deployment_name": deployment,
                "status": "stopped",
                "health": "unknown",
                "container_status": result.container_status,
            }

        if result.docker_error is not None:
            resp["docker_error"] = result.docker_error

        return resp

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

        resolved = _resolve_deployment(config, deployment)

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
        """Unload a deployment.

        Reconciles with Docker first so that stopped running containers
        after API restart are properly stopped rather than silently ignored.

        If Docker is unreachable during stop, returns 500 and preserves
        deployment state.
        """
        if deployment not in config.deployments:
            raise HTTPException(
                status_code=404,
                detail=f"deployment {deployment!r} not found in config",
            )

        # Reconcile first: if a running container exists after API restart,
        # adopt it into state so it can be stopped
        resolved = _resolve_deployment(config, deployment)
        manager.reconcile(deployment, resolved)

        try:
            await manager.unload_model(deployment)
        except KeyError:
            # Deployment not in state (already stopped/unloaded)
            return {"deployment_name": deployment, "status": "stopped"}
        except Exception as exc:
            # Docker/adapter stop failed — preserve state, return error
            logger.warning(
                "unload: adapter.stop() failed for %s: %s",
                deployment, exc, exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"failed to stop deployment {deployment!r}: {exc}",
            )

        return {"deployment_name": deployment, "status": "stopped"}

    @app.post("/api/proxy/{deployment}/{path:path}")
    async def proxy_passthrough(
        request: Request, deployment: str, path: str,
    ) -> Any:
        """Scoped passthrough to backend-specific endpoints.

        The path is forwarded literally to the backend.
        Example: /api/proxy/deployment/v1/embeddings -> /v1/embeddings
        """
        # Reconcile to adopt running containers, clear stale state
        if deployment in config.deployments:
            resolved = _resolve_deployment(config, deployment)
            manager.reconcile(deployment, resolved)

        dep = _get_running_deployment(deployment, manager)
        backend_url = _backend_url(dep, config)
        body = await request.json()

        return await _blocking_proxy(backend_url + f"/{path}", body)

    # ── /v1/ OpenAI-compatible inference routes ────────────────────

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        """OpenAI-compatible model discovery endpoint.

        Returns active (running) deployments as a list of OpenAI-compatible
        model objects. Only deployments with status "running" are included.

        Reconciles each configured deployment with Docker to adopt running
        containers and clear stale state.
        """
        # Reconcile all configured deployments
        for dep_name in config.deployments:
            resolved = _resolve_deployment(config, dep_name)
            manager.reconcile(dep_name, resolved)
            # Docker errors are informational — state preserved, don't clear

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

        # Reconcile to adopt running containers, clear stale state
        if deployment_name in config.deployments:
            resolved = _resolve_deployment(config, deployment_name)
            manager.reconcile(deployment_name, resolved)
            # Docker errors are informational — state preserved, don't clear

        deployment = _get_running_deployment(deployment_name, manager)
        backend_url = _backend_url(deployment, config)
        backend_body = dict(body)
        backend_body["model"] = deployment.metadata.get(
            "served_model_name", deployment_name
        )

        streaming = body.get("stream", False)

        if streaming:
            return await _streaming_proxy(
                backend_url + "/v1/chat/completions", backend_body
            )
        return await _blocking_proxy(
            backend_url + "/v1/chat/completions", backend_body
        )


def _mask_sensitive_args(args: dict[str, Any]) -> dict[str, Any]:
    """Mask sensitive runtime args recursively.

    Normalizes keys by replacing '-' with '_' and redacts any key containing
    'token', 'secret', 'password', or 'api_key'. Handles nested dicts including
    extra_args.
    """
    sensitive_substrings = {"token", "secret", "password", "api_key"}
    masked: dict[str, Any] = {}
    for key, value in args.items():
        normalized = key.replace("-", "_")
        if any(substr in normalized for substr in sensitive_substrings):
            masked[key] = "***redacted***"
        elif isinstance(value, dict):
            masked[key] = _mask_sensitive_args(value)
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
    """Build the backend URL using current configured host reachability.

    Host reachability is config-owned. Deployment metadata is retained as
    fallback for older/adopted state, but current config must win so stale
    in-memory metadata cannot pin proxy traffic to an obsolete hostname.
    """
    host = None
    scheme = None

    deployment_config = config.deployments.get(deployment.model_name)
    if deployment_config is not None:
        host_config = config.hosts.get(deployment_config.host)
        if host_config is not None:
            host = host_config.backend_host
            scheme = host_config.backend_scheme

    if host is None or scheme is None:
        host = host or deployment.metadata.get("backend_host")
        scheme = scheme or deployment.metadata.get("backend_scheme")

    if host is None or scheme is None:
        active_host, active_scheme, _, _ = _resolve_active_host(config)
        host = host or active_host
        scheme = scheme or active_scheme
    return f"{scheme}://{host}:{deployment.port}"


def _proxy_non_stream_timeout(settings: AppSettings) -> httpx.Timeout:
    """Build timeout policy for complete buffered proxy responses."""
    return httpx.Timeout(
        connect=settings.proxy_connect_timeout_seconds,
        read=settings.proxy_non_stream_read_timeout_seconds,
        write=settings.proxy_write_timeout_seconds,
        pool=settings.proxy_pool_timeout_seconds,
    )


def _proxy_stream_timeout(settings: AppSettings) -> httpx.Timeout:
    """Build timeout policy for streaming proxy responses."""
    return httpx.Timeout(
        connect=settings.proxy_connect_timeout_seconds,
        read=settings.proxy_stream_read_timeout_seconds,
        write=settings.proxy_write_timeout_seconds,
        pool=settings.proxy_pool_timeout_seconds,
    )


async def _blocking_proxy(url: str, body: dict[str, Any]) -> JSONResponse:
    """Make a proxy request to a backend container (async)."""
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
        return JSONResponse(
            status_code=504,
            content={"detail": "request timeout"},
        )
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"detail": "backend unavailable"},
        )


async def _streaming_proxy(url: str, body: dict[str, Any]) -> Any:
    """Stream SSE response from backend to client transparently."""
    settings = AppSettings()
    timeout = _proxy_stream_timeout(settings)
    client: httpx.AsyncClient | None = None
    stream_ctx: Any | None = None

    try:
        client = httpx.AsyncClient(timeout=timeout)
        stream_ctx = client.stream("POST", url, json=body)
        response = await stream_ctx.__aenter__()
    except httpx.TimeoutException:
        if client is not None:
            await client.aclose()
        return JSONResponse(
            status_code=504,
            content={"detail": "request timeout"},
        )
    except httpx.ConnectError:
        if client is not None:
            await client.aclose()
        return JSONResponse(
            status_code=503,
            content={"detail": "backend unavailable"},
        )

    headers = getattr(response, "headers", {}) or {}
    media_type = (
        headers.get("content-type", "text/event-stream")
        if isinstance(headers, dict)
        else "text/event-stream"
    )

    async def _generate() -> Any:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        except httpx.TimeoutException:
            logger.warning(
                "proxy: upstream stream timeout for %s", url, exc_info=True,
            )
        finally:
            assert stream_ctx is not None
            assert client is not None
            await stream_ctx.__aexit__(None, None, None)
            await client.aclose()

    read_timeout = "none" if timeout.read is None else str(timeout.read)
    return StreamingResponse(
        _generate(),
        status_code=response.status_code,
        media_type=media_type,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Switchyard-Proxy-Stream-Read-Timeout": read_timeout,
        },
    )



