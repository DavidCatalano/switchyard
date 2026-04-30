"""In-memory deployment state manager.

Tracks running deployments, model-to-deployment mappings, and port
assignments. State is lost on restart; orphan detection recovers
running containers.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from switchyard.core.adapter import _STATUS_VALUES, DeploymentInfo

logger = logging.getLogger(__name__)


class DeploymentStateManager:
    """In-memory store for deployment state.

    Maintains model → ``DeploymentInfo`` mappings and supports status
    transitions, port lookups, and bulk listing.
    """

    def __init__(self) -> None:
        self._deployments: dict[str, DeploymentInfo] = {}

    def add(self, info: DeploymentInfo, *, overwrite: bool = False) -> None:
        """Record a deployment.

        Args:
            info: The deployment info to record.
            overwrite: If ``True``, replace an existing entry for the same
                model. Defaults to ``False``.

        Raises:
            ValueError: If the model is already deployed and ``overwrite``
                is ``False``.
        """
        model_name = info.model_name
        if model_name in self._deployments and not overwrite:
            raise ValueError(
                f"model {model_name!r} is already deployed "
                f"(status: {self._deployments[model_name].status!r})"
            )
        self._deployments[model_name] = info
        logger.info(
            "registered deployment model=%s backend=%s port=%d status=%s",
            model_name,
            info.backend,
            info.port,
            info.status,
        )

    def get(self, model_name: str) -> DeploymentInfo:
        """Look up deployment info by model name.

        Args:
            model_name: The logical model identifier.

        Returns:
            The deployment info.

        Raises:
            KeyError: If the model is not found.
        """
        try:
            return self._deployments[model_name]
        except KeyError:
            raise KeyError(
                f"model {model_name!r} not found in deployment state"
            ) from None

    def get_by_port(self, port: int) -> DeploymentInfo:
        """Look up deployment info by host port.

        Args:
            port: The host port bound to a backend container.

        Returns:
            The deployment info.

        Raises:
            KeyError: If no deployment uses the given port.
        """
        for info in self._deployments.values():
            if info.port == port:
                return info
        raise KeyError(f"no deployment found for port {port}")

    def remove(self, model_name: str) -> None:
        """Remove a deployment from state.

        Args:
            model_name: The model to remove.

        Raises:
            KeyError: If the model is not found.
        """
        if model_name not in self._deployments:
            raise KeyError(
                f"model {model_name!r} not found in deployment state"
            )
        removed = self._deployments.pop(model_name)
        logger.info(
            "removed deployment model=%s port=%d", model_name, removed.port
        )

    def update_status(self, model_name: str, status: str) -> DeploymentInfo:
        """Update the status of a deployment.

        Since ``DeploymentInfo`` is frozen, this replaces the entry with
        a new instance reflecting the updated status.

        Args:
            model_name: The model whose status to update.
            status: The new status value.

        Returns:
            The updated deployment info.

        Raises:
            KeyError: If the model is not found.
            ValueError: If the status is not a valid value.
        """
        if status not in _STATUS_VALUES:
            raise ValueError(
                f"invalid status {status!r}; must be one of {_STATUS_VALUES}"
            )
        old = self.get(model_name)
        # Reconstruct with updated status (dataclass frozen, so replace)
        new_info = DeploymentInfo(
            model_name=old.model_name,
            backend=old.backend,
            port=old.port,
            status=status,
            container_id=old.container_id,
            started_at=old.started_at,
            metadata=dict(old.metadata),
        )
        self._deployments[model_name] = new_info
        logger.info(
            "status changed model=%s %s -> %s",
            model_name,
            old.status,
            status,
        )
        return new_info

    def list_deployments(self) -> Mapping[str, DeploymentInfo]:
        """Return all current deployments as a read-only mapping.

        Returns:
            ``{model_name: DeploymentInfo, ...}``
        """
        return MappingProxyType(dict(self._deployments))

    def status_counts(self) -> dict[str, int]:
        """Count deployments grouped by status.

        Returns:
            ``{status: count, ...}`` for all four valid statuses.
        """
        counts = dict.fromkeys(_STATUS_VALUES, 0)
        for info in self._deployments.values():
            counts[info.status] = counts[info.status] + 1
        return counts


# Avoid importing at module level to keep types clean
from types import MappingProxyType  # noqa: E402
