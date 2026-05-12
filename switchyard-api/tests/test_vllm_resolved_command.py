"""Tests that build vLLM CLI args from a fully resolved deployment.

Exercises the resolver → adapter path: a ``ResolvedDeployment`` is
constructed, its ``runtime_args`` are validated as ``VLLMRuntimeConfig``,
and ``VLLMAdapter._build_cli_args`` produces the expected CLI flags.

Pure unit coverage — no Docker required. Live smoke tests that touch
the Docker daemon live under ``tests/live/``.
"""

from __future__ import annotations

from typing import Any

import pytest

from switchyard.config.models import ResolvedDeployment, VLLMRuntimeConfig


def _make_resolved(
    backend: str = "vllm",
    deployment_name: str = "integration-test",
    overrides: dict[str, Any] | None = None,
) -> ResolvedDeployment:
    """Create a ResolvedDeployment for command-construction tests."""
    defaults: dict[str, Any] = {
        "deployment_name": deployment_name,
        "model_name": "test-model",
        "runtime_name": backend,
        "backend": backend,
        "host_name": "test-host",
        "backend_host": "localhost",
        "backend_scheme": "http",
        "port_range": [9800, 9900],
        "image": "vllm/vllm-openai:latest",
        "internal_port": 8000,
        "model_host_path": "/tmp/models",
        "model_container_path": "/models",
        "accelerator_ids": [],
        "docker_host": None,
        "docker_network": "model-runtime-integration",
        "runtime_args": {"model": "/models"},
        "container_environment": {},
        "container_options": {},
        "store_mounts": {},
        "model_defaults": None,
        "health_timeout_seconds": 300.0,
    }
    if overrides:
        defaults.update(overrides)
    return ResolvedDeployment(**defaults)


