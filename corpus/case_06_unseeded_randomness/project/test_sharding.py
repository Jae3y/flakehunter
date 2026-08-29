"""Sharding must spread work over every shard, not leave one idle."""

from __future__ import annotations

from app.sharding import assign_shards, shard_sizes

ITEMS = ["job-a", "job-b", "job-c", "job-d", "job-e", "job-f"]
SHARDS = 3


def test_every_shard_receives_work() -> None:
    assignment = assign_shards(ITEMS, SHARDS)
    sizes = shard_sizes(assignment, SHARDS)

    assert len(assignment) == len(ITEMS)
    assert all(size > 0 for size in sizes), f"an idle shard: {sizes}"
