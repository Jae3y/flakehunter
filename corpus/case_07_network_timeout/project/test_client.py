"""The client must get a status back from a service that is up."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from app.client import StatusClient

#: The service's own processing time. Comfortably under the client's timeout,
#: but close enough that ordinary scheduling jitter can push a reply past it.
SERVICE_WORK_S = 0.00475


@pytest.fixture()
def status_service():
    """A local status service on an ephemeral port."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.05)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            with conn:
                try:
                    conn.recv(64)
                    time.sleep(SERVICE_WORK_S)
                    conn.sendall(b"OK\n")
                except OSError:
                    pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()
    finally:
        stop.set()
        thread.join(timeout=1.0)
        listener.close()


def test_status_is_fetched_from_a_healthy_service(status_service) -> None:
    host, port = status_service
    client = StatusClient(host, port)

    assert client.fetch_status() == b"OK\n"
