"""Tests for AppSettings .env bootstrap validation.

Validates that AppSettings:
- Loads valid bootstrap fields from .env
- Raises validation error on unknown SWITCHYARD_* keys (hard fail)
- Matches .env.example keys
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from switchyard.config.models import AppSettings


class TestAppSettingsDefaults:
    """AppSettings uses sensible defaults when .env is absent."""

    def test_defaults_via_subprocess(self, tmp_path: pathlib.Path) -> None:
        """Default values apply when .env has no SWITCHYARD_* keys."""
        env_file = tmp_path / ".env"
        env_file.write_text("# empty\n")
        
        script = tmp_path / "test_defaults.py"
        script.write_text(
            "import sys; sys.path.insert(0, 'switchyard-api/src')\n"
            "from switchyard.config.models import AppSettings\n"
            "s = AppSettings()\n"
            "assert s.config_path == 'config.yaml'\n"
            "assert s.log_level == 'info'\n"
            "assert s.api_host == '0.0.0.0'\n"
            "assert s.api_port == 8000\n"
            "assert s.active_host is None\n"
            "assert s.docker_host is None\n"
            "print('OK')\n"
        )
        
        env = {k: v for k, v in os.environ.items() if not k.startswith("SWITCHYARD_")}
        result = subprocess.run(
            ["uv", "run", "python", str(script)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 0, f"Failed: {result.stdout}\n{result.stderr}"
        assert "OK" in result.stdout


class TestAppSettingsUnknownKeysFail:
    """Unknown SWITCHYARD_* keys in .env fail loudly at startup."""

    def test_unknown_key_via_subprocess(self, tmp_path: pathlib.Path) -> None:
        """Unknown SWITCHYARD_* key in isolated .env raises ValidationError."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "SWITCHYARD_CONFIG_PATH=config.yaml\n"
            "SWITCHYARD_UNKNOWN_KEY=value\n"
        )
        
        script = tmp_path / "test_unknown.py"
        script.write_text(
            "import sys; sys.path.insert(0, 'switchyard-api/src')\n"
            "from switchyard.config.models import AppSettings\n"
            "from pydantic import ValidationError\n"
            "try:\n"
            "    s = AppSettings()\n"
            "    print('UNEXPECTED: no error raised')\n"
            "except ValidationError as e:\n"
            "    print('EXPECTED:', e)\n"
            "    sys.exit(1)\n"
        )
        
        result = subprocess.run(
            ["uv", "run", "python", str(script)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={
                **os.environ,
                "SWITCHYARD_CONFIG_PATH": "config.yaml",
                "SWITCHYARD_UNKNOWN_KEY": "value",
            },
        )
        assert result.returncode != 0, "Expected ValidationError for unknown key"
        msg = result.stdout.lower() + result.stderr.lower()
        assert "unknown_key" in msg, f"Expected unknown_key error, got: {msg}"


class TestAppSettingsValidKeys:
    """All valid bootstrap keys are accepted."""

    def test_valid_keys_accepted(self) -> None:
        """All documented bootstrap keys load without error."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("SWITCHYARD_CONFIG_PATH", "config.yaml")
            mp.setenv("SWITCHYARD_LOG_LEVEL", "debug")
            mp.setenv("SWITCHYARD_API_HOST", "127.0.0.1")
            mp.setenv("SWITCHYARD_API_PORT", "9000")
            mp.setenv("SWITCHYARD_ACTIVE_HOST", "myhost")
            mp.setenv("SWITCHYARD_DOCKER_HOST", "tcp://127.0.0.1:2375")
            settings = AppSettings()
        
        assert settings.config_path == "config.yaml"
        assert settings.log_level == "debug"
        assert settings.api_host == "127.0.0.1"
        assert settings.api_port == 9000
        assert settings.active_host == "myhost"
        assert settings.docker_host == "tcp://127.0.0.1:2375"


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
