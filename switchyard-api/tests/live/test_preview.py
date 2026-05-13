"""Live smoke tests for the preview endpoint (SEP-004, Task C4).

Exercises the preview API against a running Switchyard instance.
Skipped by default unless run with ``-m live`` or ``make smoke``.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_preview_endpoint_returns_200(live_backend_url: str) -> None:
    """Preview endpoint returns 200 for a configured deployment."""
    import httpx

    # Use a deployment name that exists in config.yaml.
    # The live conftest skips if no API is reachable.
    async with httpx.AsyncClient(timeout=10.0) as client:
        # First, list deployments to find a valid name
        resp = await client.get(f"{live_backend_url}/api/deployments")
        resp.raise_for_status()
        deployments = resp.json()
        assert len(deployments) > 0, "no deployments configured"

        dep_name = deployments[0]["deployment_name"]
        resp = await client.get(
            f"{live_backend_url}/api/deployments/{dep_name}/preview"
        )

    assert resp.status_code == 200
    body = resp.json()
    # Verify core structure
    assert "image" in body
    assert "command" in body
    assert isinstance(body["command"], list)
    assert "--host" in body["command"]
    assert "0.0.0.0" in body["command"]
    assert "--model" in body["command"]
    assert "environment" in body
    assert isinstance(body["environment"], dict)
    assert "mounts" in body
    assert "port_range" in body
    assert isinstance(body["port_range"], list)
    assert len(body["port_range"]) == 2
    assert "container_options" in body
    assert "deployment_status" in body
    assert "container_status" in body


@pytest.mark.asyncio
async def test_preview_endpoint_redacts_sensitive(live_backend_url: str) -> None:
    """Preview endpoint redacts sensitive values in command and environment."""
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{live_backend_url}/api/deployments")
        resp.raise_for_status()
        deployments = resp.json()
        assert len(deployments) > 0
        dep_name = deployments[0]["deployment_name"]

        resp = await client.get(
            f"{live_backend_url}/api/deployments/{dep_name}/preview"
        )

    assert resp.status_code == 200
    body = resp.json()
    cmd = body["command"]

    # If hf_token is set, --hf-token value must be redacted
    hf_idx = cmd.index("--hf-token") if "--hf-token" in cmd else -1
    if hf_idx >= 0 and hf_idx + 1 < len(cmd):
        assert cmd[hf_idx + 1] == "***redacted***", (
            "hf_token not redacted in command"
        )

    # If HF_TOKEN is in env, it must be redacted
    env = body["environment"]
    if "HF_TOKEN" in env:
        assert env["HF_TOKEN"] == "***redacted***"


@pytest.mark.asyncio
async def test_preview_excludes_switchyard_vars(live_backend_url: str) -> None:
    """Preview environment does not contain SWITCHYARD_* variables."""
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{live_backend_url}/api/deployments")
        resp.raise_for_status()
        deployments = resp.json()
        assert len(deployments) > 0
        dep_name = deployments[0]["deployment_name"]

        resp = await client.get(
            f"{live_backend_url}/api/deployments/{dep_name}/preview"
        )

    assert resp.status_code == 200
    body = resp.json()
    env = body["environment"]
    for key in env:
        assert not key.startswith("SWITCHYARD_"), (
            f"SWITCHYARD_* key {key!r} leaked into preview environment"
        )
