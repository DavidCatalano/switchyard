"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_app_settings(request: pytest.FixtureRequest) -> None:
    """Isolate tests from the actual .env file by patching AppSettings.

    Tests that need real env var resolution skip isolation via the
    ``no_isolate`` marker.
    """
    has_marker = any(
        mark.name == "no_isolate"
        for mark in request.node.iter_markers()
    )
    if has_marker:
        yield
        return

    # AppSettings — patched at every import site so resolve_deployment()
    # and other code paths also get the mock.
    attrs = (
        "config_path",
        "log_level",
        "api_host",
        "api_port",
        "active_host",
        "docker_host",
    )
    mock = MagicMock()
    for attr in attrs:
        setattr(mock, attr, None)

    with patch(
        "switchyard.config.models.AppSettings", return_value=mock,
    ), patch(
        "switchyard.config.loader.AppSettings", return_value=mock,
    ), patch(
        "switchyard.core.docker.AppSettings", return_value=mock,
    ):
        yield
