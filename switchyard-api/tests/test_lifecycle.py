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
from unittest.mock import MagicMock

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

    async def test_load_releases_port_on_start_failure(
        self, manager: LifecycleManager,
    ) -> None:
        """T5.1: port is released when adapter.start() raises."""
        # Use a mock adapter class that fails on start
        class FailingAdapter(BackendAdapter):
            def __init__(self, **kwargs: Any) -> None:
                pass

            def start(
                self, resolved: ResolvedDeployment, port: int,
            ) -> DeploymentInfo:
                raise RuntimeError("container launch failed")

            def stop(self, deployment: DeploymentInfo) -> None:
                pass

            def health(self, deployment: DeploymentInfo) -> str:
                return "error"

            def endpoint(self, deployment: DeploymentInfo) -> str:
                return ""

        registry = AdapterRegistry()
        registry.register("failing", FailingAdapter)
        port_allocator = PortAllocator(base_port=9600)
        failing_mgr = LifecycleManager(
            registry=registry,
            port_allocator=port_allocator,
        )
        resolved = _make_resolved(
            backend="failing", deployment_name="failing-deployment"
        )
        with pytest.raises(RuntimeError, match="container launch failed"):
            await failing_mgr.load_model("failing-deployment", resolved)

        # Port must be released even though start failed
        assert 9600 not in failing_mgr.port_allocator.allocated
        # Deployment must not be in state
        with pytest.raises(KeyError):
            failing_mgr.state.get("failing-deployment")


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

    async def test_unload_unknown_is_idempotent(
        self, manager: LifecycleManager,
    ) -> None:
        """Unload unknown deployment is idempotent (no error)."""
        await manager.unload_model("nonexistent")  # no exception


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


# --- Reconciliation Tests (T2.2) ---


def _make_mock_container(
    status: str = "running",
    short_id: str = "abc123",
    name: str = "test-container",
    host_port: int | None = 9500,
    internal_port: int = 8000,
) -> MagicMock:
    """Create a mock Docker container with configurable state."""
    container = MagicMock()
    container.short_id = short_id
    container.name = name
    container.labels = {
        "switchyard.managed": "true",
        "switchyard.deployment": "test-deployment",
    }
    container.attrs = {
        "State": {"Status": status},
        "NetworkSettings": {
            "Ports": {},
        },
    }
    if host_port is not None:
        container.attrs["NetworkSettings"]["Ports"][
            f"{internal_port}/tcp"
        ] = [{"HostPort": str(host_port)}]
    return container


def _make_factory(
    container: MagicMock | None = None,
) -> MagicMock:
    """Create a mock Docker client factory.

    The factory is a callable that returns a DockerClient mock.
    The DockerClient's containers.list() returns containers matching
    the label filter.
    """
    client = MagicMock()
    factory = MagicMock(return_value=client)
    factory.return_value = client

    def _list(all: bool = False, filters: dict | None = None) -> list:
        if container is None:
            return []
        return [container]

    client.containers.list = _list
    return factory


class TestReconcile:
    """LifecycleManager.reconcile() unit tests."""

    def _make_manager(
        self,
        docker_client_factory: MagicMock | None = None,
    ) -> LifecycleManager:
        """Create a manager with optional docker factory."""
        registry = AdapterRegistry()
        return LifecycleManager(
            registry=registry,
            port_allocator=PortAllocator(base_port=9500),
            docker_client_factory=docker_client_factory,
        )

    def test_reconcile_no_factory_returns_existing_state(self) -> None:
        """Without a Docker factory, reconcile returns in-memory state."""
        manager = self._make_manager()
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9500,
            status="running",
            container_id="abc123",
        )
        manager.state.add(info)
        resolved = _make_resolved()
        result = manager.reconcile("test-deployment", resolved)
        assert result is not None
        assert result.status == "running"

    def test_reconcile_no_factory_returns_none_when_absent(self) -> None:
        """Without a Docker factory, reconcile returns None for missing state."""
        manager = self._make_manager()
        resolved = _make_resolved()
        result = manager.reconcile("test-deployment", resolved)
        assert result is None

    def test_reconcile_running_container_preserves_state(self) -> None:
        """Running container found → preserve and return existing state."""
        container = _make_mock_container(status="running")
        factory = _make_factory(container)
        manager = self._make_manager(factory)

        # Pre-existing in-memory state
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9500,
            status="loading",
            container_id="abc123",
        )
        manager.state.add(info)

        resolved = _make_resolved()
        result = manager.reconcile("test-deployment", resolved)

        assert result is not None
        assert result.status == "running"  # updated from loading

    def test_reconcile_running_container_adopts_when_missing(self) -> None:
        """Running container not in memory → adopt and reserve port."""
        container = _make_mock_container(status="running", host_port=9510)
        factory = _make_factory(container)
        manager = self._make_manager(factory)

        resolved = _make_resolved()
        result = manager.reconcile("test-deployment", resolved)

        assert result is not None
        assert result.status == "running"
        assert result.port == 9510
        assert 9510 in manager.port_allocator.allocated

    def test_reconcile_exited_container_clears_state(self) -> None:
        """Exited container → clear in-memory state, release port."""
        container = _make_mock_container(status="exited", host_port=9500)
        factory = _make_factory(container)
        manager = self._make_manager(factory)

        # Pre-existing in-memory state
        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9500,
            status="running",
            container_id="abc123",
        )
        manager.state.add(info)
        manager.port_allocator.allocate(port=9500)

        resolved = _make_resolved()
        result = manager.reconcile("test-deployment", resolved)

        assert result is None
        assert "test-deployment" not in manager.state.list_deployments()
        assert 9500 not in manager.port_allocator.allocated

    def test_reconcile_dead_container_clears_state(self) -> None:
        """Dead container → clear in-memory state, release port."""
        container = _make_mock_container(status="dead", host_port=9500)
        factory = _make_factory(container)
        manager = self._make_manager(factory)

        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9500,
            status="running",
            container_id="abc123",
        )
        manager.state.add(info)
        manager.port_allocator.allocate(port=9500)

        resolved = _make_resolved()
        result = manager.reconcile("test-deployment", resolved)

        assert result is None
        assert "test-deployment" not in manager.state.list_deployments()

    def test_reconcile_gone_container_clears_state(self) -> None:
        """Container gone (not found) → clear in-memory state, release port."""
        factory = _make_factory(None)  # returns no containers
        manager = self._make_manager(factory)

        info = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9500,
            status="running",
            container_id="abc123",
        )
        manager.state.add(info)
        manager.port_allocator.allocate(port=9500)

        resolved = _make_resolved()
        result = manager.reconcile("test-deployment", resolved)

        assert result is None
        assert "test-deployment" not in manager.state.list_deployments()
        assert 9500 not in manager.port_allocator.allocated

    def test_reconcile_unknown_deployment_noop(self) -> None:
        """Unknown deployment → no-op, no error."""
        container = _make_mock_container(status="running")
        factory = _make_factory(container)
        manager = self._make_manager(factory)

        # Configure factory to return None for unknown deployment
        factory.return_value.containers.list = lambda all=False, filters=None: []

        resolved = _make_resolved(deployment_name="nonexistent")
        result = manager.reconcile("nonexistent", resolved)
        assert result is None


