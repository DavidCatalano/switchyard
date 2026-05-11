"""Fixtures and collection hooks for live infrastructure smoke tests.

Tests under ``tests/live/`` exercise real infrastructure (Docker daemon,
running Switchyard API, loaded deployments) and are skipped by default
via ``addopts = ["-m", "not live"]`` in ``pyproject.toml``.

Run them via ``make smoke`` or ``pytest -m live``.

Auto-tagging: every test in this directory is automatically marked
``live`` and ``no_isolate`` at collection time, so contributors don't
need to remember per-test decorators, and live tests use real ``.env``
values rather than the ``AppSettings`` mock from the parent conftest.

Infra fixtures: tests should request whichever fixtures describe their
infrastructure needs (``live_docker``, ``live_backend_url``). Each
fixture probes its dependency and ``pytest.skip()``s if unreachable, so
running ``make smoke`` on a machine missing infrastructure produces
clean skips rather than failures.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    """Auto-tag every test in tests/live/ with `live` and `no_isolate`."""
    live_marker = pytest.mark.live
    no_isolate_marker = pytest.mark.no_isolate
    for item in items:
        path = str(item.path).replace("\\", "/")
        if "/tests/live/" in path:
            item.add_marker(live_marker)
            item.add_marker(no_isolate_marker)


@pytest.fixture
def live_docker() -> Iterator[None]:
    """Skip the test if the Docker daemon is not reachable."""
    try:
        from docker import from_env
        from docker.errors import APIError, DockerException
    except ImportError:
        pytest.skip("docker SDK not installed")
        return
    try:
        client = from_env()
        if not client.ping():
            pytest.skip("Docker daemon not reachable")
            return
    except (DockerException, APIError) as exc:
        pytest.skip(f"Docker daemon not reachable: {exc}")
        return
    yield


@pytest.fixture
def live_backend_url() -> Iterator[str]:
    """Resolve and verify the Switchyard API URL, or skip.

    Reads ``SWITCHYARD_SMOKE_URL`` (default ``http://localhost:8000``).
    Skips if ``/health`` is unreachable or unhealthy.
    """
    url = os.environ.get("SWITCHYARD_SMOKE_URL", "http://localhost:8000")
    try:
        resp = httpx.get(f"{url}/health", timeout=2.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Switchyard API not reachable at {url}: {exc}")
        return
    yield url
