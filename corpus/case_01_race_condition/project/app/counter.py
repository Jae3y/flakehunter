"""Request accounting for a small service."""

from __future__ import annotations


class RequestCounter:
    """Counts requests seen across worker threads.

    The counter is shared by every worker. ``record`` reads the current total
    and writes back the incremented value as two separate operations, with no
    lock between them.
    """

    def __init__(self) -> None:
        self.total = 0
        self.by_route: dict[str, int] = {}

    def record(self, route: str) -> None:
        """Record one request against ``route``."""
        current = self.total
        seen = self.by_route.get(route, 0)
        self.by_route[route] = seen + 1
        self.total = current + 1
