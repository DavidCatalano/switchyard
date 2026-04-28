"""Adapter registry for backend adapters.

Maps backend names (e.g. ``"vllm"``) to adapter classes and provides
a factory to instantiate them with optional constructor arguments.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from switchyard.core.adapter import BackendAdapter

TAdapter = TypeVar("TAdapter", bound=BackendAdapter)

BackendFactory = Callable[..., BackendAdapter]


class AdapterRegistry:
    """Registry mapping backend names to adapter classes.

    Provides ``register``, ``get``, and ``create`` operations.
    Raises ``KeyError`` on unknown backend names.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, type[BackendAdapter]] = {}

    def register(self, backend: str, adapter_class: type[BackendAdapter]) -> None:
        """Register an adapter class for a backend name.

        Args:
            backend: Backend identifier (e.g. ``"vllm"``).
            adapter_class: Concrete ``BackendAdapter`` subclass.
        """
        self._adapters[backend] = adapter_class

    def get(self, backend: str) -> type[BackendAdapter]:
        """Look up the adapter class for a backend name.

        Args:
            backend: Backend identifier.

        Returns:
            The registered adapter class.

        Raises:
            KeyError: If the backend is not registered.
        """
        try:
            return self._adapters[backend]
        except KeyError:
            raise KeyError(
                f"unknown backend {backend!r}; "
                f"registered backends: {sorted(self._adapters)}"
            ) from None

    def create(self, backend: str, **kwargs: Any) -> BackendAdapter:
        """Instantiate an adapter for a backend name.

        Args:
            backend: Backend identifier.
            **kwargs: Extra constructor arguments forwarded to the adapter.

        Returns:
            A new adapter instance.

        Raises:
            KeyError: If the backend is not registered.
        """
        adapter_class = self.get(backend)
        return adapter_class(**kwargs)

    def list_backends(self) -> list[str]:
        """Return a sorted list of registered backend names."""
        return sorted(self._adapters)
