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

from switchyard.config.models import ResolvedDeployment
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.registry import AdapterRegistry


def _make_resolved(
    overrides: dict | None = None,
) -> ResolvedDeployment:
    """Create a minimal ResolvedDeployment for tests."""
    defaults: dict = {
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


class _TestAdapter(BackendAdapter):
    """Minimal concrete adapter for tests."""

    def __init__(self, tag: str = "") -> None:
        self.tag = tag
        self._started: list[str] = []

    def start(self, resolved: ResolvedDeployment, port: int) -> DeploymentInfo:
        self._started.append(resolved.backend)
        return DeploymentInfo(
            model_name=resolved.deployment_name,
            backend=resolved.backend,
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

    def start(self, resolved: ResolvedDeployment, port: int) -> DeploymentInfo:
        return DeploymentInfo(
            model_name=resolved.deployment_name,
            backend=resolved.backend,
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
            def start(
                self, resolved: ResolvedDeployment, port: int,
            ) -> DeploymentInfo:
                return DeploymentInfo(
                    model_name=resolved.deployment_name,
                    backend=resolved.backend,
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