class TestVLLMExtraForbid:
    """VLLMRuntimeConfig rejects unknown top-level keys (SEP-004, Task A1)."""

    def test_unknown_key_raises_validation_error(self) -> None:
        """Unknown top-level key is rejected at validation time."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            VLLMRuntimeConfig.model_validate(
                {"model": "/models/test", "max_seqs": 10}
            )

    def test_typo_in_known_field_raises_validation_error(self) -> None:
        """Typo on a known field name is rejected at validation time."""
        from pydantic import ValidationError

        # "tensor_parallel_size" is valid; "tensor_parallel_sizes" is not
        with pytest.raises(ValidationError):
            VLLMRuntimeConfig.model_validate(
                {
                    "model": "/models/test",
                    "tensor_parallel_sizes": 2,  # typo
                }
            )

    def test_known_fields_still_validate(self) -> None:
        """Known fields validate normally after extra: forbid."""
        runtime = VLLMRuntimeConfig.model_validate(
            {
                "model": "/models/test",
                "tensor_parallel_size": 2,
                "max_model_len": 4096,
                "dtype": "auto",
            }
        )
        assert runtime.tensor_parallel_size == 2
        assert runtime.max_model_len == 4096
        assert runtime.dtype == "auto"

    def test_device_field_has_switchyard_only_metadata(self) -> None:
        """device field is marked switchyard_only in Pydantic model metadata."""
        field_info = VLLMRuntimeConfig.model_fields["device"]
        assert field_info.json_schema_extra == {"switchyard_only": True}

    def test_no_other_field_has_switchyard_only_metadata(self) -> None:
        """Only device is marked switchyard_only; no other field is."""
        for name, field_info in VLLMRuntimeConfig.model_fields.items():
            if name == "device":
                continue
            extra = field_info.json_schema_extra
            assert not (isinstance(extra, dict) and extra.get("switchyard_only")), (
                f"unexpected switchyard_only on field {name!r}"
            )


class TestVLLMResolvedCommand:
    """Tests for resolved deployment command construction."""

    def test_cli_args_build_from_resolved_deployment(self) -> None:
        """ResolvedDeployment runtime args validate and render as CLI args."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            deployment_name="int-test-start",
            overrides={
                "docker_network": "",  # use default bridge
            },
        )
        adapter = VLLMAdapter()

        runtime = VLLMRuntimeConfig.model_validate(resolved.runtime_args)
        cli_args = adapter._build_cli_args(runtime)
        assert isinstance(cli_args, list)
        assert any("--model" in arg for arg in cli_args)
        assert len(cli_args) > 0

    def test_cli_args_structure(self) -> None:
        """_build_cli_args returns a flat list of strings."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            overrides={
                "runtime_args": {
                    "model": "/models/test",
                    "tensor_parallel_size": 1,
                    "max_model_len": 4096,
                },
            },
        )
        adapter = VLLMAdapter()
        runtime = VLLMRuntimeConfig.model_validate(resolved.runtime_args)
        args = adapter._build_cli_args(runtime)

        assert isinstance(args, list)
        assert all(isinstance(a, str) for a in args)
        assert "--model" in args
        assert "/models/test" in args
        assert "--tensor-parallel-size" in args
        assert "1" in args

    def test_device_never_reaches_cli(self) -> None:
        """device is excluded from CLI args by field metadata, not string matching."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            overrides={
                "runtime_args": {
                    "model": "/models/test",
                    "device": "cpu",
                    "max_model_len": 4096,
                },
            },
        )
        adapter = VLLMAdapter()
        runtime = VLLMRuntimeConfig.model_validate(resolved.runtime_args)
        args = adapter._build_cli_args(runtime)
        assert "--device" not in args
        assert "cpu" not in args
        assert "--max-model-len" in args
        assert "4096" in args

    def test_adapter_derives_skip_from_metadata(self) -> None:
        """Adapter build_cli_args uses field metadata to skip switchyard_only."""
        from switchyard.adapters.vllm import VLLMAdapter

        runtime = VLLMRuntimeConfig.model_validate(
            {
                "model": "/models/test",
                "device": "cuda",
                "max_model_len": 2048,
            }
        )
        # Call the static method directly
        args = VLLMAdapter._build_cli_args(runtime)
        # Verify that the skip is driven by metadata, not by hardcoded list
        switchyard_only_fields = {
            name
            for name, fi in VLLMRuntimeConfig.model_fields.items()
            if isinstance(fi.json_schema_extra, dict)
            and fi.json_schema_extra.get("switchyard_only")
        }
        assert switchyard_only_fields == {"device"}
        # None of those fields should appear in CLI args
        for field_name in switchyard_only_fields:
            flag = "--" + field_name.replace("_", "-")
            assert flag not in args, f"{flag!r} (switchyard_only) leaked into CLI args"

    def test_extra_args_dict_values_are_json_serialized(self) -> None:
        """Dict values inside extra_args are JSON-serialized, not Python repr."""
        import json

        from switchyard.adapters.vllm import VLLMAdapter

        runtime = VLLMRuntimeConfig.model_validate(
            {
                "model": "/models/test",
                "extra_args": {"some_config": {"a": 1, "b": "hello"}},
            }
        )
        args = VLLMAdapter._build_cli_args(runtime)
        # Find the value after --some-config
        idx = args.index("--some-config")
        value_str = args[idx + 1]
        # Should be valid JSON (not Python repr with single quotes)
        parsed = json.loads(value_str)
        assert parsed == {"a": 1, "b": "hello"}

    def test_extra_args_keys_are_kebab_case(self) -> None:
        """extra_args keys are normalized through snake-case → kebab-case conversion."""
        from switchyard.adapters.vllm import VLLMAdapter

        runtime = VLLMRuntimeConfig.model_validate(
            {
                "model": "/models/test",
                "extra_args": {"disable_log_requests": True, "some_flag": "value"},
            }
        )
        args = VLLMAdapter._build_cli_args(runtime)
        # disable_log_requests → --disable-log-requests
        assert "--disable-log-requests" in args
        assert "--disable_log_requests" not in args
        # some_flag → --some-flag
        assert "--some-flag" in args
        assert "--some_flag" not in args


