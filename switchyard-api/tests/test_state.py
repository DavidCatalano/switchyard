"""Tests for the deployment state manager.

Validates:
- Adding a deployment for a model
- Getting deployment info by model name
- Removing a deployment
- Status transitions (loading -> running, loading -> error, etc.)
- Listing all deployments
- Unknown model lookup raises
- Cannot add duplicate model without explicit overwrite
"""

from __future__ import annotations

import pytest

from switchyard.core.adapter import DeploymentInfo
from switchyard.core.state import DeploymentStateManager


def _make_info(
    model_name: str = "qwen-32b",
    backend: str = "vllm",
    port: int = 8000,
    status: str = "running",
    container_id: str = "abc123",
) -> DeploymentInfo:
    return DeploymentInfo(
        model_name=model_name,
        backend=backend,
        port=port,
        status=status,
        container_id=container_id,
    )


class TestDeploymentStateManager:
    """Deployment state manager tests."""

    def test_add_deployment(self) -> None:
        mgr = DeploymentStateManager()
        info = _make_info()
        mgr.add(info)
        assert mgr.get("qwen-32b") == info

    def test_get_unknown_model_raises(self) -> None:
        mgr = DeploymentStateManager()
        with pytest.raises(KeyError, match="not found"):
            mgr.get("unknown-model")

    def test_add_multiple_models(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info(model_name="qwen-32b", port=8000))
        mgr.add(_make_info(model_name="llama-70b", port=8001, backend="vllm"))
        assert mgr.get("qwen-32b").port == 8000
        assert mgr.get("llama-70b").port == 8001

    def test_update_status(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info(status="loading"))
        mgr.update_status("qwen-32b", "running")
        assert mgr.get("qwen-32b").status == "running"

    def test_update_status_unknown_model_raises(self) -> None:
        mgr = DeploymentStateManager()
        with pytest.raises(KeyError):
            mgr.update_status("unknown", "running")

    def test_update_status_invalid_raises(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info())
        with pytest.raises(ValueError, match="invalid status"):
            mgr.update_status("qwen-32b", "crashed")

    def test_remove_deployment(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info())
        mgr.remove("qwen-32b")
        with pytest.raises(KeyError):
            mgr.get("qwen-32b")

    def test_remove_unknown_model_raises(self) -> None:
        mgr = DeploymentStateManager()
        with pytest.raises(KeyError):
            mgr.remove("unknown")

    def test_list_deployments_empty(self) -> None:
        mgr = DeploymentStateManager()
        assert mgr.list_deployments() == {}

    def test_list_deployments(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info(model_name="qwen-32b", port=8000))
        mgr.add(_make_info(model_name="llama-70b", port=8001))
        deployments = mgr.list_deployments()
        assert len(deployments) == 2
        assert "qwen-32b" in deployments
        assert "llama-70b" in deployments

    def test_get_by_port(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info(model_name="qwen-32b", port=8000))
        mgr.add(_make_info(model_name="llama-70b", port=8001))
        info = mgr.get_by_port(8001)
        assert info.model_name == "llama-70b"

    def test_get_by_port_not_found_raises(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info(port=8000))
        with pytest.raises(KeyError):
            mgr.get_by_port(9999)

    def test_duplicate_model_raises(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info())
        with pytest.raises(ValueError, match="already deployed"):
            mgr.add(_make_info())

    def test_duplicate_with_overwrite(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info(port=8000, status="loading"))
        mgr.add(_make_info(port=8001, status="running"), overwrite=True)
        assert mgr.get("qwen-32b").port == 8001
        assert mgr.get("qwen-32b").status == "running"

    def test_status_count_by_state(self) -> None:
        mgr = DeploymentStateManager()
        mgr.add(_make_info(model_name="a", status="running", port=8000))
        mgr.add(_make_info(model_name="b", status="loading", port=8001))
        mgr.add(_make_info(model_name="c", status="running", port=8002))
        mgr.add(_make_info(model_name="d", status="error", port=8003))

        counts = mgr.status_counts()
        assert counts["running"] == 2
        assert counts["loading"] == 1
        assert counts["error"] == 1
        assert counts["stopped"] == 0
