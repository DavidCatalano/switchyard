"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_app_settings(request: pytest.FixtureRequest) -> None:
    """Isolate tests from the actual .env file by patching AppSettings.

    Patches both:
    - switchyard.config.loader.AppSettings (legacy SEP-001)
    - switchyard.config.models.AppSettings (SEP-002 entity model)

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

    # Legacy AppSettings (loader.py)
    legacy_attrs = (
        "config_path",
        "base_port",
        "log_level",
        "docker_host",
        "backend_host",
        "backend_scheme",
        "docker_network",
        "health_interval_seconds",
        "health_timeout_seconds",
    )
    legacy_mock = MagicMock()
    for attr in legacy_attrs:
        setattr(legacy_mock, attr, None)

    # SEP-002 AppSettings (models.py)
    new_attrs = (
        "config_path",
        "log_level",
        "api_host",
        "api_port",
        "active_host",
        "docker_host",
    )
    new_mock = MagicMock()
    for attr in new_attrs:
        setattr(new_mock, attr, None)

    with patch(
        "switchyard.config.loader.AppSettings", return_value=legacy_mock,
    ), patch(
        "switchyard.config.models.AppSettings", return_value=new_mock,
    ), patch(
        "tests.test_entity_models.AppSettings", return_value=new_mock,
    ):
        yield
