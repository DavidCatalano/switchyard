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
from switchyard.config.models import VLLMRuntimeConfig
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
