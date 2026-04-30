"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_app_settings(request: pytest.FixtureRequest) -> None:
    """Isolate tests from the actual .env file by patching AppSettings.

    Tests that need real env var resolution (those setting SWITCHYARD_* in
    os.environ) skip isolation via the ``no_isolate`` marker.
    """
    has_marker = any(
        mark.name == "no_isolate"
        for mark in request.node.iter_markers()
    )
    if has_marker:
        yield
        return

    mock_settings = MagicMock()
    for attr in (
        "config_path",
        "base_port",
        "log_level",
        "docker_host",
        "backend_host",
        "backend_scheme",
        "docker_network",
        "health_interval_seconds",
        "health_timeout_seconds",
    ):
        setattr(mock_settings, attr, None)

    with patch("switchyard.config.loader.AppSettings", return_value=mock_settings):
        yield
