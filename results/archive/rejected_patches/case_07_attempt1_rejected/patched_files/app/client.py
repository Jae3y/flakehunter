"""Client for the internal status service."""

from __future__ import annotations

import socket

#: How long to wait for the service before giving up.
TIMEOUT_S = 5.0


class StatusClient:
    """Fetches a status line from the service over TCP.

    A single attempt is made. Whatever the network or the peer's scheduler
    happens to be doing at that moment decides whether the call succeeds.
    """

    def __init__(self, host: str, port: int, timeout_s: float = TIMEOUT_S) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s

    def fetch_status(self) -> bytes:
        """Return the service's status line.

        Raises:
            OSError: On connect failure or timeout.
        """
        sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout_s
        )
        try:
            sock.settimeout(self.timeout_s)
            sock.sendall(b"STATUS\n")
            return sock.recv(64)
        finally:
            sock.close()
