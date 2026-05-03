"""Tests for orphan detection.

Validates:
- Scanning for containers matching naming convention
- Adopting running orphans into state
- Removing crashed orphans
- Skipping containers that are already tracked
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from switchyard.config.models import Config
from switchyard.core.orphan import OrphanDetector


def _make_config(*deployments: tuple[str, str, str]) -> Config:
    """Build a Config with the given (deployment_name, model_name, backend) pairs.

    Each deployment references a model/runtime/host so validation passes.
    """
    # Build minimal valid config with named entities
    host_cfg = {
        "test-host": {
            "stores": {
                "models": {
                    "host_path": "/data/models",
                    "container_path": "/models",
                },
            },
        },
    }
    runtime_cfg = {
        "vllm": {"backend": "vllm"},
        "koboldcpp": {"backend": "koboldcpp"},
    }
    model_cfg: dict = {}
    deployment_cfg: dict = {}
    for dep_name, model_name, backend in deployments:
        model_cfg[model_name] = {
            "source": {"store": "models", "path": model_name},
        }
        deployment_cfg[dep_name] = {
            "model": model_name,
            "runtime": "vllm" if backend == "vllm" else "koboldcpp",
            "host": "test-host",
        }

    return Config.model_validate({
        "hosts": host_cfg,
        "runtimes": runtime_cfg,
        "models": model_cfg,
        "deployments": deployment_cfg,
    })


def _make_container(
    name: str,
    state: str = "running",
    host_port: int | None = None,
    internal_port: int = 8000,
) -> MagicMock:
    """Create a mock Docker container."""
    container = MagicMock()
    container.name = name
    container.attrs = {
        "State": {"Status": state},
        "NetworkSettings": {"Ports": {}},
    }
    container.status = state
    if host_port is not None:
        port_key = f"{internal_port}/tcp"
        container.attrs["NetworkSettings"]["Ports"][port_key] = [
            {"HostIp": "0.0.0.0", "HostPort": host_port},
        ]
    else:
        port_key = f"{internal_port}/tcp"
        container.attrs["NetworkSettings"]["Ports"][port_key] = None
    return container


class TestOrphanDetector:
    """Orphan detector tests."""

    @pytest.fixture
    def config(self) -> Config:
        return _make_config(
            ("qwen-32b-vllm-1", "qwen-32b", "vllm"),
            ("llama-70b-vllm-1", "llama-70b", "vllm"),
        )

    def test_no_orphans_found(self, config: Config) -> None:
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert results.adopted == []
        assert results.removed == []

    def test_running_orphan_adopted(self, config: Config) -> None:
        mock_client = MagicMock()
        container = _make_container(
            "qwen-32b-vllm-1-vllm-1",
            state="running",
            host_port=8001,
        )
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert len(results.adopted) == 1
        assert results.adopted[0].model_name == "qwen-32b-vllm-1"
        assert results.adopted[0].port == 8001
        assert results.adopted[0].status == "running"

    def test_crashed_orphan_removed(self, config: Config) -> None:
        mock_client = MagicMock()
        container = _make_container(
            "qwen-32b-vllm-1-vllm-1", state="exited",
        )
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert results.adopted == []
        assert len(results.removed) == 1
        container.remove.assert_called_once_with(force=True)

    def test_unknown_container_ignored(self, config: Config) -> None:
        """Containers not matching configured deployments are ignored."""
        mock_client = MagicMock()
        container = _make_container("random-container", state="running")
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert results.adopted == []
        assert results.removed == []

    def test_multiple_orphans_mixed(self, config: Config) -> None:
        mock_client = MagicMock()
        running = _make_container(
            "qwen-32b-vllm-1-vllm-1",
            state="running",
            host_port=8001,
        )
        crashed = _make_container(
            "llama-70b-vllm-1-vllm-1",
            state="dead",
        )
        mock_client.containers.list.return_value = [running, crashed]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert len(results.adopted) == 1
        assert results.adopted[0].model_name == "qwen-32b-vllm-1"
        assert len(results.removed) == 1

    def test_name_pattern_parsing(self) -> None:
        """Container names like model-backend-N are parsed correctly."""
        config = _make_config(
            ("my-model-koboldcpp-3", "my-model", "koboldcpp"),
        )
        mock_client = MagicMock()
        container = _make_container(
            "my-model-koboldcpp-3-koboldcpp-3",
            state="running",
            host_port=9001,
        )
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert len(results.adopted) == 1
        assert results.adopted[0].model_name == "my-model-koboldcpp-3"
        assert results.adopted[0].backend == "koboldcpp"

    def test_orphan_port_8000_preferred(self, config: Config) -> None:
        """Port extraction prefers 8000/tcp (vLLM default internal port)."""
        mock_client = MagicMock()
        container = _make_container(
            "qwen-32b-vllm-1-vllm-1",
            state="running",
            host_port=8010,
            internal_port=8000,
        )
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert len(results.adopted) == 1
        assert results.adopted[0].port == 8010

    def test_orphan_port_80_fallback(self, config: Config) -> None:
        """Port extraction falls back to 80/tcp if 8000/tcp not present."""
        mock_client = MagicMock()
        container = _make_container(
            "qwen-32b-vllm-1-vllm-1",
            state="running",
            host_port=8020,
            internal_port=80,
        )
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert len(results.adopted) == 1
        assert results.adopted[0].port == 8020
