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

from switchyard.config.models import Config, ResolvedDeployment
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
    # Also register "vllm" so bootstrap health tasks can run
    registry.register("vllm", _MockAdapter)
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
        """Running orphans are adopted as loading with health task."""
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

        def mock_resolve(name: str) -> ResolvedDeployment:
            return ResolvedDeployment(
                deployment_name=name,
                model_name="test-model",
                runtime_name="vllm",
                backend="vllm",
                host_name="test-host",
                backend_host="localhost",
                backend_scheme="http",
                port_range=[9500, 9600],
                image="vllm:latest",
                internal_port=8000,
                model_host_path="/data/models/test",
                model_container_path="/models/test",
                accelerator_ids=[],
                docker_host=None,
                docker_network="model-runtime",
                runtime_args={"model": "/models/test"},
                container_environment={},
                container_options={},
                store_mounts={},
                model_defaults=None,
            )

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker, resolve=mock_resolve)

        stored = manager.state.get("test-deployment")
        assert stored.status == "loading"
        assert stored.port == 9600
        assert stored.container_id == "orphan-123"
        # Metadata populated from resolved config
        assert stored.metadata["backend_host"] == "localhost"
        assert stored.metadata["backend_scheme"] == "http"
        # Health task started
        assert "test-deployment" in manager._health_tasks

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
        """Adopted orphans are recorded as loading, not started twice."""
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

        def mock_resolve(name: str) -> ResolvedDeployment:
            return ResolvedDeployment(
                deployment_name=name,
                model_name="test-model",
                runtime_name="vllm",
                backend="vllm",
                host_name="test-host",
                backend_host="localhost",
                backend_scheme="http",
                port_range=[9500, 9600],
                image="vllm:latest",
                internal_port=8000,
                model_host_path="/data/models/test",
                model_container_path="/models/test",
                accelerator_ids=[],
                docker_host=None,
                docker_network="model-runtime",
                runtime_args={"model": "/models/test"},
                container_environment={},
                container_options={},
                store_mounts={},
                model_defaults=None,
            )

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker, resolve=mock_resolve)

        stored = manager.state.get("test-deployment")
        assert stored.status == "loading"
        assert stored.port == 9600
        assert "test-deployment" in manager._health_tasks

    async def test_bootstrap_skips_orphan_on_config_error(
        self, manager: LifecycleManager, config: Config,
    ) -> None:
        """Orphan whose config lookup fails is skipped, others proceed."""
        orphan1 = DeploymentInfo(
            model_name="test-deployment",
            backend="vllm",
            port=9600,
            status="running",
            container_id="orphan-123",
        )
        orphan2 = DeploymentInfo(
            model_name="unknown-deployment",
            backend="vllm",
            port=9601,
            status="running",
            container_id="orphan-789",
        )
        mock_docker = MagicMock()
        mock_detector = MagicMock()
        mock_detector.return_value.scan.return_value = OrphanResults(
            adopted=[orphan1, orphan2], removed=[],
        )

        def mock_resolve(name: str) -> ResolvedDeployment:
            if name == "unknown-deployment":
                raise ValueError("deployment not found")
            return ResolvedDeployment(
                deployment_name=name,
                model_name="test-model",
                runtime_name="vllm",
                backend="vllm",
                host_name="test-host",
                backend_host="localhost",
                backend_scheme="http",
                port_range=[9500, 9600],
                image="vllm:latest",
                internal_port=8000,
                model_host_path="/data/models/test",
                model_container_path="/models/test",
                accelerator_ids=[],
                docker_host=None,
                docker_network="model-runtime",
                runtime_args={"model": "/models/test"},
                container_environment={},
                container_options={},
                store_mounts={},
                model_defaults=None,
            )

        with patch(
            "switchyard.core.lifecycle.OrphanDetector", mock_detector,
        ):
            await manager.bootstrap(config, mock_docker, resolve=mock_resolve)

        # Good orphan adopted
        stored = manager.state.get("test-deployment")
        assert stored.status == "loading"
        assert stored.port == 9600
        # Unknown orphan skipped
        with pytest.raises(KeyError):
            manager.state.get("unknown-deployment")
        assert 9601 not in manager.port_allocator.allocated

    async def test_bootstrap_skips_orphan_without_resolver(
        self, manager: LifecycleManager, config: Config,
    ) -> None:
        """Orphans are skipped when no resolver is provided."""
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

        # Orphan skipped without resolver
        with pytest.raises(KeyError):
            manager.state.get("test-deployment")
        assert 9600 not in manager.port_allocator.allocated
