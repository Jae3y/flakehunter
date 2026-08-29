"""Helpers for standing up a listening service on a free port.

Ports are allocated from a fixed range rather than by asking the OS for an
ephemeral port, so that the service's address is predictable for local tooling
and firewall rules.
"""

from __future__ import annotations

import socket

#: Start of the range services are allocated from.
BASE_PORT = 45000

#: How far up the range to scan before giving up.
SCAN_WIDTH = 64


def find_free_port(start: int = BASE_PORT) -> int:
    """Return the first port in the range that nothing is listening on.

    Each candidate is tested by binding a probe socket and closing it again,
    so the answer describes the moment the probe ran and not the moment the
    caller gets around to using it.
    """
    for port in range(start, start + SCAN_WIDTH):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            probe.close()
        return port
    raise RuntimeError(f"no free port in {start}..{start + SCAN_WIDTH}")


class Service:
    """A listening socket on a port from the configured range."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.port: int | None = None
        self._sock: socket.socket | None = None

    def start(self) -> int:
        """Find a port, prepare the socket, then bind and listen."""
        self.port = find_free_port()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self._sock.bind(("127.0.0.1", self.port))
        self._sock.listen(1)
        return self.port

    def stop(self) -> None:
        """Close the listening socket."""
        if self._sock is not None:
            self._sock.close()
            self._sock = None
