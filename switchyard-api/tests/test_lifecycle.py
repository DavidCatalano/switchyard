"""Tests for the LifecycleManager.

Validates:
- load_model allocates port, calls adapter.start, records "loading" status
- load_model returns immediately (async) with DeploymentInfo
- Background health check transitions loading -> running on success
- Background health check transitions loading -> error on failure
- unload_model stops adapter, releases port, removes state
- Cannot load already-running deployment
- Cannot unload unknown deployment
- Health check on unknown deployment raises
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio

from switchyard.config.models import ResolvedDeployment
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.lifecycle import LifecycleManager
from switchyard.core.ports import PortAllocator
from switchyard.core.registry import AdapterRegistry


def _make_resolved(
    backend: str = "mock",
    deployment_name: str = "test-deployment",
    overrides: dict[str, Any] | None = None,
) -> ResolvedDeployment:
    """Create a minimal ResolvedDeployment for tests."""
    defaults: dict[str, Any] = {
        "deployment_name": deployment_name,
        "model_name": "test-model",
        "runtime_name": backend,
        "backend": backend,
        "host_name": "test-host",
        "backend_host": "localhost",
        "backend_scheme": "http",
        "port_range": [9500, 9600],
        "image": "mock:latest",
        "internal_port": 8000,
        "model_host_path": "/data/models/test",
        "model_container_path": "/models/test",
        "accelerator_ids": [],
        "docker_host": None,
        "docker_network": "model-runtime",
        "runtime_args": {"model": "/models/test"},
        "container_environment": {},
        "container_options": {},
        "store_mounts": {},
        "model_defaults": None,
    }
    if overrides:
        defaults.update(overrides)
    return ResolvedDeployment(**defaults)


@pytest.fixture
def adapter_class() -> type[BackendAdapter]:
    class MockAdapter(BackendAdapter):
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.starts: list[tuple[ResolvedDeployment, int]] = []
            self.stops: list[DeploymentInfo] = []
            self._healthy = True

        def start(
            self, resolved: ResolvedDeployment, port: int,
        ) -> DeploymentInfo:
            self.starts.append((resolved, port))
            return DeploymentInfo(
                model_name=resolved.deployment_name,
                backend=resolved.backend,
                port=port,
                status="loading",
                container_id=f"mock-{port}",
            )

        def stop(self, deployment: DeploymentInfo) -> None:
            self.stops.append(deployment)

        def health(self, deployment: DeploymentInfo) -> str:
            return "running" if self._healthy else "error"

        def endpoint(self, deployment: DeploymentInfo) -> str:
            return f"http://localhost:{deployment.port}"

    return MockAdapter


@pytest_asyncio.fixture
async def manager(adapter_class: type[BackendAdapter]) -> LifecycleManager:
    registry = AdapterRegistry()
    registry.register("mock", adapter_class)
    return LifecycleManager(
        registry=registry,
        port_allocator=PortAllocator(base_port=9500),
    )


# --- Tests ---


class TestLoadModel:
    """load_model tests."""

    async def test_load_transitions_to_loading(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        info = await manager.load_model("test-deployment", resolved)
        assert info.status == "loading"

    async def test_load_records_in_state(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        await manager.load_model("test-deployment", resolved)
        stored = manager.state.get("test-deployment")
        assert stored.status == "loading"
        assert stored.backend == "mock"

    async def test_load_allocates_port(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        await manager.load_model("test-deployment", resolved)
        assert 9500 in manager.port_allocator.allocated

    async def test_load_calls_adapter_start(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        await manager.load_model("test-deployment", resolved)
        adapter = manager._adapters.get("mock")
        assert len(adapter.starts) == 1
        assert adapter.starts[0][1] == 9500  # port

    async def test_load_multiple_sequential_ports(
        self, manager: LifecycleManager,
    ) -> None:
        resolved_a = _make_resolved(deployment_name="model-a")
        resolved_b = _make_resolved(deployment_name="model-b")
        await manager.load_model("model-a", resolved_a)
        await manager.load_model("model-b", resolved_b)
        info_a = manager.state.get("model-a")
        info_b = manager.state.get("model-b")
        assert info_a.port == 9500
        assert info_b.port == 9501

    async def test_load_duplicate_raises(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        await manager.load_model("test-deployment", resolved)
        with pytest.raises(ValueError, match="already deployed"):
            await manager.load_model("test-deployment", resolved)

    async def test_load_unknown_backend_raises(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved(backend="nonexistent")
        with pytest.raises(KeyError, match="unknown backend"):
            await manager.load_model("bad-deployment", resolved)


class TestHealthCheck:
    """Background health check tests."""

    async def test_health_transitions_to_running(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        info = await manager.load_model("test-deployment", resolved)
        assert info.status == "loading"
        # Wait for background health poll
        await manager._wait_for_status("test-deployment", "running", timeout=5.0)
        stored = manager.state.get("test-deployment")
        assert stored.status == "running"

    async def test_health_transitions_to_error(
        self, manager: LifecycleManager,
    ) -> None:
        # Set a short health timeout so error transition happens quickly
        manager._health_timeout = 0.05  # 50ms
        # Trigger adapter creation first
        manager._get_adapter("mock")
        adapter = manager._adapters["mock"]
        adapter._healthy = False  # type: ignore[attr-defined]
        resolved = _make_resolved()
        info = await manager.load_model("test-deployment", resolved)
        assert info.status == "loading"
        await manager._wait_for_status("test-deployment", "error", timeout=5.0)
        stored = manager.state.get("test-deployment")
        assert stored.status == "error"

    async def test_health_timeout_keeps_loading(
        self, manager: LifecycleManager,
    ) -> None:
        """Within startup timeout, health failures keep deployment in loading."""
        # Set a short timeout and unhealthy adapter
        manager._health_timeout = 0.05  # 50ms
        manager._get_adapter("mock")
        adapter = manager._adapters["mock"]
        adapter._healthy = False  # type: ignore[attr-defined]

        resolved = _make_resolved()
        info = await manager.load_model("test-deployment", resolved)
        assert info.status == "loading"

        # Before timeout, should still be loading
        await asyncio.sleep(0.02)
        assert manager.get_status("test-deployment") == "loading"

        # After timeout, should transition to error
        await manager._wait_for_status("test-deployment", "error", timeout=5.0)
        assert manager.get_status("test-deployment") == "error"


class TestUnloadModel:
    """unload_model tests."""

    async def test_unload_stops_container(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        await manager.load_model("test-deployment", resolved)
        await manager._wait_for_status("test-deployment", "running", timeout=5.0)
        await manager.unload_model("test-deployment")
        adapter = manager._adapters.get("mock")
        assert len(adapter.stops) == 1

    async def test_unload_releases_port(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        await manager.load_model("test-deployment", resolved)
        port = manager.state.get("test-deployment").port
        await manager.unload_model("test-deployment")
        assert port not in manager.port_allocator.allocated

    async def test_unload_removes_state(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        await manager.load_model("test-deployment", resolved)
        await manager.unload_model("test-deployment")
        with pytest.raises(KeyError):
            manager.state.get("test-deployment")

    async def test_unload_unknown_raises(self, manager: LifecycleManager) -> None:
        with pytest.raises(KeyError, match="not found"):
            await manager.unload_model("nonexistent")


class TestStatus:
    """get_status tests."""

    async def test_get_status_running(
        self, manager: LifecycleManager,
    ) -> None:
        resolved = _make_resolved()
        await manager.load_model("test-deployment", resolved)
        await manager._wait_for_status("test-deployment", "running", timeout=5.0)
        assert manager.get_status("test-deployment") == "running"

    async def test_get_status_unknown_raises(self, manager: LifecycleManager) -> None:
        with pytest.raises(KeyError):
            manager.get_status("nonexistent")
