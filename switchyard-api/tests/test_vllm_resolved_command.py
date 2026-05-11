"""Tests that build vLLM CLI args from a fully resolved deployment.

Exercises the resolver → adapter path: a ``ResolvedDeployment`` is
constructed, its ``runtime_args`` are validated as ``VLLMRuntimeConfig``,
and ``VLLMAdapter._build_cli_args`` produces the expected CLI flags.

Pure unit coverage — no Docker required. Live smoke tests that touch
the Docker daemon live under ``tests/live/``.
"""

from __future__ import annotations

from typing import Any

from switchyard.config.models import ResolvedDeployment, VLLMRuntimeConfig


def _make_resolved(
    backend: str = "vllm",
    deployment_name: str = "integration-test",
    overrides: dict[str, Any] | None = None,
) -> ResolvedDeployment:
    """Create a ResolvedDeployment for command-construction tests."""
    defaults: dict[str, Any] = {
        "deployment_name": deployment_name,
        "model_name": "test-model",
        "runtime_name": backend,
        "backend": backend,
        "host_name": "test-host",
        "backend_host": "localhost",
        "backend_scheme": "http",
        "port_range": [9800, 9900],
        "image": "vllm/vllm-openai:latest",
        "internal_port": 8000,
        "model_host_path": "/tmp/models",
        "model_container_path": "/models",
        "accelerator_ids": [],
        "docker_host": None,
        "docker_network": "model-runtime-integration",
        "runtime_args": {"model": "/models"},
        "container_environment": {},
        "container_options": {},
        "store_mounts": {},
        "model_defaults": None,
        "health_timeout_seconds": 300.0,
    }
    if overrides:
        defaults.update(overrides)
    return ResolvedDeployment(**defaults)


class TestVLLMResolvedCommand:
    """Tests for resolved deployment command construction."""

    def test_cli_args_build_from_resolved_deployment(self) -> None:
        """ResolvedDeployment runtime args validate and render as CLI args."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            deployment_name="int-test-start",
            overrides={
                "docker_network": "",  # use default bridge
            },
        )
        adapter = VLLMAdapter()

        runtime = VLLMRuntimeConfig.model_validate(resolved.runtime_args)
        cli_args = adapter._build_cli_args(runtime)
        assert isinstance(cli_args, list)
        assert any("--model" in arg for arg in cli_args)
        assert len(cli_args) > 0

    def test_cli_args_structure(self) -> None:
        """_build_cli_args returns a flat list of strings."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            overrides={
                "runtime_args": {
                    "model": "/models/test",
                    "tensor_parallel_size": 1,
                    "max_model_len": 4096,
                },
            },
        )
        adapter = VLLMAdapter()
        runtime = VLLMRuntimeConfig.model_validate(resolved.runtime_args)
        args = adapter._build_cli_args(runtime)

        assert isinstance(args, list)
        assert all(isinstance(a, str) for a in args)
        assert "--model" in args
        assert "/models/test" in args
        assert "--tensor-parallel-size" in args
        assert "1" in args