class TestVLLMLaunchSpec:
    """Tests for VLLMAdapter.build_launch_spec() (SEP-004, Task A7)."""

    def test_build_launch_spec_returns_structured_dict(self) -> None:
        """build_launch_spec returns a structured dict with all launch fields."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            overrides={
                "runtime_args": {
                    "model": "/models/test",
                    "max_model_len": 4096,
                },
                "image": "vllm/vllm-openai:latest",
                "internal_port": 8000,
                "docker_network": "model-runtime",
                "container_environment": {"HF_HOME": "/hf-cache"},
            },
        )
        adapter = VLLMAdapter()
        spec = adapter.build_launch_spec(resolved, port=9500)

        assert spec["image"] == "vllm/vllm-openai:latest"
        assert isinstance(spec["command"], list)
        assert "--host" in spec["command"]
        assert "0.0.0.0" in spec["command"]
        assert spec["environment"] == {"HF_HOME": "/hf-cache"}
        assert spec["network"] == "model-runtime"
        assert spec["ports"] == {8000: 9500}
        assert spec["name"] == "switchyard-integration-test"
        assert spec["labels"]["switchyard.managed"] == "true"
        assert spec["labels"]["switchyard.deployment"] == "integration-test"

    def test_build_launch_spec_cuda_device_requests(self) -> None:
        """CUDA device sets device_requests with specific accelerator IDs."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            overrides={
                "runtime_args": {
                    "model": "/models/test",
                    "device": "cuda",
                },
                "accelerator_ids": ["0", "1"],
            },
        )
        adapter = VLLMAdapter()
        spec = adapter.build_launch_spec(resolved, port=9500)

        assert spec["device_requests"] is not None
        assert len(spec["device_requests"]) == 1
        # Check the device request has the correct IDs
        dr = spec["device_requests"][0]
        assert dr.driver == "nvidia"
        assert dr.device_ids == ["0", "1"]

    def test_build_launch_spec_cpu_env_vars(self) -> None:
        """CPU device sets VLLM_CPU_* env vars and no device_requests."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            overrides={
                "runtime_args": {
                    "model": "/models/test",
                    "device": "cpu",
                },
            },
        )
        adapter = VLLMAdapter()
        spec = adapter.build_launch_spec(resolved, port=9500)

        assert spec["device_requests"] is None
        assert spec["environment"]["VLLM_CPU_KVCACHE_SPACE"] == "4"
        assert spec["environment"]["VLLM_CPU_NUM_OF_RESERVED_CPU"] == "1"

    def test_build_launch_spec_hf_token_in_env(self) -> None:
        """HF_TOKEN is set in environment when runtime has hf_token."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            overrides={
                "runtime_args": {
                    "model": "/models/test",
                    "hf_token": "secret-token-123",
                },
            },
        )
        adapter = VLLMAdapter()
        spec = adapter.build_launch_spec(resolved, port=9500)

        assert spec["environment"]["HF_TOKEN"] == "secret-token-123"

    def test_build_launch_spec_command_matches_start(self) -> None:
        """build_launch_spec command matches what start() would send to Docker."""
        from switchyard.adapters.vllm import VLLMAdapter

        resolved = _make_resolved(
            overrides={
                "runtime_args": {
                    "model": "/models/test",
                    "max_model_len": 4096,
                    "dtype": "auto",
                    "tensor_parallel_size": 2,
                },
                "image": "vllm/vllm-openai:latest",
                "internal_port": 8000,
            },
        )
        adapter = VLLMAdapter()
        spec = adapter.build_launch_spec(resolved, port=9500)

        # Command should start with --host/--port, then model, then other flags
        cmd = spec["command"]
        assert cmd[:4] == ["--host", "0.0.0.0", "--port", "8000"]
        assert "--model" in cmd
        assert "/models/test" in cmd
        assert "--max-model-len" in cmd
        assert "4096" in cmd
        assert "--dtype" in cmd
        assert "auto" in cmd
        assert "--tensor-parallel-size" in cmd
        assert "2" in cmd
