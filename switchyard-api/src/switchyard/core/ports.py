"""Port allocator for backend containers.

Allocates ports sequentially from a configurable base port, skips ports
already in use on the system, and releases ports back to the pool on
container stop.
"""

from __future__ import annotations

import logging
import socket
import threading

logger = logging.getLogger(__name__)


class PortAllocator:
    """Sequential port allocator with skip-in-use and release support.

    Allocates from ``base_port`` upward, checking both internal tracking
    and actual system socket availability. Thread-safe.

    Args:
        base_port: Starting port number (default ``8000``).
        max_attempts: How many ports to try before giving up (default ``256``).
    """

    def __init__(self, base_port: int = 8000, max_attempts: int = 256) -> None:
        self._base_port = base_port
        self._max_attempts = max_attempts
        self._allocated: set[int] = set()
        self._lock = threading.Lock()

    @property
    def allocated(self) -> set[int]:
        """Return a copy of currently allocated ports."""
        return set(self._allocated)

    def allocate(self) -> int:
        """Allocate the next available port.

        Scans sequentially from ``base_port``, skipping ports that are
        already allocated internally or externally bound.

        Returns:
            An available port number.

        Raises:
            RuntimeError: If no ports are available within ``max_attempts``.
        """
        with self._lock:
            for offset in range(self._max_attempts):
                port = self._base_port + offset
                if port in self._allocated:
                    continue
                if not self._is_port_free(port):
                    logger.debug("port %d in use externally, skipping", port)
                    continue
                self._allocated.add(port)
                logger.info("allocated port %d for backend", port)
                return port

            raise RuntimeError(
                f"no available ports found in range "
                f"{self._base_port}-{self._base_port + self._max_attempts - 1}"
            )

    def release(self, port: int) -> None:
        """Release a port back to the allocation pool.

        Silently ignores ports that were never allocated.

        Args:
            port: The port number to release.
        """
        with self._lock:
            if port in self._allocated:
                self._allocated.discard(port)
                logger.info("released port %d", port)

    def _is_port_free(self, port: int) -> bool:
        """Check if a port is free on the system by attempting to bind."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
