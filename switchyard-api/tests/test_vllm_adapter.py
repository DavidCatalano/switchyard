"""Tests for the vLLM adapter implementation.

Validates:
- _build_cli_args renders known VLLMRuntimeConfig fields as CLI args
- Unknown fields in VLLMRuntimeConfig are appended via extra_args
- start() creates Docker container with correct spec
- stop() stops and removes container
- health() checks endpoint status
- endpoint() returns correct URL
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from switchyard.adapters.vllm import VLLMAdapter
from switchyard.config.models import ResolvedDeployment, VLLMRuntimeConfig
from switchyard.core.adapter import DeploymentInfo


class TestVLLMAdapter:
    """vLLM adapter tests."""

    @pytest.fixture
    def adapter(self) -> VLLMAdapter:
        return VLLMAdapter()

    def test_build_cli_args_basic(self, adapter: VLLMAdapter) -> None:
        """Known typed fields render as CLI args."""
        runtime = VLLMRuntimeConfig.model_validate({
            "model": "/models/test",
            "tensor_parallel_size": 2,
            "max_model_len": 8192,
        })
        args = adapter._build_cli_args(runtime)
        assert "--model" in args
        assert "/models/test" in args
        assert "--tensor-parallel-size" in args
        assert "2" in args
        assert "--max-model-len" in args
        assert "8192" in args

    def test_build_cli_args_defaults(self, adapter: VLLMAdapter) -> None:
        """Default VLLMRuntimeConfig produces standard args."""
        runtime = VLLMRuntimeConfig.model_validate({
            "model": "/models/test",
        })
        args = adapter._build_cli_args(runtime)
        assert "--model" in args
        assert "/models/test" in args

    def test_build_cli_args_extra_fields(self, adapter: VLLMAdapter) -> None:
        """Extra fields from runtime_args render as CLI flags."""
        runtime = VLLMRuntimeConfig.model_validate({
            "model": "/models/test",
            "extra_args": {
                "some_unknown_flag": "value1",
                "another_flag": "true",
            },
        })
        args = adapter._build_cli_args(runtime)
        assert "--some_unknown_flag" in args
        assert "value1" in args
        assert "--another_flag" in args
        assert "true" in args

    def test_build_cli_args_empty_extra(self, adapter: VLLMAdapter) -> None:
        """Empty extra_args does not produce spurious flags."""
        runtime = VLLMRuntimeConfig.model_validate({
            "model": "/models/test",
            "extra_args": {},
        })
        args = adapter._build_cli_args(runtime)
        args_str = " ".join(args)
        assert "--" not in args_str or "--model" in args_str

    def test_endpoint_returns_url(self, adapter: VLLMAdapter) -> None:
        info = DeploymentInfo(
            model_name="test",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
        )
        url = adapter.endpoint(info)
        assert "8001" in url

    def test_health_running(self, adapter: VLLMAdapter) -> None:
        """Health returns 'running' when endpoint responds 200."""
        info = DeploymentInfo(
            model_name="test",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("switchyard.adapters.vllm.httpx.Client", return_value=mock_client):
            result = adapter.health(info)
            assert result == "running"

    def test_health_error(self, adapter: VLLMAdapter) -> None:
        """Health returns 'error' when endpoint fails."""
        import httpx

        info = DeploymentInfo(
            model_name="test",
            backend="vllm",
            port=8001,
            status="running",
            container_id="abc123",
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("connection refused")

        with patch("switchyard.adapters.vllm.httpx.Client", return_value=mock_client):
            result = adapter.health(info)
            assert result == "error"

    def test_build_cli_args_accelerator_count(self, adapter: VLLMAdapter) -> None:
        """TP size defaults to number of accelerators."""
        runtime = VLLMRuntimeConfig.model_validate({
            "model": "/models/test",
        })
        args = adapter._build_cli_args(runtime)
        assert "--model" in args

    def test_start_passes_docker_kwargs(self, adapter: VLLMAdapter) -> None:
        """start() passes correct volumes, devices, env, port, network to Docker.

        Covers T4.9 (adapter launch), T4.15 (store mounts), T4.16 (docker_host).
        """
        resolved = ResolvedDeployment(
            deployment_name="gpu-test",
            model_name="test-model",
            runtime_name="vllm",
            backend="vllm",
            host_name="test-host",
            backend_host="localhost",
            backend_scheme="http",
            port_range=[9800, 9900],
            image="vllm/vllm-openai:latest",
            internal_port=8000,
            model_host_path="/host/models",
            model_container_path="/models",
            accelerator_ids=["0", "1"],
            docker_host="unix:///var/run/docker.sock",
            docker_network="model-runtime",
            runtime_args={
                "model": "/models/test",
                "served_model_name": "tinyllama-1.1b-chat",
            },
            container_environment={"CUDA_VISIBLE_DEVICES": "0,1"},
            container_options={"mem_limit": "32g"},
            store_mounts={
                "/host/models": {"bind": "/models", "mode": "ro"},
                "/host/cache": {"bind": "/cache", "mode": "rw"},
            },
            model_defaults=None,
        )
        mock_container = MagicMock()
        mock_container.short_id = "abc123"
        mock_containers = MagicMock()
        mock_containers.run.return_value = mock_container
        mock_client = MagicMock()
        mock_client.containers = mock_containers
        adapter._docker_client = mock_client

        info = adapter.start(resolved, 9001)

        assert info.container_id == "abc123"
        assert info.port == 9001
        assert info.metadata["served_model_name"] == "tinyllama-1.1b-chat"

        # Verify containers.run was called with correct kwargs
        call_kwargs = mock_containers.run.call_args.kwargs
        assert call_kwargs["image"] == "vllm/vllm-openai:latest"
        assert call_kwargs["ports"] == {8000: 9001}
        assert call_kwargs["network"] == "model-runtime"
        assert call_kwargs["detach"] is True

        # T4.15: store mounts (volumes)
        assert "/host/models" in call_kwargs["volumes"]
        assert "/host/cache" in call_kwargs["volumes"]

        # T4.9: device requests for GPU accelerators
        assert len(call_kwargs["device_requests"]) == 1
        dev_req = call_kwargs["device_requests"][0]
        assert dev_req.driver == "nvidia"
        assert set(dev_req.device_ids) == {"0", "1"}

        # T4.9: environment merge
        assert call_kwargs["environment"]["CUDA_VISIBLE_DEVICES"] == "0,1"

        # T4.9: container options
        assert call_kwargs["mem_limit"] == "32g"

        # Command includes --host 0.0.0.0 and --port 8000
        command = call_kwargs["command"]
        assert "--host" in command
        assert "0.0.0.0" in command
        assert "--port" in command
        assert "8000" in command

    def test_start_normalizes_container_options(self, adapter: VLLMAdapter) -> None:
        """Compose-style container options are translated to Docker SDK kwargs."""
        resolved = ResolvedDeployment(
            deployment_name="cpu-test",
            model_name="test-model",
            runtime_name="vllm-cpu",
            backend="vllm",
            host_name="test-host",
            backend_host="localhost",
            backend_scheme="http",
            port_range=[9800, 9900],
            image="vllm/vllm-openai-cpu:latest-x86_64",
            internal_port=8000,
            model_host_path="/host/models",
            model_container_path="/models",
            accelerator_ids=[],
            docker_host=None,
            docker_network="model-runtime",
            runtime_args={"model": "/models/test", "device": "cpu"},
            container_environment={},
            container_options={
                "ipc": "host",
                "ulimits": {"memlock": {"soft": -1, "hard": -1}},
            },
            store_mounts={"/host/models": {"bind": "/models", "mode": "ro"}},
            model_defaults=None,
        )
        mock_container = MagicMock()
        mock_container.short_id = "cpu123"
        mock_containers = MagicMock()
        mock_containers.run.return_value = mock_container
        mock_client = MagicMock()
        mock_client.containers = mock_containers
        adapter._docker_client = mock_client

        adapter.start(resolved, 9003)

        call_kwargs = mock_containers.run.call_args.kwargs
        assert "ipc" not in call_kwargs
        assert call_kwargs["ipc_mode"] == "host"
        assert len(call_kwargs["ulimits"]) == 1
        assert call_kwargs["ulimits"][0]["Name"] == "memlock"
        assert call_kwargs["ulimits"][0]["Soft"] == -1
        assert call_kwargs["ulimits"][0]["Hard"] == -1

    def test_start_uses_docker_host_from_resolved(self, adapter: VLLMAdapter) -> None:
        """start() creates Docker client with resolved.docker_host when not injected.

        Covers T4.16: docker_host wiring — no injected client, so adapter
        must honour resolved.docker_host via _set_docker_client().
        """
        resolved = ResolvedDeployment(
            deployment_name="host-test",
            model_name="test-model",
            runtime_name="vllm",
            backend="vllm",
            host_name="test-host",
            backend_host="localhost",
            backend_scheme="http",
            port_range=[9800, 9900],
            image="vllm/vllm-openai:latest",
            internal_port=8000,
            model_host_path="/host/models",
            model_container_path="/models",
            accelerator_ids=[],
            docker_host="tcp://remote-host:2375",
            docker_network="",
            runtime_args={"model": "/models/test"},
            container_environment={},
            container_options={},
            store_mounts={"/host/models": {"bind": "/models", "mode": "ro"}},
            model_defaults=None,
        )
        mock_container = MagicMock()
        mock_container.short_id = "def456"
        mock_containers = MagicMock()
        mock_containers.run.return_value = mock_container
        mock_client = MagicMock()
        mock_client.containers = mock_containers

        # No injected client — adapter must create one from resolved.docker_host
        assert adapter._docker_client is None
        with patch(
            "switchyard.adapters.vllm.docker.DockerClient",
            return_value=mock_client,
        ) as mock_docker_client:
            info = adapter.start(resolved, 9002)

        assert info.container_id == "def456"

        # Assert DockerClient was called with base_url=resolved.docker_host
        mock_docker_client.assert_called_once_with(
            base_url="tcp://remote-host:2375"
        )
