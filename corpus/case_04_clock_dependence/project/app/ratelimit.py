"""A per-second request budget."""

from __future__ import annotations

import time


def current_bucket() -> int:
    """The wall-clock second the caller is currently in."""
    return int(time.time())


class RateLimiter:
    """Counts hits into whole-second buckets taken from the wall clock."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self._counts: dict[int, int] = {}

    def hit(self) -> int:
        """Record one hit and return the count within the current second."""
        bucket = current_bucket()
        self._counts[bucket] = self._counts.get(bucket, 0) + 1
        return self._counts[bucket]

    def remaining(self) -> int:
        """Budget left in the current second."""
        return self.budget - self._counts.get(current_bucket(), 0)
