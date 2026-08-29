"""The counter must not lose requests when workers run concurrently."""

from __future__ import annotations

import threading

from app.counter import RequestCounter

WORKERS = 8

#: Enough iterations that a worker's loop spans several GIL switch intervals.
#: CPython switches threads every 5 ms by default; a loop of a few thousand
#: increments finishes inside one slice, so the workers run to completion one
#: after another and the interleaving that loses updates never happens.
REQUESTS_PER_WORKER = 50_000


def test_no_requests_are_lost_under_concurrency() -> None:
    counter = RequestCounter()

    def worker() -> None:
        for _ in range(REQUESTS_PER_WORKER):
            counter.record("/health")

    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert counter.total == WORKERS * REQUESTS_PER_WORKER
