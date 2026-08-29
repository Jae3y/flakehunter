"""Concurrent cache writes must each land intact."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app.cache import store

ENTRIES = {
    "alpha": "payload-for-alpha",
    "beta": "payload-for-beta",
}

#: Two requests arriving close together, but not simultaneously.
ARRIVAL_GAP_S = 0.00006


def test_concurrent_writes_all_land(tmp_path: Path) -> None:
    errors: list[BaseException] = []

    def write(key: str, payload: str) -> None:
        try:
            store(tmp_path / key, payload)
        except OSError as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=write, args=(key, payload))
        for key, payload in ENTRIES.items()
    ]
    for thread in threads:
        thread.start()
        time.sleep(ARRIVAL_GAP_S)
    for thread in threads:
        thread.join()

    assert not errors, f"{len(errors)} write(s) failed: {errors[0]}"
    for key, payload in ENTRIES.items():
        assert (tmp_path / key).read_text(encoding="utf-8") == payload
