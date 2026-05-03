"""Lifecycle manager for model deployments.

Orchestrates adapter start/stop, port allocation, state tracking,
and background health checks.
"""

from __future__ import annotations

import asyncio
import logging

from switchyard.config.models import (
    Config,
    ResolvedDeployment,
)
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.orphan import OrphanDetector, _DockerClient
from switchyard.core.ports import PortAllocator
from switchyard.core.registry import AdapterRegistry
from switchyard.core.state import DeploymentStateManager

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
    ) -> None:
        self.registry = registry or AdapterRegistry()
        self.port_allocator = port_allocator or PortAllocator()
        self.state = DeploymentStateManager()
        self._health_interval = health_interval
        self._health_timeout = health_timeout
        self._backend_host = backend_host
        self._backend_scheme = backend_scheme
        self._docker_network = docker_network
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

    async def load_model(
        self, deployment_name: str, resolved: ResolvedDeployment,
    ) -> DeploymentInfo:
        """Start a deployment.

        Allocates a port, starts the container via the backend adapter,
        records the deployment in state as ``"loading"``, and begins
        background health polling.

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
        # Check for duplicates
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

        Args:
            deployment_name: The deployment to unload.

        Raises:
            KeyError: If the deployment is not found in state.
        """
        deployment = self.state.get(deployment_name)

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
