"""Tests for the startup bootstrap sequence.

Validates:
- Bootstrap verifies Docker connectivity
- Bootstrap runs orphan detection and adopts results
- Bootstrap auto-starts models with auto_start=true
- Bootstrap skips models with auto_start=false
- Bootstrap fails if Docker is unreachable
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from switchyard.config.models import (
    Config,
    ControlConfig,
    ModelConfig,
    VLLMRuntimeConfig,
)
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.lifecycle import LifecycleManager
from switchyard.core.orphan import OrphanResults
from switchyard.core.ports import PortAllocator
from switchyard.core.registry import AdapterRegistry


def _make_config(*models: tuple[str, str, bool]) -> Config:
    """Build a Config with the given (model_name, backend, auto_start) pairs."""
    model_entries = {}
    for name, backend, auto in models:
        model_entries[name] = ModelConfig(
            backend=backend,
            image=f"{backend}:latest",
            runtime=VLLMRuntimeConfig(repo=f"mock/{name}"),
            control=ControlConfig(auto_start=auto),
        )
    return Config(
        global_config={"base_port": 8000, "log_level": "info"},
        models=model_entries,
    )


class _MockAdapter(BackendAdapter):
    """Simple mock adapter for bootstrap tests."""

    def __init__(self) -> None:
        self.starts: list[DeploymentInfo] = []
        self.stops: list[DeploymentInfo] = []

    def start(self, model_config: ModelConfig, port: int) -> DeploymentInfo:
        info = DeploymentInfo(
            model_name="test",
            backend=model_config.backend,
            port=port,
            status="loading",
            container_id=f"mock-{port}",
        )
        self.starts.append(info)
        return info

    def stop(self, deployment: DeploymentInfo) -> None:
        self.stops.append(deployment)

    def health(self, deployment: DeploymentInfo) -> str:
        return "running"

    def endpoint(self, deployment: DeploymentInfo) -> str:
        return f"http://localhost:{deployment.port}"


@pytest_asyncio.fixture
async def manager() -> LifecycleManager:
    registry = AdapterRegistry()
    registry.register("mock", _MockAdapter)
    return LifecycleManager(
        registry=registry,
        port_allocator=PortAllocator(base_port=9500),
    )


class TestBootstrap:
    """Startup bootstrap sequence tests."""

    async def test_bootstrap_adopt_orphans(
        self, manager: LifecycleManager,
    ) -> None:
        """Running orphans are adopted into state."""
        orphan = DeploymentInfo(
            model_name="orphan-model",
            backend="mock",
            port=9600,
            status="running",
            container_id="orphan-123",
        )
        mock_docker = MagicMock()
        mock_detector = MagicMock()
        mock_detector.return_value.scan.return_value = OrphanResults(
            adopted=[orphan], removed=[],
        )

        config = _make_config(("orphan-model", "mock", False))

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker)

        stored = manager.state.get("orphan-model")
        assert stored.status == "running"
        assert stored.port == 9600

    async def test_bootstrap_auto_start_models(
        self, manager: LifecycleManager,
    ) -> None:
        """Models with auto_start=true are loaded on bootstrap."""
        mock_docker = MagicMock()
        mock_detector = MagicMock()
        mock_detector.return_value.scan.return_value = OrphanResults(
            adopted=[], removed=[],
        )

        config = _make_config(("auto-model", "mock", True))

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker)

        stored = manager.state.get("auto-model")
        assert stored.status == "loading"

    async def test_bootstrap_skip_non_auto(
        self, manager: LifecycleManager,
    ) -> None:
        """Models with auto_start=false are not started."""
        mock_docker = MagicMock()
        mock_detector = MagicMock()
        mock_detector.return_value.scan.return_value = OrphanResults(
            adopted=[], removed=[],
        )

        config = _make_config(("manual-model", "mock", False))

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker)

        names = manager.state.list_deployments()
        assert "manual-model" not in names

    async def test_bootstrap_docker_unreachable(
        self, manager: LifecycleManager,
    ) -> None:
        """Bootstrap raises if Docker daemon is not accessible."""
        mock_docker = MagicMock()
        mock_docker.ping.return_value = False
        config = _make_config()

        with pytest.raises(ConnectionError, match="docker"):
            await manager.bootstrap(config, mock_docker)

    async def test_bootstrap_adopted_skip_auto_start(
        self, manager: LifecycleManager,
    ) -> None:
        """Adopted orphans are not auto-started again."""
        orphan = DeploymentInfo(
            model_name="existing-model",
            backend="mock",
            port=9600,
            status="running",
            container_id="orphan-456",
        )
        mock_docker = MagicMock()
        mock_detector = MagicMock()
        mock_detector.return_value.scan.return_value = OrphanResults(
            adopted=[orphan], removed=[],
        )

        config = _make_config(("existing-model", "mock", True))

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker)

        # Should be adopted once, not started twice
        stored = manager.state.get("existing-model")
        assert stored.status == "running"
        assert stored.port == 9600
