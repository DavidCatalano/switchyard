"""Tests for the BackendAdapter protocol and DeploymentInfo dataclass.

Validates:
- DeploymentInfo carries required fields (model name, backend, port, status,
  container_id, started_at)
- DeploymentInfo validates status values
- BackendAdapter is abstract and enforces method implementation
- Concrete implementations can satisfy the contract with ResolvedDeployment
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from switchyard.config.models import ResolvedDeployment
from switchyard.core.adapter import BackendAdapter, DeploymentInfo


def _make_resolved(
    overrides: dict[str, Any] | None = None,
) -> ResolvedDeployment:
    """Create a minimal ResolvedDeployment for tests."""
    defaults: dict[str, Any] = {
        "deployment_name": "test-deployment",
        "model_name": "test-model",
        "runtime_name": "vllm",
        "backend": "vllm",
        "host_name": "test-host",
        "backend_host": "localhost",
        "backend_scheme": "http",
        "port_range": [8000, 9000],
        "image": "vllm/vllm-openai:latest",
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


class TestDeploymentInfo:
    """DeploymentInfo dataclass tests."""

    def test_create_with_all_fields(self) -> None:
        info = DeploymentInfo(
            model_name="qwen-32b",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
            started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        assert info.model_name == "qwen-32b"
        assert info.backend == "vllm"
        assert info.port == 8001
        assert info.status == "running"
        assert info.container_id == "abc123"
        assert info.started_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_default_started_at(self) -> None:
        info = DeploymentInfo(
            model_name="m",
            backend="vllm",
            port=8000,
            status="running",
            container_id="c",
        )
        assert info.started_at.tzinfo is not None

    def test_default_metadata_is_empty_dict(self) -> None:
        info = DeploymentInfo(
            model_name="m",
            backend="vllm",
            port=8000,
            status="running",
            container_id="c",
        )
        assert info.metadata == {}

    def test_metadata_can_be_set(self) -> None:
        info = DeploymentInfo(
            model_name="m",
            backend="vllm",
            port=8000,
            status="running",
            container_id="c",
            metadata={"key": "value"},
        )
        assert info.metadata == {"key": "value"}

    def test_is_frozen(self) -> None:
        info = DeploymentInfo(
            model_name="m",
            backend="vllm",
            port=8000,
            status="running",
            container_id="c",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            info.status = "stopped"  # type: ignore[frozen-instantiation-align]

    def test_valid_statuses(self) -> None:
        """All four spec statuses are accepted."""
        for status in ("running", "stopped", "loading", "error"):
            info = DeploymentInfo(
                model_name="m",
                backend="vllm",
                port=8000,
                status=status,  # type: ignore[arg-type]
                container_id="x",
            )
            assert info.status == status

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid status"):
            DeploymentInfo(
                model_name="m",
                backend="vllm",
                port=8000,
                status="crashed",  # type: ignore[arg-type]
                container_id="x",
            )


class TestBackendAdapter:
    """BackendAdapter abstract class tests."""

    def test_cannot_instantiate_directly(self) -> None:
        """BackendAdapter is abstract — direct instantiation fails."""
        with pytest.raises(TypeError):
            BackendAdapter()  # type: ignore[misc]

    def test_missing_method_raises(self) -> None:
        """Subclass that doesn't implement all methods cannot be instantiated."""

        class PartialAdapter(BackendAdapter):
            def start(
                self, resolved: ResolvedDeployment, port: int,
            ) -> DeploymentInfo:
                return DeploymentInfo(
                    model_name="m",
                    backend="b",
                    port=port,
                    status="running",
                    container_id="c",
                )

        with pytest.raises(TypeError):
            PartialAdapter()

    def test_complete_implementation_instantiates(self) -> None:
        """A class implementing all abstract methods can be instantiated."""

        class MockAdapter(BackendAdapter):
            def start(
                self, resolved: ResolvedDeployment, port: int,
            ) -> DeploymentInfo:
                return DeploymentInfo(
                    model_name="test",
                    backend="mock",
                    port=port,
                    status="running",
                    container_id="mock-123",
                )

            def stop(self, deployment: DeploymentInfo) -> None:
                pass

            def health(self, deployment: DeploymentInfo) -> str:
                return "running"

            def endpoint(self, deployment: DeploymentInfo) -> str:
                return f"http://localhost:{deployment.port}"

        adapter = MockAdapter()
        assert adapter is not None

    def test_complete_implementation_methods(self) -> None:
        """Verify a concrete adapter's methods return expected types."""

        class MockAdapter(BackendAdapter):
            def start(
                self, resolved: ResolvedDeployment, port: int,
            ) -> DeploymentInfo:
                return DeploymentInfo(
                    model_name=resolved.deployment_name,
                    backend=resolved.backend,
                    port=port,
                    status="running",
                    container_id="mock-123",
                )

            def stop(self, deployment: DeploymentInfo) -> None:
                pass

            def health(self, deployment: DeploymentInfo) -> str:
                return "running"

            def endpoint(self, deployment: DeploymentInfo) -> str:
                return f"http://localhost:{deployment.port}"

        adapter = MockAdapter()

        # start returns DeploymentInfo
        resolved = _make_resolved()
        info = adapter.start(resolved, 8001)
        assert isinstance(info, DeploymentInfo)
        assert info.port == 8001

        # health returns str
        status = adapter.health(info)
        assert isinstance(status, str)

        # endpoint returns str
        url = adapter.endpoint(info)
        assert isinstance(url, str)
        assert "8001" in url

        # stop returns None
        result = adapter.stop(info)
        assert result is None