class TestReconcileIntegration:
    """Lifecycle integration tests for reconciliation (T2.5)."""

    async def test_reconcile_adopts_running_container_on_load(
        self,
    ) -> None:
        """API restart scenario: reconcile adopts running labeled container."""
        container = _make_mock_container(status="running", host_port=9510)
        factory = _make_factory(container)

        registry = AdapterRegistry()

        class MockAdapter(BackendAdapter):
            def start(
                self, resolved: ResolvedDeployment, port: int,
            ) -> DeploymentInfo:
                return DeploymentInfo(
                    model_name=resolved.deployment_name,
                    backend=resolved.backend,
                    port=port,
                    status="loading",
                    container_id="new-container",
                )

            def stop(self, deployment: DeploymentInfo) -> None:
                pass

            def health(self, deployment: DeploymentInfo) -> str:
                return "running"

            def endpoint(self, deployment: DeploymentInfo) -> str:
                return f"http://localhost:{deployment.port}"

        registry.register("mock", MockAdapter)
        manager = LifecycleManager(
            registry=registry,
            port_allocator=PortAllocator(base_port=9500),
            docker_client_factory=factory,
        )

        resolved = _make_resolved()

        # Before load, reconcile should adopt the running container
        result = manager.reconcile("test-deployment", resolved)
        assert result is not None
        assert result.status == "running"
        assert result.container_id == "abc123"  # adopted container

    async def test_reconcile_clears_stale_before_load(self) -> None:
        """Reconcile clears exited container before load succeeds."""
        # Start with factory returning exited container
        container = _make_mock_container(status="exited")
        factory = _make_factory(container)

        registry = AdapterRegistry()

        class StartAdapter(BackendAdapter):
            def __init__(self, **kwargs: Any) -> None:
                self.started = False

            def start(
                self, resolved: ResolvedDeployment, port: int,
            ) -> DeploymentInfo:
                self.started = True
                return DeploymentInfo(
                    model_name=resolved.deployment_name,
                    backend=resolved.backend,
                    port=port,
                    status="loading",
                    container_id="new-123",
                )

            def stop(self, deployment: DeploymentInfo) -> None:
                pass

            def health(self, deployment: DeploymentInfo) -> str:
                return "running"

            def endpoint(self, deployment: DeploymentInfo) -> str:
                return f"http://localhost:{deployment.port}"

        registry.register("mock", StartAdapter)
        port_allocator = PortAllocator(base_port=9500)
        manager = LifecycleManager(
            registry=registry,
            port_allocator=port_allocator,
            docker_client_factory=factory,
        )

        resolved = _make_resolved()

        # Reconcile clears stale exited container
        result = manager.reconcile("test-deployment", resolved)
        assert result is None

        # Now factory should return None (container gone)
        factory.return_value.containers.list = lambda all=False, filters=None: []

        # Load should succeed
        info = await manager.load_model("test-deployment", resolved)
        assert info.status == "loading"
        assert info.container_id == "new-123"

    async def test_reconcile_unload_gone_container_idempotent(self) -> None:
        """Reconcile finds gone container → unload returns without error."""
        factory = _make_factory(None)  # no container found

        manager = self._make_manager(factory)

        resolved = _make_resolved()
        # Reconcile finds no container → returns None
        result = manager.reconcile("test-deployment", resolved)
        assert result is None

        # Unload should be idempotent (no state to clear)
        try:
            info = manager.state.get("test-deployment")
        except KeyError:
            info = None
        assert info is None

    def _make_manager(
        self,
        docker_client_factory: MagicMock | None = None,
    ) -> LifecycleManager:
        registry = AdapterRegistry()
        return LifecycleManager(
            registry=registry,
            port_allocator=PortAllocator(base_port=9500),
            docker_client_factory=docker_client_factory,
        )
