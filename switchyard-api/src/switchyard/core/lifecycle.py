"""Lifecycle manager for model deployments.

Orchestrates adapter start/stop, port allocation, state tracking,
and background health checks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

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
)
from switchyard.core.orphan import OrphanDetector, _DockerClient
from switchyard.core.ports import PortAllocator
from switchyard.core.registry import AdapterRegistry
from switchyard.core.state import DeploymentStateManager

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
    ) -> DeploymentInfo | None:
        """Reconcile in-memory state with actual Docker container state.

        Looks up the Docker container by Switchyard labels and corrects
        in-memory state to match reality.

        Four outcomes:
        1. Container running and already in memory → preserve state, update
           to running if needed, return DeploymentInfo.
        2. Container running and missing from memory → adopt it, reserve
           port, return DeploymentInfo.
        3. Container exited/dead → clear in-memory state, release port,
           cancel health task, return None.
        4. Container gone → clear in-memory state, release port, cancel
           health task, return None.

        Args:
            deployment_name: Logical deployment identifier.
            resolved: Fully resolved deployment configuration.

        Returns:
            ``DeploymentInfo`` if the deployment is running, ``None``
            otherwise.
        """
        # No factory configured → skip reconciliation
        if self._docker_client_factory is None:
            # If we have in-memory state, return it as-is
            try:
                return self.state.get(deployment_name)
            except KeyError:
                return None

        client = self._docker_client_factory(resolved.docker_host)
        container = self._find_container(client, deployment_name)

        if container is None:
            # Container gone → clear any stale state
            self._clear_deployment(deployment_name)
            return None

        status = self._get_container_status(container)

        if status == "running":
            return self._handle_running(deployment_name, container, resolved)

        # exited, dead, or other non-running state
        self._clear_deployment(deployment_name)
        return None

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
    ) -> DeploymentInfo:
        """Handle a running container found during reconciliation."""
        existing = self.state._deployments.get(deployment_name)

        if existing is not None:
            # Already in memory — ensure status is running
            if existing.status != "running":
                self.state.update_status(deployment_name, "running")
            return self.state.get(deployment_name)

        # Running container not in memory → adopt it
        internal_port = resolved.internal_port
        port = get_container_host_port(container, internal_port)
        if port is None:
            # Can't determine port — treat as gone
            return self._adopt_container(deployment_name, container, resolved)

        # Reserve the observed port
        try:
            self.port_allocator.allocate(port=port)
        except ValueError:
            pass  # already allocated

        info = DeploymentInfo(
            model_name=deployment_name,
            backend=resolved.backend,
            port=port,
            status="running",
            container_id=container.short_id,
            metadata=dict(resolved.runtime_args),
        )
        self.state.add(info)
        logger.info(
            "reconcile: adopted running container for %s "
            "(port=%d container=%s)",
            deployment_name, port, container.short_id,
        )
        return info

    def _adopt_container(
        self,
        deployment_name: str,
        container: _DockerContainer,
        resolved: ResolvedDeployment,
    ) -> DeploymentInfo:
        """Adopt a running container even when host port can't be resolved."""
        info = DeploymentInfo(
            model_name=deployment_name,
            backend=resolved.backend,
            port=resolved.internal_port,
            status="running",
            container_id=container.short_id,
            metadata=dict(resolved.runtime_args),
        )
        self.state.add(info)
        return info

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
            ValueError: If the deployment is already loaded.
            KeyError: If the backend is not registered.
        """
        # Reconcile first: clear stale state or adopt running container
        # Only block on "already running" if we actually found a Docker container
        reconciled = self.reconcile(deployment_name, resolved)
        if reconciled is not None and self._docker_client_factory is not None:
            # Container already running (adopted or preserved)
            raise ValueError(
                f"deployment {deployment_name!r} is already running "
                f"(container {reconciled.container_id})"
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

        # Record in state as "loading"
        loading_info = DeploymentInfo(
            model_name=deployment_name,
            backend=deployment.backend,
            port=deployment.port,
            status="loading",
            container_id=deployment.container_id,
            started_at=deployment.started_at,
            metadata=dict(deployment.metadata),
        )
        self.state.add(loading_info)

        # Spawn background health checker
        task = asyncio.create_task(
            self._health_poll(deployment_name, deployment),
            name=f"health-{deployment_name}",
        )
        self._health_tasks[deployment_name] = task

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
        start_time = asyncio.get_event_loop().time()

        while True:
            await asyncio.sleep(self._health_interval)

            # Check startup timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            timed_out = elapsed >= self._health_timeout

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
        self, config: Config, docker_client: _DockerClient,
    ) -> None:
        """Run the startup bootstrap sequence.

        1. Verify Docker daemon is accessible
        2. Run orphan detection (adopt running, remove crashed)
        3. Auto-start models with ``auto_start=True``

        Args:
            config: Application configuration.
            docker_client: Docker SDK client instance.

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
            # Allocate the orphan's port so allocator knows it's taken
            self.port_allocator.allocate(port=orphan.port)
            self.state.add(orphan)
            logger.info(
                "bootstrap: adopted orphan %s (port=%d)",
                orphan.model_name, orphan.port,
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
