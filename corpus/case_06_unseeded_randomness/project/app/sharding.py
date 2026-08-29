"""Distribute work items across shards."""

from __future__ import annotations

import random


def assign_shards(items: list[str], shard_count: int) -> dict[str, int]:
    """Assign each item to a shard.

    Assignment is drawn from the process-wide ``random`` module, so the same
    inputs produce a different layout on every call.
    """
    return {item: random.randrange(shard_count) for item in items}


def shard_sizes(assignment: dict[str, int], shard_count: int) -> list[int]:
    """How many items landed in each shard."""
    sizes = [0] * shard_count
    for shard in assignment.values():
        sizes[shard] += 1
    return sizes
