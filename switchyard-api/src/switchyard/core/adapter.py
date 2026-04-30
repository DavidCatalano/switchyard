"""Backend adapter protocol and deployment information types.

Defines the contract that every backend adapter (vLLM, koboldcpp, etc.)
must implement, plus the DeploymentInfo dataclass that represents a
running runtime instance.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from switchyard.config.models import ModelConfig

DeploymentStatus = str
_STATUS_VALUES = ("running", "stopped", "loading", "error")


@dataclass(frozen=True)
class DeploymentInfo:
    """Immutable record describing a running (or stopping) deployment.

    Attributes:
        model_name: Logical model identifier (e.g. ``"qwen-32b"``).
        backend: Backend engine name (e.g. ``"vllm"``).
        port: Host port bound to the backend container.
        status: Current lifecycle status.
        container_id: Docker container short ID.
        started_at: Timestamp when the deployment was started.
        metadata: Arbitrary extra data carried by the adapter.
    """

    model_name: str
    backend: str
    port: int
    status: DeploymentStatus
    container_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _STATUS_VALUES:
            raise ValueError(
                f"invalid status {self.status!r}; "
                f"must be one of {_STATUS_VALUES}"
            )


class BackendAdapter(abc.ABC):
    """Abstract base class defining the backend adapter contract.

    Concrete adapters handle all Docker/container details (CLI flags,
    environment variables, port binding, health checks). The control
    plane never sees container internals.
    """

    @abc.abstractmethod
    def start(self, model_config: ModelConfig, port: int) -> DeploymentInfo:
        """Start a backend container for the given model.

        Args:
            model_config: Full model configuration (runtime, resources, etc.).
            port: Host port to bind the container to.

        Returns:
            DeploymentInfo describing the running deployment.

        Raises:
            RuntimeError: If the container fails to start.
        """
        ...

    @abc.abstractmethod
    def stop(self, deployment: DeploymentInfo) -> None:
        """Stop and remove the container for a deployment.

        Args:
            deployment: The deployment to stop.
        """
        ...

    @abc.abstractmethod
    def health(self, deployment: DeploymentInfo) -> str:
        """Check the health of a running deployment.

        Args:
            deployment: The deployment to check.

        Returns:
            A status string (e.g. ``"running"`` or ``"error"``).
        """
        ...

    @abc.abstractmethod
    def endpoint(self, deployment: DeploymentInfo) -> str:
        """Return the HTTP endpoint URL for a deployment.

        Args:
            deployment: The deployment to get the endpoint for.

        Returns:
            Base URL (e.g. ``"http://localhost:8001"``).
        """
        ...
