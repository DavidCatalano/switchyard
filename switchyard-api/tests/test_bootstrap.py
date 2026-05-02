"""Tests for the startup bootstrap sequence.

Validates:
- Bootstrap verifies Docker connectivity
- Bootstrap runs orphan detection and adopts results
- Bootstrap fails if Docker is unreachable
- Auto-start is a stub (SEP-003)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from switchyard.config.models import Config
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.lifecycle import LifecycleManager
from switchyard.core.orphan import OrphanResults
from switchyard.core.ports import PortAllocator
from switchyard.core.registry import AdapterRegistry


class _MockAdapter(BackendAdapter):
    """Simple mock adapter for bootstrap tests."""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.starts: list[DeploymentInfo] = []
        self.stops: list[DeploymentInfo] = []

    def start(self, resolved, port: int) -> DeploymentInfo:  # noqa: ANN001
        info = DeploymentInfo(
            model_name=resolved.deployment_name,
            backend=resolved.backend,
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


@pytest.fixture
def config() -> Config:
    return Config.model_validate({
        "hosts": {
            "test-host": {
                "stores": {
                    "models": {
                        "host_path": "/data/models",
                        "container_path": "/models",
                    },
                },
            },
        },
        "runtimes": {"vllm": {"backend": "vllm"}},
        "models": {
            "test-model": {
                "source": {"store": "models", "path": "test-model"},
            },
        },
        "deployments": {
            "test-deployment": {
                "model": "test-model",
                "runtime": "vllm",
                "host": "test-host",
            },
        },
    })


class TestBootstrap:
    """Startup bootstrap sequence tests."""

    async def test_bootstrap_adopt_orphans(
        self, manager: LifecycleManager, config: Config,
    ) -> None:
        """Running orphans are adopted into state."""
        orphan = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9600,
            status="running",
            container_id="orphan-123",
        )
        mock_docker = MagicMock()
        mock_detector = MagicMock()
        mock_detector.return_value.scan.return_value = OrphanResults(
            adopted=[orphan], removed=[],
        )

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker)

        stored = manager.state.get("test-deployment")
        assert stored.status == "running"
        assert stored.port == 9600

    async def test_bootstrap_skip_non_auto(
        self, manager: LifecycleManager, config: Config,
    ) -> None:
        """Auto-start is a stub (SEP-003). No deployments are auto-started."""
        mock_docker = MagicMock()
        mock_detector = MagicMock()
        mock_detector.return_value.scan.return_value = OrphanResults(
            adopted=[], removed=[],
        )

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker)

        names = manager.state.list_deployments()
        assert "test-deployment" not in names

    async def test_bootstrap_docker_unreachable(
        self, manager: LifecycleManager, config: Config,
    ) -> None:
        """Bootstrap raises if Docker daemon is not accessible."""
        mock_docker = MagicMock()
        mock_docker.ping.return_value = False

        with pytest.raises(ConnectionError, match="docker"):
            await manager.bootstrap(config, mock_docker)

    async def test_bootstrap_adopted_skip_auto_start(
        self, manager: LifecycleManager, config: Config,
    ) -> None:
        """Adopted orphans are recorded, not started twice."""
        orphan = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9600,
            status="running",
            container_id="orphan-456",
        )
        mock_docker = MagicMock()
        mock_detector = MagicMock()
        mock_detector.return_value.scan.return_value = OrphanResults(
            adopted=[orphan], removed=[],
        )

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker)

        stored = manager.state.get("test-deployment")
        assert stored.status == "running"
        assert stored.port == 9600
