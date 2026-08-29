"""Background index construction.

The index is expensive to build, so callers start it and carry on, then wait
for it when they need the result.
"""

from __future__ import annotations

import threading

#: Documents in the corpus being indexed.
DOCUMENT_COUNT = 10_500


class BackgroundIndexer:
    """Builds a search index on a worker thread."""

    def __init__(self) -> None:
        self.index: dict[str, int] | None = None
        self.ready = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Begin building the index."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """Build and publish the index."""
        self.index = {}
        for number in range(DOCUMENT_COUNT):
            self.index[f"doc-{number}"] = number * 2
        self.ready = True

    def wait_until_ready(self, timeout_s: float = 2.0) -> bool:
        """Block until the indexer reports the index available."""
        tick = threading.Event()
        waited = 0.0
        step = 0.001
        while not self.ready and waited < timeout_s:
            tick.wait(step)
            waited += step
        return self.ready
