"""Revenue totals are invoiced, so the figure is asserted exactly."""

from __future__ import annotations

from app.billing import total_revenue

#: Four shards of comparable size, so they finish at comparable times and the
#: order they are folded into the running total varies between runs.
PADDING = [0.0] * 1500

SHARDS = [
    [0.1, *PADDING],
    [0.7, *PADDING],
    [1.1, *PADDING],
    [2.3, *PADDING],
]

#: The invoiced total. Exact under `math.fsum`; half the accumulation orders
#: land on 4.199999999999999 instead.
EXPECTED = 4.2


def test_total_revenue_matches_the_invoiced_figure() -> None:
    assert total_revenue(SHARDS) == EXPECTED
