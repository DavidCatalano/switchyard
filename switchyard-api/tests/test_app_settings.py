"""Tests for AppSettings .env bootstrap validation.

Validates that AppSettings:
- Loads valid bootstrap fields from .env
- Raises validation error on unknown SWITCHYARD_* keys (hard fail)
- Raises validation error on non-prefixed keys in .env with extra=forbid
- Matches .env.example keys
"""

from __future__ import annotations

import os
import pathlib

import pytest
from pydantic import ValidationError

from switchyard.config.models import AppSettings


def _clear_switchyard_env() -> dict[str, str]:
    """Remove all SWITCHYARD_* env vars and return originals for restore."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("SWITCHYARD_")}
    for k in saved:
        del os.environ[k]
    return saved


def _restore_switchyard_env(saved: dict[str, str]) -> None:
    os.environ.update(saved)


def _load_settings(env_file: pathlib.Path) -> AppSettings:
    """Load AppSettings from an isolated .env file.

    _env_file is an internal pydantic-settings keyword not in the type stub,
    so we silence the mypy warning.
    """
    return AppSettings(_env_file=str(env_file))  # type: ignore[call-arg]


class TestAppSettingsDefaults:
    """AppSettings uses sensible defaults when .env is absent."""

    def test_defaults_with_empty_env(self, tmp_path: pathlib.Path) -> None:
        """Default values apply when .env is empty and no env vars set."""
        env_file = tmp_path / ".env"
        env_file.write_text("# empty\n")
        saved = _clear_switchyard_env()
        try:
            settings = _load_settings(env_file)
            assert settings.config_path == "config.yaml"
            assert settings.log_level == "info"
            assert settings.api_host == "0.0.0.0"
            assert settings.api_port == 8000
            assert settings.active_host is None
            assert settings.docker_host is None
        finally:
            _restore_switchyard_env(saved)


class TestAppSettingsUnknownKeysFail:
    """Unknown SWITCHYARD_* keys in .env fail loudly at startup."""

    def test_unknown_prefixed_key_raises(self, tmp_path: pathlib.Path) -> None:
        """Unknown SWITCHYARD_* key in .env raises ValidationError."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "SWITCHYARD_CONFIG_PATH=config.yaml\n"
            "SWITCHYARD_UNKNOWN_KEY=value\n"
        )
        saved = _clear_switchyard_env()
        try:
            with pytest.raises(ValidationError, match="unknown_key"):
                _load_settings(env_file)
        finally:
            _restore_switchyard_env(saved)

    def test_non_prefixed_key_in_env_raises(self, tmp_path: pathlib.Path) -> None:
        """A non-prefixed key in .env also fails (extra=forbid is strict).

        pydantic-settings reads all keys from .env (not just prefixed ones).
        With extra=forbid, a bare key like STALE_VAR=old_value is rejected.
        """
        env_file = tmp_path / ".env"
        env_file.write_text(
            "CONFIG_PATH=config.yaml\n"
            "STALE_VAR=old_value\n"
        )
        saved = _clear_switchyard_env()
        try:
            with pytest.raises(ValidationError, match="stale_var"):
                _load_settings(env_file)
        finally:
            _restore_switchyard_env(saved)


class TestAppSettingsValidKeys:
    """All valid bootstrap keys are accepted."""

    def test_valid_keys_accepted(self, tmp_path: pathlib.Path) -> None:
        """All documented bootstrap keys load without error."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "SWITCHYARD_CONFIG_PATH=config.yaml\n"
            "SWITCHYARD_LOG_LEVEL=debug\n"
            "SWITCHYARD_API_HOST=127.0.0.1\n"
            "SWITCHYARD_API_PORT=9000\n"
            "SWITCHYARD_ACTIVE_HOST=myhost\n"
            "SWITCHYARD_DOCKER_HOST=tcp://127.0.0.1:2375\n"
        )
        saved = _clear_switchyard_env()
        try:
            settings = _load_settings(env_file)
            assert settings.config_path == "config.yaml"
            assert settings.log_level == "debug"
            assert settings.api_host == "127.0.0.1"
            assert settings.api_port == 9000
            assert settings.active_host == "myhost"
            assert settings.docker_host == "tcp://127.0.0.1:2375"
        finally:
            _restore_switchyard_env(saved)


class TestAppSettingsEnvExampleMatches:
    """Keys in .env.example must match AppSettings fields exactly."""

    def test_env_example_keys_match_settings(self) -> None:
        """Every SWITCHYARD_* key in .env.example maps to an AppSettings field."""
        project_root = pathlib.Path(__file__).resolve().parent.parent
        env_example = project_root / ".env.example"
        settings_fields = {
            "config_path",
            "log_level",
            "api_host",
            "api_port",
            "active_host",
            "docker_host",
        }

        env_keys: set[str] = set()
        for line in env_example.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") and "=" in line:
                key = line.lstrip("#").strip().split("=", 1)[0]
                if key.startswith("SWITCHYARD_"):
                    env_keys.add(key[len("SWITCHYARD_"):].lower())
            elif "=" in line:
                key = line.split("=", 1)[0]
                if key.startswith("SWITCHYARD_"):
                    env_keys.add(key[len("SWITCHYARD_"):].lower())

        extra_keys = env_keys - settings_fields
        assert not extra_keys, f"Extra keys: {extra_keys}"

        missing_keys = settings_fields - env_keys
        assert not missing_keys, f"Missing keys: {missing_keys}"
