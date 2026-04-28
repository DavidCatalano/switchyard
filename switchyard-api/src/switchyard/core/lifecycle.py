"""Lifecycle manager for model deployments.

Orchestrates adapter start/stop, port allocation, state tracking,
and background health checks.
"""

from __future__ import annotations

import asyncio
import logging

from switchyard.config.models import ModelConfig
from switchyard.core.adapter import BackendAdapter, DeploymentInfo
from switchyard.core.ports import PortAllocator
from switchyard.core.registry import AdapterRegistry
from switchyard.core.state import DeploymentStateManager

logger = logging.getLogger(__name__)

_DEFAULT_HEALTH_INTERVAL = 2.0  # seconds between health polls


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
    ) -> None:
        self.registry = registry or AdapterRegistry()
        self.port_allocator = port_allocator or PortAllocator()
        self.state = DeploymentStateManager()
        self._health_interval = health_interval
        # backend name → live adapter instance (one per backend, reused)
        self._adapters: dict[str, BackendAdapter] = {}
        # model name → background health task
        self._health_tasks: dict[str, asyncio.Task[None]] = {}

    def _get_adapter(self, backend: str) -> BackendAdapter:
        """Get or create the adapter instance for a backend."""
        if backend not in self._adapters:
            self._adapters[backend] = self.registry.create(backend)
        return self._adapters[backend]

    async def load_model(
        self, model_name: str, model_config: ModelConfig,
    ) -> DeploymentInfo:
        """Start a model deployment.

        Allocates a port, starts the container via the backend adapter,
        records the deployment in state as ``"loading"``, and begins
        background health polling.

        Returns immediately (non-blocking).

        Args:
            model_name: Logical model identifier.
            model_config: Full model configuration.

        Returns:
            ``DeploymentInfo`` with ``status="loading"``.

        Raises:
            ValueError: If the model is already deployed.
            KeyError: If the backend is not registered.
        """
        # Check for duplicates
        if model_name in self.state.list_deployments():
            existing = self.state.get(model_name)
            raise ValueError(
                f"model {model_name!r} is already deployed "
                f"(status: {existing.status!r})"
            )

        backend = model_config.backend
        adapter = self._get_adapter(backend)

        # Allocate port
        port = self.port_allocator.allocate()

        # Start container via adapter
        deployment = adapter.start(model_config, port)

        # Record in state as "loading"
        loading_info = DeploymentInfo(
            model_name=model_name,
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
            self._health_poll(model_name, deployment),
            name=f"health-{model_name}",
        )
        self._health_tasks[model_name] = task

        logger.info(
            "model %s loading (backend=%s port=%d container=%s)",
            model_name,
            backend,
            port,
            deployment.container_id,
        )
        return loading_info

    async def unload_model(self, model_name: str) -> None:
        """Stop and remove a model deployment.

        Stops the container via the adapter, releases the port,
        cancels the background health task, and removes state.

        Args:
            model_name: The model to unload.

        Raises:
            KeyError: If the model is not found in state.
        """
        deployment = self.state.get(model_name)

        # Cancel health poll
        if model_name in self._health_tasks:
            self._health_tasks[model_name].cancel()
            try:
                await self._health_tasks[model_name]
            except asyncio.CancelledError:
                pass
            del self._health_tasks[model_name]

        # Stop via adapter
        adapter = self._get_adapter(deployment.backend)
        adapter.stop(deployment)

        # Release port
        self.port_allocator.release(deployment.port)

        # Remove state
        self.state.remove(model_name)

        logger.info("model %s unloaded (port=%d released)", model_name, deployment.port)

    def get_status(self, model_name: str) -> str:
        """Get the current status of a model deployment.

        Args:
            model_name: The model identifier.

        Returns:
            Status string (``"running"``, ``"loading"``, ``"error"``, ``"stopped"``).

        Raises:
            KeyError: If the model is not found.
        """
        return self.state.get(model_name).status

    async def _health_poll(self, model_name: str, initial: DeploymentInfo) -> None:
        """Background task: poll adapter health until running or error.

        Transitions the deployment from ``"loading"`` to ``"running"``
        or ``"error"`` based on adapter health responses.

        Args:
            model_name: The model being polled.
            initial: The initial deployment info from adapter.start().
        """
        adapter = self._get_adapter(initial.backend)
        poll_info = initial  # current snapshot
        while True:
            await asyncio.sleep(self._health_interval)
            try:
                health_status = adapter.health(poll_info)
            except Exception:
                logger.warning(
                    "health check exception for model %s",
                    model_name, exc_info=True,
                )
                try:
                    self.state.update_status(model_name, "error")
                except KeyError:
                    # Model was unloaded during poll
                    return
                break

            if health_status == "running":
                try:
                    poll_info = self.state.update_status(model_name, "running")
                except KeyError:
                    return  # unloaded during poll
                logger.info("model %s is running", model_name)
                break
            else:
                try:
                    poll_info = self.state.update_status(model_name, "error")
                except KeyError:
                    return
                logger.error(
                    "model %s health check failed: %s",
                    model_name, health_status,
                )
                break

    async def _wait_for_status(
        self,
        model_name: str,
        target: str,
        timeout: float = 10.0,
    ) -> DeploymentInfo:
        """Block until the model reaches the target status.

        Primarily useful for tests; not part of the public API contract.

        Args:
            model_name: The model to wait for.
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
                status = self.get_status(model_name)
            except KeyError:
                raise TimeoutError(
                    f"model {model_name!r} disappeared while waiting for {target!r}"
                )
            if status == target:
                return self.state.get(model_name)
            await asyncio.sleep(0.1)

        raise TimeoutError(
            f"model {model_name!r} did not reach status {target!r} within {timeout}s"
        )
