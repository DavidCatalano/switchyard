"""Tests for the adapter registry.

Validates:
- Registering an adapter by backend name
- Looking up a registered adapter class
- Error on unknown backend
- Factory instantiation with parameters
- Duplicate registration behavior
"""

from __future__ import annotations

import pytest

from switchyard.config.models import (
    LegacyModelConfig as ModelConfig,
)
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.registry import AdapterRegistry


class _TestAdapter(BackendAdapter):
    """Minimal concrete adapter for tests."""

    def __init__(self, tag: str = "") -> None:
        self.tag = tag
        self._started: list[str] = []

    def start(self, model_config: ModelConfig, port: int) -> DeploymentInfo:
        self._started.append(model_config.backend)
        return DeploymentInfo(
            model_name="test",
            backend=model_config.backend,
            port=port,
            status="running",
            container_id=f"test-{self.tag}",
        )

    def stop(self, deployment: DeploymentInfo) -> None:
        pass

    def health(self, deployment: DeploymentInfo) -> str:
        return "running"

    def endpoint(self, deployment: DeploymentInfo) -> str:
        return f"http://localhost:{deployment.port}"


class _AnotherAdapter(BackendAdapter):
    """Second adapter class for testing multiple registrations."""

    def start(self, model_config: ModelConfig, port: int) -> DeploymentInfo:
        return DeploymentInfo(
            model_name="test",
            backend=model_config.backend,
            port=port,
            status="running",
            container_id="another-123",
        )

    def stop(self, deployment: DeploymentInfo) -> None:
        pass

    def health(self, deployment: DeploymentInfo) -> str:
        return "running"

    def endpoint(self, deployment: DeploymentInfo) -> str:
        return f"http://localhost:{deployment.port}"


class TestAdapterRegistry:
    """Adapter registry tests."""

    def test_register_and_lookup(self) -> None:
        registry = AdapterRegistry()
        registry.register("mock", _TestAdapter)
        cls = registry.get("mock")
        assert cls is _TestAdapter

    def test_register_multiple_backends(self) -> None:
        registry = AdapterRegistry()
        registry.register("vllm", _TestAdapter)
        registry.register("koboldcpp", _AnotherAdapter)
        assert registry.get("vllm") is _TestAdapter
        assert registry.get("koboldcpp") is _AnotherAdapter

    def test_unknown_backend_raises(self) -> None:
        registry = AdapterRegistry()
        registry.register("mock", _TestAdapter)
        with pytest.raises(KeyError, match="unknown"):
            registry.get("nonexistent")

    def test_factory_instantiates_with_args(self) -> None:
        registry = AdapterRegistry()
        registry.register("mock", _TestAdapter)
        adapter = registry.create("mock", tag="alpha")
        assert isinstance(adapter, _TestAdapter)
        assert adapter.tag == "alpha"

    def test_factory_no_args(self) -> None:
        """Factory works with no extra kwargs."""

        class SimpleAdapter(BackendAdapter):
            def start(self, model_config: ModelConfig, port: int) -> DeploymentInfo:
                return DeploymentInfo(
                    model_name="m",
                    backend="b",
                    port=port,
                    status="running",
                    container_id="c",
                )

            def stop(self, deployment: DeploymentInfo) -> None:
                pass

            def health(self, deployment: DeploymentInfo) -> str:
                return "running"

            def endpoint(self, deployment: DeploymentInfo) -> str:
                return f"http://localhost:{deployment.port}"

        registry = AdapterRegistry()
        registry.register("simple", SimpleAdapter)
        adapter = registry.create("simple")
        assert isinstance(adapter, SimpleAdapter)

    def test_factory_unknown_backend_raises(self) -> None:
        registry = AdapterRegistry()
        with pytest.raises(KeyError):
            registry.create("nonexistent")

    def test_registered_backends_list(self) -> None:
        registry = AdapterRegistry()
        assert registry.list_backends() == []
        registry.register("vllm", _TestAdapter)
        assert "vllm" in registry.list_backends()
        registry.register("koboldcpp", _AnotherAdapter)
        backends = registry.list_backends()
        assert "vllm" in backends
        assert "koboldcpp" in backends
