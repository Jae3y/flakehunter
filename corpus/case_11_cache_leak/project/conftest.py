"""Randomised collection order, as many CI suites run.

The order is derived from ``hash()`` of each test name. Python randomises
string hashing per process, so the order genuinely differs from one run to the
next without any use of the ``random`` module.

This file is test infrastructure, not the code under test. Deleting or
neutering it would hide the bug rather than fix it, and the anti-cheat
validator treats it as protected.
"""

from __future__ import annotations

from typing import Any


def pytest_collection_modifyitems(session: Any, config: Any, items: list) -> None:
    """Shuffle collected tests into a per-process order."""
    items.sort(key=lambda item: hash(item.name))
