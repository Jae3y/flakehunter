"""Revenue aggregation across billing shards."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed


def shard_total(amounts: list[float]) -> float:
    """Sum one shard's line items."""
    total = 0.0
    for amount in amounts:
        total += amount
    return total


def total_revenue(shards: list[list[float]]) -> float:
    """Sum every shard, accumulating results as the workers finish.

    Shards are summed in parallel and folded into the running total in
    whatever order they complete.
    """
    total = 0.0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(shard_total, shard) for shard in shards]
        for future in as_completed(futures):
            total += future.result()
    return total
