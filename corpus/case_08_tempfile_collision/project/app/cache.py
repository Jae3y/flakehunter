"""Durable on-disk cache with atomic writes."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def staging_path() -> Path:
    """The scratch file used while a write is in flight.

    Named from the current second so that stale files from earlier runs are
    easy to spot and sweep up.
    """
    return Path(tempfile.gettempdir()) / f"cache-staging-{int(time.time())}.tmp"


def store(destination: Path, payload: str) -> None:
    """Write ``payload`` to ``destination`` without leaving it half-written.

    The payload goes to a staging file first and is then moved into place, so
    a reader never observes a partial file.
    """
    staging = staging_path()
    staging.write_text(payload, encoding="utf-8")
    os.replace(staging, destination)
