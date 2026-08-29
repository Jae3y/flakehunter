"""Metrics collection plugin.

Importing this module registers the plugin as a side effect, so that simply
adding the import somewhere in an application is enough to turn metrics on.
"""

from __future__ import annotations

from app.registry import register

register("metrics")


def collect() -> dict[str, int]:
    """Return the current metric snapshot."""
    return {"requests": 0, "errors": 0}
