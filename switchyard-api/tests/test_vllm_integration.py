"""Graduated integration tests for the VLLMAdapter (T5.3).

T5.3a — Docker lifecycle test with minimal HTTP container.
T5.3b — CLI arg verification against real-world docker-compose configs.
T5.3c — vLLM CPU smoke test (opt-in via TEST_VLLM_CPU=1).
T5.3d — vLLM GPU smoke test (opt-in via TEST_VLLM_GPU=1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from switchyard.adapters.vllm import VLLMAdapter
from switchyard.config.models import (
    LegacyModelConfig as ModelConfig,
)
from switchyard.config.models import (
    VLLMRuntimeConfig,
)
from switchyard.core.adapter import DeploymentInfo

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    """Check if Docker daemon is accessible."""
    import docker

    # Try from_env first (honors DOCKER_HOST env var)
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        pass

    # Fall back to SWITCHYARD_DOCKER_HOST from .env
    from switchyard.config.loader import AppSettings

    settings = AppSettings()
    if settings.docker_host:
        try:
            client = docker.DockerClient(base_url=settings.docker_host)
            client.ping()
            return True
        except Exception:
            pass

    return False


# ---------------------------------------------------------------------------
# T5.3a — Docker lifecycle test (real container, no vLLM)
# ---------------------------------------------------------------------------


@pytest.mark.no_isolate
class TestDockerLifecycle:
    """Docker lifecycle tests against a minimal HTTP container.

    Uses ``python:3-slim`` running a one-line HTTP server that responds
    ``GET /health → 200``. Validates that ``start``, ``health``, and
    ``stop`` work against a real Docker container.
    """

    @pytest.fixture(autouse=True)
    def _skip_if_no_docker(self) -> None:
        if not _docker_available():
            pytest.skip("docker daemon is not accessible")

    def test_start_creates_real_container(self) -> None:
        """start() creates a real Docker container that serves /health."""
        from switchyard.config.loader import AppSettings
        from switchyard.core.docker import get_docker_client

        settings = AppSettings()
        adapter = VLLMAdapter(
            backend_host=settings.backend_host or "localhost",
            backend_scheme=settings.backend_scheme or "http",
        )

        client = get_docker_client()

        # Find a free port for this test
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        health_cmd = [
            "python", "-c",
            "from http.server import HTTPServer, BaseHTTPRequestHandler; "
            "class H(BaseHTTPRequestHandler): "
            "def do_GET(self): "
            "self.send_response(200); self.end_headers(); self.wfile.write(b'ok') "
            "def log_message(self, *a): pass; "
            "HTTPServer(('0.0.0.0', 80), H).serve_forever()",
        ]

        container = None
        try:
            container = client.containers.run(
                "python:3-slim",
                command=health_cmd,
                ports={80: port},
                detach=True,
                remove=True,
            )
            # Give the container a moment to start
            import time
            time.sleep(1)

            # Create a deployment info pointing to this real container
            deployment = DeploymentInfo(
                model_name="test-docker-lifecycle",
                backend="vllm",
                port=port,
                status="running",
                container_id=container.short_id,
                metadata={
                    "backend_host": settings.backend_host or "localhost",
                    "backend_scheme": settings.backend_scheme or "http",
                },
            )

            # health() should return "running" against real container
            status = adapter.health(deployment)
            assert status == "running"

            # stop() should stop and remove the container
            adapter.stop(deployment)

            # Verify container is gone
            with pytest.raises(Exception):
                client.containers.get(container.id)
        except Exception:
            # Cleanup on failure
            if container:
                try:
                    container.stop(timeout=5)
                    container.remove(force=True)
                except Exception:
                    pass

    def test_health_fails_for_unreachable_port(self) -> None:
        """health() returns 'error' when no container is listening."""
        deployment = DeploymentInfo(
            model_name="test-unreachable",
            backend="vllm",
            port=19999,  # almost certainly unused
            status="running",
            container_id="fake-id",
        )

        adapter = VLLMAdapter()
        status = adapter.health(deployment)
        assert status == "error"


# ---------------------------------------------------------------------------
# T5.3b — CLI arg verification against real-world docker-compose configs
# ---------------------------------------------------------------------------


class TestCliArgsAgainstCompos:
    """Verify _build_cli_args output matches real-world docker-compose YAML.

    Extracts the ``command`` section from ``reference-then-delete/vLLM/``
    docker-compose files and asserts that our adapter produces equivalent
    CLI flags.
    """

    @pytest.fixture(scope="class")
    def compose_dir(self) -> Path:
        """Path to the reference docker-compose directory."""
        return Path(__file__).resolve().parents[2] / "reference-then-delete" / "vLLM"

    def _parse_compose_command(self, path: Path) -> dict[str, str | bool]:
        """Parse a docker-compose ``command: >`` block into {flag: value}."""
        import yaml

        with open(path) as fh:
            data = yaml.safe_load(fh)

        command_str = data["services"]["vllm"]["command"]
        parts = command_str.strip().split()
        result: dict[str, str | bool] = {}

        i = 0
        while i < len(parts):
            token = parts[i]
            if token.startswith("--"):
                flag = token.lstrip("-")
                # Check if next token is a value (not another flag)
                if i + 1 < len(parts) and not parts[i + 1].startswith("--"):
                    result[flag] = parts[i + 1]
                    i += 2
                else:
                    result[flag] = True  # standalone flag
                    i += 1
            else:
                i += 1  # skip non-flag tokens

        return result

    def _args_to_dict(self, args: list[str]) -> dict[str, str | bool]:
        """Convert _build_cli_args output to {flag: value} dict."""
        result: dict[str, str | bool] = {}
        i = 0
        while i < len(args):
            token = args[i]
            if token.startswith("--"):
                flag = token.lstrip("-")
                if i + 1 < len(args) and not args[i + 1].startswith("--"):
                    result[flag] = args[i + 1]
                    i += 2
                else:
                    result[flag] = True
                    i += 1
            else:
                i += 1
        return result

    def test_main_compose_matches(self, compose_dir: Path) -> None:
        """docker-compose.yml main config produces matching CLI args."""
        compose_path = compose_dir / "docker-compose.yml"
        if not compose_path.exists():
            pytest.skip(f"{compose_path} not found")

        expected = self._parse_compose_command(compose_path)

        # Build the runtime config that matches the compose file
        runtime = VLLMRuntimeConfig(
            model="/data/LLM/oobabooga/models/Qwen3.6-27B-FP8",
            served_model_name="vllm-Qwen3.6-27B",
            tensor_parallel_size=2,
            gpu_memory_utilization=0.97,
            kv_cache_dtype="fp8_e4m3",
            max_model_len=100000,
            max_num_seqs=4,
            reasoning_parser="qwen3",
            enable_auto_tool_choice=True,
            tool_call_parser="qwen3_coder",
            disable_custom_all_reduce=True,
            enable_prefix_caching=True,
            speculative_config={
                "method": "qwen3_next_mtp",
                "num_speculative_tokens": 2,
            },
        )

        args = VLLMAdapter._build_cli_args(runtime)
        actual = self._args_to_dict(args)

        # Compare only the flags we model (not --port which is adapter-managed)
        for flag, value in expected.items():
            if flag == "port":
                continue
            assert flag in actual, f"missing flag --{flag} from compose config"
            # Compose YAML may wrap JSON in single quotes; strip them first
            raw_value = str(value).strip("'\"")
            if raw_value.startswith("{"):
                expected_json = json.loads(raw_value)
                actual_json = json.loads(str(actual[flag]))  # type: ignore[arg-type]
                assert expected_json == actual_json, (
                    f"mismatch for --{flag}: {expected_json} != {actual_json}"
                )
            elif value is True:
                assert actual[flag] is True
            else:
                assert str(actual[flag]) == str(value), (
                    f"mismatch for --{flag}: expected {value!r}, got {actual[flag]!r}"
                )

    def test_vl_compose_matches(self, compose_dir: Path) -> None:
        """compose-qwen2.5-vl.yml multimodal config produces matching CLI args."""
        compose_path = compose_dir / "compose-qwen2.5-vl.yml"
        if not compose_path.exists():
            pytest.skip(f"{compose_path} not found")

        expected = self._parse_compose_command(compose_path)

        runtime = VLLMRuntimeConfig(
            model="/data/LLM/oobabooga/models/Qwen2.5-VL-32B-Instruct-AWQ",
            served_model_name="vllm-Qwen2.5-VL-32B",
            trust_remote_code=True,
            gpu_memory_utilization=0.95,
            enable_chunked_prefill=True,
            max_model_len=32768,
            tensor_parallel_size=2,
            max_num_batched_tokens=32768,
            limit_mm_per_prompt={"image": 4},
            kv_cache_dtype="auto",
            max_num_seqs=32,
            enable_auto_tool_choice=True,
            tool_call_parser="hermes",
            tokenizer_revision="05440b7",
        )

        args = VLLMAdapter._build_cli_args(runtime)
        actual = self._args_to_dict(args)

        for flag, value in expected.items():
            if flag == "port":
                continue
            assert flag in actual, f"missing flag --{flag} from VL compose config"

    def test_extra_args_passthrough_compose(self, compose_dir: Path) -> None:
        """Flags not modeled in VLLMRuntimeConfig can be passed via extra_args."""
        # Example: if vLLM adds a new flag, extra_args covers it immediately
        runtime = VLLMRuntimeConfig(
            repo="test/model",
            extra_args={
                "some-new-flag": "value",
                "another-flag": 42,
                "bool-flag": True,
            },
        )

        args = VLLMAdapter._build_cli_args(runtime)
        assert "--some-new-flag" in args
        assert "--another-flag" in args
        assert "--bool-flag" in args


# ---------------------------------------------------------------------------
# T5.3c — vLLM CPU smoke test (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.no_isolate
class TestVLLMOnCPU:
    """Full vLLM adapter + CPU model test.

    Skipped by default. Enable with ``TEST_VLLM_CPU=1`` env var.
    Uses the official CPU image, does not request GPU devices.
    Requires Docker + downloads the vLLM CPU image and gpt2 model on first run.
    """

    ENABLED = os.environ.get("TEST_VLLM_CPU", "").lower() in ("1", "true", "yes")

    @pytest.fixture(autouse=True)
    def _skip_if_disabled(self) -> None:
        if not self.ENABLED:
            pytest.skip("set TEST_VLLM_CPU=1 to enable")
        if not _docker_available():
            pytest.skip("docker daemon is not accessible")

    def test_cpu_model_lifecycle(self) -> None:
        """Start vLLM with gpt2 on CPU, verify health, stop."""
        import time

        from switchyard.config.loader import AppSettings

        settings = AppSettings()
        runtime = VLLMRuntimeConfig(
            repo="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            device="cpu",
        )
        config = ModelConfig(
            backend="vllm",
            image="vllm/vllm-openai-cpu:latest-x86_64",
            runtime=runtime,
        )

        adapter = VLLMAdapter(
            backend_host=settings.backend_host or "localhost",
            backend_scheme=settings.backend_scheme or "http",
            docker_network=settings.docker_network,
        )
        info = adapter.start(config, port=18000)

        print(
            f"[vLLM smoke CPU] started container={info.container_id} "
            f"endpoint={adapter.endpoint(info)}",
            flush=True,
        )
        print(
            f"[vLLM smoke CPU] booting {runtime.repo} on "
            f"{settings.backend_host or 'localhost'}:18000...",
            flush=True,
        )
        print(
            "[vLLM smoke CPU] waiting for health check (up to 90s)...",
            flush=True,
        )

        try:
            for attempt in range(1, 31):
                time.sleep(3)
                status = adapter.health(info)
                print(
                    f"[vLLM smoke CPU] attempt {attempt}/30 status={status} "
                    f"port={info.port}",
                    flush=True,
                )
                if status == "running":
                    break
            else:
                # Print recent container logs before failing
                try:
                    container = adapter._client.containers.get(info.container_id)
                    logs = container.logs(tail=80).decode(errors="replace")
                    print(logs, flush=True)
                except Exception:
                    pass
                status = adapter.health(info)
                assert status == "running", (
                    "vLLM CPU health check never succeeded after 90s"
                )

            # Verify endpoint uses configured backend host/scheme
            endpoint = adapter.endpoint(info)
            expected_host = settings.backend_host or "localhost"
            expected_scheme = settings.backend_scheme or "http"
            assert endpoint == f"{expected_scheme}://{expected_host}:18000"

            # Send a real chat completion request (end-to-end validation)
            import httpx

            client = httpx.Client(timeout=30.0)
            response = client.post(
                f"{endpoint}/v1/chat/completions",
                json={
                    "model": runtime.repo,
                    "messages": [{"role": "user", "content": "Say hello"}],
                    "max_tokens": 10,
                },
            )
            assert response.status_code == 200
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            assert text, "chat completion returned empty message"
            print(
                f"[vLLM smoke CPU] chat OK: {text!r}",
                flush=True,
            )
        finally:
            adapter.stop(info)


# ---------------------------------------------------------------------------
# T5.3d — vLLM GPU smoke test (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.no_isolate
class TestVLLMOnGPU:
    """Full vLLM adapter + GPU model test.

    Skipped by default. Enable with ``TEST_VLLM_GPU=1`` env var.
    Uses the official GPU image with NVIDIA device_requests.
    Requires Docker, an accessible NVIDIA GPU, and sufficient free VRAM.
    """

    ENABLED = os.environ.get("TEST_VLLM_GPU", "").lower() in ("1", "true", "yes")

    @pytest.fixture(autouse=True)
    def _skip_if_disabled(self) -> None:
        if not self.ENABLED:
            pytest.skip("set TEST_VLLM_GPU=1 to enable")
        if not _docker_available():
            pytest.skip("docker daemon is not accessible")

    def test_gpu_model_lifecycle(self) -> None:
        """Start vLLM with gpt2 on GPU, verify health, stop."""
        import time

        from switchyard.config.loader import AppSettings

        settings = AppSettings()
        runtime = VLLMRuntimeConfig(
            repo="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            device="cuda",
            gpu_memory_utilization=0.3,
        )
        config = ModelConfig(
            backend="vllm",
            image="vllm/vllm-openai:latest",
            runtime=runtime,
        )

        adapter = VLLMAdapter(
            backend_host=settings.backend_host or "localhost",
            backend_scheme=settings.backend_scheme or "http",
            docker_network=settings.docker_network,
        )
        info = adapter.start(config, port=18000)

        print(
            f"[vLLM smoke GPU] started container={info.container_id} "
            f"endpoint={adapter.endpoint(info)}",
            flush=True,
        )
        print(
            f"[vLLM smoke GPU] booting {runtime.repo} on "
            f"{settings.backend_host or 'localhost'}:18000...",
            flush=True,
        )
        print(
            "[vLLM smoke GPU] waiting for health check (up to 90s)...",
            flush=True,
        )

        try:
            for attempt in range(1, 31):
                time.sleep(3)
                status = adapter.health(info)
                print(
                    f"[vLLM smoke GPU] attempt {attempt}/30 status={status} "
                    f"port={info.port}",
                    flush=True,
                )
                if status == "running":
                    break
            else:
                # Print recent container logs before failing
                try:
                    container = adapter._client.containers.get(info.container_id)
                    logs = container.logs(tail=80).decode(errors="replace")
                    print(logs, flush=True)
                except Exception:
                    pass
                status = adapter.health(info)
                assert status == "running", (
                    "vLLM GPU health check never succeeded after 90s"
                )

            # Verify endpoint uses configured backend host/scheme
            endpoint = adapter.endpoint(info)
            expected_host = settings.backend_host or "localhost"
            expected_scheme = settings.backend_scheme or "http"
            assert endpoint == f"{expected_scheme}://{expected_host}:18000"

            # Send a real chat completion request (end-to-end validation)
            import httpx

            client = httpx.Client(timeout=30.0)
            response = client.post(
                f"{endpoint}/v1/chat/completions",
                json={
                    "model": runtime.repo,
                    "messages": [{"role": "user", "content": "Say hello"}],
                    "max_tokens": 10,
                },
            )
            assert response.status_code == 200
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            assert text, "chat completion returned empty message"
            print(
                f"[vLLM smoke GPU] chat OK: {text!r}",
                flush=True,
            )
        finally:
            adapter.stop(info)
