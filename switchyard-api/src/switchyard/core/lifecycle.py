"""Lifecycle manager for model deployments.

Orchestrates adapter start/stop, port allocation, state tracking,
and background health checks.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import docker

from switchyard.config.models import (
    Config,
    ResolvedDeployment,
)
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.docker import DockerClientFactory as DockerClientFactoryType
from switchyard.core.docker import (
    find_container_by_labels,
    get_container_host_port,
    get_container_status,
    remove_container,
)
from switchyard.core.orphan import OrphanDetector, _DockerClient
from switchyard.core.ports import PortAllocator
from switchyard.core.registry import AdapterRegistry
from switchyard.core.state import DeploymentStateManager


@dataclass
class ReconcileResult:
    """Structured result from reconcile().

    Attributes:
        info: DeploymentInfo if state exists (adopted or preserved),
              None if container was missing/exited/dead.
        container_status: Docker container observation. Use raw Docker
              status strings when available (e.g. "running", "exited",
              "dead", "created", "paused", "restarting"). Use
              Switchyard-added "missing" when no managed container exists
              and "unknown" when Docker is unreachable.
        docker_error: Error message if Docker was unreachable or an
              unrecoverable container-level issue was found,
              None otherwise.
    """

    info: DeploymentInfo | None = None
    container_status: str = "unknown"
    docker_error: str | None = None
    _adopted: bool = field(default=False, repr=False)  # internal flag
    _health_task_started: bool = field(default=False, repr=False)  # internal flag

if TYPE_CHECKING:
    from switchyard.core.docker import _DockerContainer

logger = logging.getLogger(__name__)

_DEFAULT_HEALTH_INTERVAL = 2.0  # seconds between health polls
_DEFAULT_HEALTH_TIMEOUT = 300.0  # seconds before loading->error


class LifecycleManager:
    """Manages the full lifecycle of model deployments.

    Coordinates adapter start/stop, port allocation, state tracking,
    and background health polling.
    """

    def __init__(
        self,
        registry: AdapterRegistry | None = None,
        port_allocator: PortAllocator | None = None,
        *,
        health_interval: float = _DEFAULT_HEALTH_INTERVAL,
        health_timeout: float = _DEFAULT_HEALTH_TIMEOUT,
        backend_host: str = "localhost",
        backend_scheme: str = "http",
        docker_network: str | None = None,
        docker_client_factory: DockerClientFactoryType | None = None,
    ) -> None:
        self.registry = registry or AdapterRegistry()
        self.port_allocator = port_allocator or PortAllocator()
        self.state = DeploymentStateManager()
        self._health_interval = health_interval
        self._health_timeout = health_timeout
        self._backend_host = backend_host
        self._backend_scheme = backend_scheme
        self._docker_network = docker_network
        self._docker_client_factory = docker_client_factory
        # backend name → live adapter instance (one per backend, reused)
        self._adapters: dict[str, BackendAdapter] = {}
        # model name → background health task
        self._health_tasks: dict[str, asyncio.Task[None]] = {}

    def _get_adapter(self, backend: str) -> BackendAdapter:
        """Get or create the adapter instance for a backend."""
        if backend not in self._adapters:
            self._adapters[backend] = self.registry.create(
                backend,
                backend_host=self._backend_host,
                backend_scheme=self._backend_scheme,
                docker_network=self._docker_network,
            )
        return self._adapters[backend]

    def reconcile(
        self, deployment_name: str, resolved: ResolvedDeployment,
    ) -> ReconcileResult:
        """Reconcile in-memory state with actual Docker container state.

        Looks up the Docker container by Switchyard labels and corrects
        in-memory state to match reality.

        Returns:
            ``ReconcileResult`` with ``info``, ``container_status``, and
            ``docker_error`` (when Docker is unreachable).
        """
        # No factory configured → return in-memory state as-is
        if self._docker_client_factory is None:
            try:
                return ReconcileResult(
                    info=self.state.get(deployment_name),
                    container_status="unknown",
                )
            except KeyError:
                return ReconcileResult(
                    info=None, container_status="unknown"
                )

        # Wrap all Docker calls; catch Docker SDK exceptions only
        try:
            client = self._docker_client_factory(resolved.docker_host)
            container = self._find_container(client, deployment_name)

            if container is None:
                # Container gone → clear any stale state
                self._clear_deployment(deployment_name)
                return ReconcileResult(
                    info=None, container_status="missing"
                )

            status = self._get_container_status(container)

            if status == "running":
                return self._handle_running(
                    deployment_name, container, resolved
                )

            # exited, dead, or other non-running state → clear + remove
            remove_container(container)
            self._clear_deployment(deployment_name)
            return ReconcileResult(
                info=None, container_status=status
            )
        except docker.errors.DockerException as exc:
            logger.warning(
                "reconcile: Docker operation failed for %s: %s",
                deployment_name, exc, exc_info=True,
            )
            # Preserve in-memory state, report diagnostic
            try:
                info = self.state.get(deployment_name)
            except KeyError:
                info = None
            return ReconcileResult(
                info=info,
                container_status="unknown",
                docker_error="docker operation failed during reconciliation",
            )

    def _find_container(
        self, client: docker.DockerClient, deployment_name: str,
    ) -> _DockerContainer | None:
        """Find a Switchyard-managed container by label."""
        return find_container_by_labels(client, deployment_name)

    def _get_container_status(self, container: _DockerContainer) -> str:
        """Get normalized Docker container status."""
        return get_container_status(container)

    def _handle_running(
        self,
        deployment_name: str,
        container: _DockerContainer,
        resolved: ResolvedDeployment,
    ) -> ReconcileResult:
        """Handle a running container found during reconciliation.

        Preserves existing in-memory status for ``loading``, ``running``,
        and ``error``. Treats ``stopped`` as stale (re-adopt as ``loading``).
        Adopts missing containers as ``loading`` with health polling.
        """
        existing = self.state._deployments.get(deployment_name)

        if existing is not None and existing.status in (
            "loading", "running", "error"
        ):
            # Preserve existing state — Docker running alone is not enough
            # to coerce a health-determined status.
            if existing.status == "loading":
                # Ensure a health task exists (may have been missed by
                # sync reconcile that had no event loop).
                self._ensure_health_task(deployment_name, existing)
            # "running" and "error" — do nothing. Error is terminal.
            return ReconcileResult(
                info=existing, container_status="running"
            )

        # Either no existing state or existing state is "stopped" (stale).
        # Clear stale state before adopting.
        if existing is not None:
            self._clear_deployment(deployment_name)

        # Either no existing state or existing state is "stopped" (stale).
        # Adopt the container as "loading".
        internal_port = resolved.internal_port
        port = get_container_host_port(container, internal_port)
        if port is None:
            # Can't determine host port — can't safely adopt.
            logger.warning(
                "reconcile: running container for %s has no bound host "
                "port (internal=%d) — skipping adoption",
                deployment_name, internal_port,
            )
            return ReconcileResult(
                info=None,
                container_status="running",
                docker_error=(
                    "managed container is running but has no host port "
                    "binding"
                ),
            )

        # Reserve the observed port
        try:
            self.port_allocator.allocate(port=port)
        except ValueError:
            pass  # already allocated

        info = DeploymentInfo(
            model_name=deployment_name,
            backend=resolved.backend,
            port=port,
            status="loading",
            container_id=container.short_id,
            metadata=self._build_adopted_metadata(resolved),
        )
        self.state.add(info)

        # Start health polling for the newly adopted container
        self._ensure_health_task(deployment_name, info)

        logger.info(
            "reconcile: adopted running container for %s as loading "
            "(port=%d container=%s)",
            deployment_name, port, container.short_id,
        )
        return ReconcileResult(info=info, container_status="running")

    def _build_adopted_metadata(
        self, resolved: ResolvedDeployment,
    ) -> dict[str, Any]:
        """Build metadata dict for an adopted deployment.

        Includes the fields needed by _backend_url() and
        chat_completions model rewriting.

        ``served_model_name`` comes from ``resolved.runtime_args`` (which
        already has deployment/runtime overrides applied), not from
        ``resolved.model_defaults``.
        """
        metadata: dict[str, Any] = dict(resolved.runtime_args)
        metadata["backend_host"] = resolved.backend_host
        metadata["backend_scheme"] = resolved.backend_scheme
        # health_timeout_seconds is a first-class resolved value; set it
        # after copying runtime_args so it always wins over any leaked value
        if hasattr(resolved, "health_timeout_seconds"):
            metadata["health_timeout_seconds"] = resolved.health_timeout_seconds
        # served_model_name from runtime_args is authoritative; overrides
        # take precedence over raw model_defaults
        return metadata

    def _ensure_health_task(
        self, deployment_name: str, info: DeploymentInfo,
    ) -> None:
        """Start a health poll task if one doesn't already exist.

        Safe to call from sync context: if no event loop is running,
        logs a warning and returns without creating a task.
        """
        # Check for existing non-cancelled (and not completed) task
        existing_task = self._health_tasks.get(deployment_name)
        if existing_task is not None and not existing_task.done():
            return  # task still active

        # Try to get a running event loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running (e.g. sync test context).
            # Health poll will resume on the next async reconcile call.
            logger.warning(
                "_ensure_health_task: no event loop for %s; "
                "health task will start on next async reconcile",
                deployment_name,
            )
            return

        task = loop.create_task(
            self._health_poll(deployment_name, info),
            name=f"health-{deployment_name}",
        )
        self._health_tasks[deployment_name] = task

    def _clear_deployment(self, deployment_name: str) -> None:
        """Clear in-memory state, release port, cancel health task."""
        # Cancel health task
        if deployment_name in self._health_tasks:
            self._health_tasks[deployment_name].cancel()
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._wait_for_cancellation(
                        self._health_tasks[deployment_name],
                    ))
                else:
                    # Synchronous context — just discard
                        pass
            except Exception:
                pass
            del self._health_tasks[deployment_name]

        # Remove state and release port
        try:
            info = self.state.get(deployment_name)
            self.port_allocator.release(info.port)
            self.state.remove(deployment_name)
            logger.info(
                "reconcile: cleared stale state for %s (port=%d released)",
                deployment_name, info.port,
            )
        except KeyError:
            pass  # already gone from state

    async def _wait_for_cancellation(self, task: asyncio.Task[None]) -> None:
        """Wait for a cancelled task to finish."""
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def load_model(
        self, deployment_name: str, resolved: ResolvedDeployment,
    ) -> DeploymentInfo:
        """Start a deployment.

        Allocates a port, starts the container via the backend adapter,
        records the deployment in state as ``"loading"``, and begins
        background health polling.

        Before starting, reconciles in-memory state with Docker to clear
        stale state or adopt an already-running container.

        Returns immediately (non-blocking).

        Args:
            deployment_name: Logical deployment identifier.
            resolved: Fully resolved deployment configuration.

        Returns:
            ``DeploymentInfo`` with ``status="loading"``.

        Raises:
            ValueError: If the deployment is already loaded or in error.
            RuntimeError: If Docker is unreachable (fail fast).
            KeyError: If the backend is not registered.
        """
        # Reconcile first: clear stale state or adopt running container
        result = self.reconcile(deployment_name, resolved)

        # Docker unreachable → fail fast (can't safely start)
        if result.docker_error is not None:
            raise RuntimeError(
                f"failed to reconcile Docker state before load: "
                f"{result.docker_error}"
            )

        if result.info is not None:
            if result.info.status == "running":
                raise ValueError(
                    f"deployment {deployment_name!r} is already running "
                    f"(container {result.info.container_id})"
                )
            if result.info.status == "loading":
                raise ValueError(
                    f"deployment {deployment_name!r} is already starting, "
                    f"not yet ready"
                )
            if result.info.status == "error":
                raise ValueError(
                    f"deployment {deployment_name!r} is in error state, "
                    f"unload first"
                )

        # Check for duplicates in state (should be cleared by reconcile,
        # but defensive check remains)
        if deployment_name in self.state.list_deployments():
            existing = self.state.get(deployment_name)
            raise ValueError(
                f"deployment {deployment_name!r} is already deployed "
                f"(status: {existing.status!r})"
            )

        backend = resolved.backend
        adapter = self._get_adapter(backend)

        # Allocate port
        port = self.port_allocator.allocate()

        # Start container via adapter (may raise)
        try:
            deployment = adapter.start(resolved, port)
        except Exception:
            logger.warning(
                "adapter.start() failed for deployment %s, releasing port %d",
                deployment_name, port, exc_info=True,
            )
            self.port_allocator.release(port)
            raise

        # Build metadata: start from adapter-provided values (e.g. served_model_name)
        # then overlay first-class Switchyard metadata.
        metadata: dict[str, Any] = dict(deployment.metadata)
        metadata.update(resolved.runtime_args)
        metadata["backend_host"] = resolved.backend_host
        metadata["backend_scheme"] = resolved.backend_scheme
        # health_timeout_seconds is set on ResolvedDeployment and must not
        # leak into runtime_args; set it explicitly in metadata
        if hasattr(resolved, "health_timeout_seconds"):
            metadata["health_timeout_seconds"] = resolved.health_timeout_seconds

        # Record in state as "loading"
        loading_info = DeploymentInfo(
            model_name=deployment_name,
            backend=deployment.backend,
            port=deployment.port,
            status="loading",
            container_id=deployment.container_id,
            started_at=deployment.started_at,
            metadata=metadata,
        )
        self.state.add(loading_info)

        # Spawn background health checker via idempotent helper
        self._ensure_health_task(deployment_name, loading_info)

        logger.info(
            "deployment %s loading (backend=%s port=%d container=%s)",
            deployment_name,
            backend,
            port,
            deployment.container_id,
        )
        return loading_info

    async def unload_model(self, deployment_name: str) -> None:
        """Stop and remove a deployment.

        Stops the container via the adapter, releases the port,
        cancels the background health task, and removes state.

        If the container is already gone (cleared by reconcile),
        returns idempotent success.

        Args:
            deployment_name: The deployment to unload.

        Raises:
            KeyError: If the deployment is not found in state.
        """
        # Check if deployment exists in state
        try:
            deployment = self.state.get(deployment_name)
        except KeyError:
            # Not in state — check Docker for any straggler
            # Cancel health task if it exists
            if deployment_name in self._health_tasks:
                self._health_tasks[deployment_name].cancel()
                del self._health_tasks[deployment_name]
            return  # idempotent: already unloaded

        # Cancel health poll
        if deployment_name in self._health_tasks:
            self._health_tasks[deployment_name].cancel()
            try:
                await self._health_tasks[deployment_name]
            except asyncio.CancelledError:
                pass
            del self._health_tasks[deployment_name]

        # Stop via adapter
        adapter = self._get_adapter(deployment.backend)
        adapter.stop(deployment)

        # Release port
        self.port_allocator.release(deployment.port)

        # Remove state
        self.state.remove(deployment_name)

        logger.info(
            "deployment %s unloaded (port=%d released)",
            deployment_name, deployment.port,
        )

    def get_status(self, deployment_name: str) -> str:
        """Get the current status of a deployment.

        Args:
            deployment_name: The deployment identifier.

        Returns:
            Status string (``"running"``, ``"loading"``, ``"error"``, ``"stopped"``).

        Raises:
            KeyError: If the deployment is not found.
        """
        return self.state.get(deployment_name).status

    async def _health_poll(self, deployment_name: str, initial: DeploymentInfo) -> None:
        """Background task: poll adapter health until running or error.

        Transitions the deployment from ``"loading"`` to ``"running"``
        or ``"error"`` based on adapter health responses.

        During the startup timeout window, transient health failures keep
        the deployment in ``"loading"``. After the timeout, a failed check
        transitions to ``"error"``.

        Args:
            deployment_name: The deployment being polled.
            initial: The initial deployment info from adapter.start().
        """
        adapter = self._get_adapter(initial.backend)
        poll_info = initial  # current snapshot
        # Per-deployment timeout from metadata (set by load_model or
        # reconciliation adoption), falling back to manager default.
        timeout = poll_info.metadata.get(
            "health_timeout_seconds", self._health_timeout
        )
        start_time = asyncio.get_event_loop().time()

        while True:
            await asyncio.sleep(self._health_interval)

            # Check startup timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            timed_out = elapsed >= timeout

            try:
                health_status = adapter.health(poll_info)
            except Exception:
                logger.warning(
                    "health check exception for deployment %s",
                    deployment_name, exc_info=True,
                )
                if timed_out:
                    try:
                        self.state.update_status(deployment_name, "error")
                    except KeyError:
                        return
                    break
                else:
                    continue  # still within startup window, keep polling

            if health_status == "running":
                try:
                    poll_info = self.state.update_status(deployment_name, "running")
                except KeyError:
                    return  # unloaded during poll
                logger.info("deployment %s is running", deployment_name)
                break
            else:
                if not timed_out:
                    continue  # still loading, keep polling
                try:
                    poll_info = self.state.update_status(deployment_name, "error")
                except KeyError:
                    return
                logger.error(
                    "deployment %s health check failed after %.0fs: %s",
                    deployment_name, elapsed, health_status,
                )
                break

    async def bootstrap(
        self,
        config: Config,
        docker_client: _DockerClient,
        *,
        resolve: Callable[[str], ResolvedDeployment] | None = None,
    ) -> None:
        """Run the startup bootstrap sequence.

        1. Verify Docker daemon is accessible
        2. Run orphan detection (adopt running, remove crashed)
        3. Auto-start models with ``auto_start=True``

        Orphan normalization: each running orphan from `OrphanDetector`
        is normalized to ``DeploymentInfo(status="loading")`` with
        metadata populated from the resolved deployment config.
        Orphans whose config lookup fails are skipped with a warning.

        Args:
            config: Application configuration.
            docker_client: Docker SDK client instance.
            resolve: Optional resolver callable mapping deployment
                name to ``ResolvedDeployment``. If ``None``, metadata
                is not populated but adoption still occurs.

        Raises:
            ConnectionError: If Docker daemon is not accessible.
        """
        # 1. Verify Docker connectivity
        if not docker_client.ping():
            raise ConnectionError("docker daemon is not accessible")

        # 2. Orphan detection
        detector = OrphanDetector(docker_client, config)
        results = detector.scan()

        for orphan in results.adopted:
            # Note: orphan.model_name carries the deployment name
            # (naming artifact of OrphanDetector)
            deployment_name = orphan.model_name

            # Require a resolver for proper metadata/health polling
            if resolve is None:
                logger.warning(
                    "bootstrap: skipping orphan %s — no resolver provided; "
                    "cannot populate metadata or health timeout",
                    deployment_name,
                )
                continue

            # Try to resolve deployment config for metadata
            resolved = None
            try:
                resolved = resolve(deployment_name)
            except (KeyError, ValueError) as exc:
                logger.warning(
                    "bootstrap: skipping orphan %s — "
                    "config lookup failed (deployment may have been "
                    "renamed or removed): %s",
                    deployment_name, exc,
                )
                continue

            # Build metadata from resolved config if available
            metadata: dict[str, Any] = {}
            # Use resolved.backend when available; warn on mismatch.
            # orphan.backend comes from a legacy container name pattern,
            # not from resolved config.
            backend = orphan.backend
            if resolved is not None:
                metadata = self._build_adopted_metadata(resolved)
                if orphan.backend != resolved.backend:
                    logger.warning(
                        "bootstrap: orphan %s has backend %r from container "
                        "name but config says %r; using resolved config",
                        deployment_name, orphan.backend, resolved.backend,
                    )
                backend = resolved.backend

            # Normalize to loading with resolved port/metadata
            orphan_loading = DeploymentInfo(
                model_name=deployment_name,
                backend=backend,
                port=orphan.port,
                status="loading",
                container_id=orphan.container_id,
                metadata=metadata,
            )

            # Allocate the orphan's port so allocator knows it's taken
            try:
                self.port_allocator.allocate(port=orphan.port)
            except ValueError:
                pass  # already allocated

            self.state.add(orphan_loading)

            # Start health polling for the adopted orphan
            self._ensure_health_task(deployment_name, orphan_loading)

            logger.info(
                "bootstrap: adopted orphan %s as loading (port=%d)",
                deployment_name, orphan.port,
            )

        for name in results.removed:
            logger.info("bootstrap: removed orphan %s", name)

        # 3. Auto-start is handled via deployment lifecycle (SEP-003).
        #    During migration, this loop is a stub — no auto-start behavior.
        pass  # pragma: no cover - auto-start wiring is SEP-003
    async def _wait_for_status(
        self,
        deployment_name: str,
        target: str,
        timeout: float = 10.0,
    ) -> DeploymentInfo:
        """Block until the deployment reaches the target status.

        Primarily useful for tests; not part of the public API contract.

        Args:
            deployment_name: The deployment to wait for.
            target: The desired status.
            timeout: Maximum seconds to wait.

        Returns:
            The deployment info at target status.

        Raises:
            TimeoutError: If the target status is not reached in time.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                status = self.get_status(deployment_name)
            except KeyError:
                raise TimeoutError(
                    f"deployment {deployment_name!r} disappeared "
                    f"while waiting for {target!r}"
                )
            if status == target:
                return self.state.get(deployment_name)
            await asyncio.sleep(0.1)

        raise TimeoutError(
            f"deployment {deployment_name!r} did not reach status "
            f"{target!r} within {timeout}s"
        )
