"""Tests for the LifecycleManager.

Validates:
- load_model allocates port, calls adapter.start, records "loading" status
- load_model returns immediately (async) with DeploymentInfo
- Background health check transitions loading -> running on success
- Background health check transitions loading -> error on failure
- unload_model stops adapter, releases port, removes state
- Cannot load already-running model
- Cannot unload unknown model
- Health check on unknown model raises
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from switchyard.config.models import ModelConfig, VLLMRuntimeConfig
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.lifecycle import LifecycleManager
from switchyard.core.ports import PortAllocator
from switchyard.core.registry import AdapterRegistry

# --- Fixtures ---

@pytest.fixture
def model_config() -> ModelConfig:
    return ModelConfig(
        backend="mock",
        image="mock:latest",
        runtime=VLLMRuntimeConfig(repo="mock/model"),
    )


@pytest.fixture
def adapter_class() -> type[BackendAdapter]:
    class MockAdapter(BackendAdapter):
        def __init__(self) -> None:
            self.starts: list[tuple[ModelConfig, int]] = []
            self.stops: list[DeploymentInfo] = []
            self._healthy = True

        def start(self, model_config: ModelConfig, port: int) -> DeploymentInfo:
            self.starts.append((model_config, port))
            return DeploymentInfo(
                model_name="test-model",
                backend="mock",
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
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        info = await manager.load_model("test-model", model_config)
        assert info.status == "loading"

    async def test_load_records_in_state(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        await manager.load_model("test-model", model_config)
        stored = manager.state.get("test-model")
        assert stored.status == "loading"
        assert stored.backend == "mock"

    async def test_load_allocates_port(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        await manager.load_model("test-model", model_config)
        assert 9500 in manager.port_allocator.allocated

    async def test_load_calls_adapter_start(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        await manager.load_model("test-model", model_config)
        adapter = manager._adapters.get("mock")
        assert len(adapter.starts) == 1
        assert adapter.starts[0][1] == 9500  # port

    async def test_load_multiple_sequential_ports(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        cfg1 = ModelConfig(
            backend="mock", image="mock:latest",
            runtime=VLLMRuntimeConfig(repo="mock/m1"),
        )
        cfg2 = ModelConfig(
            backend="mock", image="mock:latest",
            runtime=VLLMRuntimeConfig(repo="mock/m2"),
        )
        await manager.load_model("model-a", cfg1)
        await manager.load_model("model-b", cfg2)
        info_a = manager.state.get("model-a")
        info_b = manager.state.get("model-b")
        assert info_a.port == 9500
        assert info_b.port == 9501

    async def test_load_duplicate_raises(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        await manager.load_model("test-model", model_config)
        with pytest.raises(ValueError, match="already deployed"):
            await manager.load_model("test-model", model_config)

    async def test_load_unknown_backend_raises(self, manager: LifecycleManager) -> None:
        bad_config = ModelConfig(
            backend="nonexistent",
            image="bad:latest",
            runtime=VLLMRuntimeConfig(repo="bad/model"),
        )
        with pytest.raises(KeyError, match="unknown backend"):
            await manager.load_model("bad-model", bad_config)


class TestHealthCheck:
    """Background health check tests."""

    async def test_health_transitions_to_running(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        info = await manager.load_model("test-model", model_config)
        assert info.status == "loading"
        # Wait for background health poll
        await manager._wait_for_status("test-model", "running", timeout=5.0)
        stored = manager.state.get("test-model")
        assert stored.status == "running"

    async def test_health_transitions_to_error(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        # Trigger adapter creation first
        manager._get_adapter("mock")
        adapter = manager._adapters["mock"]
        adapter._healthy = False  # type: ignore[attr-defined]
        info = await manager.load_model("test-model", model_config)
        assert info.status == "loading"
        await manager._wait_for_status("test-model", "error", timeout=5.0)
        stored = manager.state.get("test-model")
        assert stored.status == "error"


class TestUnloadModel:
    """unload_model tests."""

    async def test_unload_stops_container(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        await manager.load_model("test-model", model_config)
        await manager._wait_for_status("test-model", "running", timeout=5.0)
        await manager.unload_model("test-model")
        adapter = manager._adapters.get("mock")
        assert len(adapter.stops) == 1

    async def test_unload_releases_port(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        await manager.load_model("test-model", model_config)
        port = manager.state.get("test-model").port
        await manager.unload_model("test-model")
        assert port not in manager.port_allocator.allocated

    async def test_unload_removes_state(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        await manager.load_model("test-model", model_config)
        await manager.unload_model("test-model")
        with pytest.raises(KeyError):
            manager.state.get("test-model")

    async def test_unload_unknown_raises(self, manager: LifecycleManager) -> None:
        with pytest.raises(KeyError, match="not found"):
            await manager.unload_model("nonexistent")


class TestStatus:
    """get_status tests."""

    async def test_get_status_running(
        self, manager: LifecycleManager, model_config: ModelConfig,
    ) -> None:
        await manager.load_model("test-model", model_config)
        await manager._wait_for_status("test-model", "running", timeout=5.0)
        assert manager.get_status("test-model") == "running"

    async def test_get_status_unknown_raises(self, manager: LifecycleManager) -> None:
        with pytest.raises(KeyError):
            manager.get_status("nonexistent")
