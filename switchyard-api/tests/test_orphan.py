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

from switchyard.config.models import (
    LegacyConfig as Config,
)
from switchyard.config.models import (
    LegacyModelConfig as ModelConfig,
)
from switchyard.config.models import (
    VLLMRuntimeConfig,
)
from switchyard.core.orphan import OrphanDetector


def _make_config(*models: tuple[str, str]) -> Config:
    """Build a config with the given (model_name, backend) pairs."""
    model_entries = {}
    for name, backend in models:
        model_entries[name] = ModelConfig(
            backend=backend,
            image=f"{backend}:latest",
            runtime=VLLMRuntimeConfig(repo=f"mock/{name}"),
        )
    return Config(
        global_config={"base_port": 8000, "log_level": "info"},
        models=model_entries,
    )


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
            ("qwen-32b", "vllm"),
            ("llama-70b", "vllm"),
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
        container = _make_container("qwen-32b-vllm-1", state="running", host_port=8001)
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert len(results.adopted) == 1
        assert results.adopted[0].model_name == "qwen-32b"
        assert results.adopted[0].port == 8001
        assert results.adopted[0].status == "running"

    def test_crashed_orphan_removed(self, config: Config) -> None:
        mock_client = MagicMock()
        container = _make_container("qwen-32b-vllm-1", state="exited")
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert results.adopted == []
        assert len(results.removed) == 1
        container.remove.assert_called_once_with(force=True)

    def test_unknown_container_ignored(self, config: Config) -> None:
        """Containers not matching configured models are ignored."""
        mock_client = MagicMock()
        container = _make_container("random-container", state="running")
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert results.adopted == []
        assert results.removed == []

    def test_multiple_orphans_mixed(self, config: Config) -> None:
        mock_client = MagicMock()
        running = _make_container("qwen-32b-vllm-1", state="running", host_port=8001)
        crashed = _make_container("llama-70b-vllm-1", state="dead")
        mock_client.containers.list.return_value = [running, crashed]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert len(results.adopted) == 1
        assert results.adopted[0].model_name == "qwen-32b"
        assert len(results.removed) == 1

    def test_name_pattern_parsing(self, config: Config) -> None:
        """Container names like model-backend-N are parsed correctly."""
        mock_client = MagicMock()
        container = _make_container(
            "my-model-koboldcpp-3",
            state="running",
            host_port=9001,
        )
        config_with_kobold = _make_config(("my-model", "koboldcpp"))
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config_with_kobold)
        results = detector.scan()
        assert len(results.adopted) == 1
        assert results.adopted[0].model_name == "my-model"
        assert results.adopted[0].backend == "koboldcpp"

    def test_orphan_port_8000_preferred(self, config: Config) -> None:
        """Port extraction prefers 8000/tcp (vLLM default internal port)."""
        mock_client = MagicMock()
        container = _make_container(
            "qwen-32b-vllm-1",
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
            "qwen-32b-vllm-1",
            state="running",
            host_port=8020,
            internal_port=80,
        )
        mock_client.containers.list.return_value = [container]
        detector = OrphanDetector(mock_client, config)
        results = detector.scan()
        assert len(results.adopted) == 1
        assert results.adopted[0].port == 8020
