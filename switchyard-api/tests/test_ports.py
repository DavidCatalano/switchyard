"""Tests for the port allocator.

Validates:
- Sequential allocation from base port
- Skipping ports that are already in use
- Releasing ports back to the pool
- Error when no ports available in range
- Thread safety of allocation/release
"""

from __future__ import annotations

import socket
import threading

import pytest

from switchyard.core.ports import PortAllocator


def _bind_port(port: int) -> socket.socket:
    """Bind a socket to a port to simulate it being in use."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    return sock


class TestPortAllocator:
    """Port allocator tests."""

    def test_allocate_from_base_port(self) -> None:
        allocator = PortAllocator(base_port=9000)
        port = allocator.allocate()
        assert port == 9000

    def test_sequential_allocation(self) -> None:
        allocator = PortAllocator(base_port=9000)
        p1 = allocator.allocate()
        p2 = allocator.allocate()
        p3 = allocator.allocate()
        assert p1 == 9000
        assert p2 == 9001
        assert p3 == 9002

    def test_release_port(self) -> None:
        allocator = PortAllocator(base_port=9000)
        port = allocator.allocate()
        assert port == 9000
        allocator.release(port)
        # Port should be available again
        port2 = allocator.allocate()
        assert port2 == 9000

    def test_skips_allocated_port(self) -> None:
        allocator = PortAllocator(base_port=9000)
        p1 = allocator.allocate()  # 9000
        p2 = allocator.allocate()  # 9001
        assert p1 == 9000
        assert p2 == 9001
        allocator.release(p1)
        # Released port reused
        p3 = allocator.allocate()
        assert p3 == 9000

    def test_release_untracked_port_ignored(self) -> None:
        """Releasing a port that wasn't allocated is silently ignored."""
        allocator = PortAllocator(base_port=9000)
        allocator.release(9999)  # should not raise

    def test_skips_in_use_system_ports(self) -> None:
        """Allocator skips ports that are externally bound."""
        sock = _bind_port(9100)
        try:
            allocator = PortAllocator(base_port=9100, max_attempts=10)
            port = allocator.allocate()
            assert port > 9100  # should skip the bound port
        finally:
            sock.close()

    def test_exhaustion_raises(self) -> None:
        """Allocator raises when max_attempts exceeded."""
        sock = _bind_port(9200)
        try:
            allocator = PortAllocator(base_port=9200, max_attempts=1)
            with pytest.raises(RuntimeError, match="no available ports"):
                allocator.allocate()
        finally:
            sock.close()

    def test_concurrent_allocations_no_duplicates(self) -> None:
        """Multiple threads can allocate without getting duplicate ports."""
        allocator = PortAllocator(base_port=9300)
        ports: list[int] = []
        lock = threading.Lock()

        def alloc() -> None:
            p = allocator.allocate()
            with lock:
                ports.append(p)

        threads = [threading.Thread(target=alloc) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ports) == 10
        assert len(set(ports)) == 10  # no duplicates

    def test_get_allocated_ports(self) -> None:
        allocator = PortAllocator(base_port=9400)
        p1 = allocator.allocate()
        p2 = allocator.allocate()
        assert allocator.allocated == {p1, p2}
        allocator.release(p1)
        assert allocator.allocated == {p2}
