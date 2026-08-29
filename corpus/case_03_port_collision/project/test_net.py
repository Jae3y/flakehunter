"""Two services starting at once must not land on the same port."""

from __future__ import annotations

import threading

from app.net import Service

NAMES = ("api", "worker")


def test_concurrent_services_get_distinct_ports() -> None:
    services = [Service(name) for name in NAMES]
    errors: list[BaseException] = []
    ports: list[int] = []
    lock = threading.Lock()
    ready = threading.Barrier(len(services))

    def start(service: Service) -> None:
        ready.wait()
        try:
            port = service.start()
        except OSError as exc:
            with lock:
                errors.append(exc)
        else:
            with lock:
                ports.append(port)

    threads = [threading.Thread(target=start, args=(s,)) for s in services]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert not errors, f"bind failed for {len(errors)} service(s): {errors[0]}"
        assert len(set(ports)) == len(NAMES), f"ports collided: {sorted(ports)}"
    finally:
        for service in services:
            service.stop()
