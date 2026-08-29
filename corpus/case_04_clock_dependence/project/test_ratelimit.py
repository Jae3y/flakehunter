"""Two hits in quick succession belong to the same per-second budget."""

from __future__ import annotations

import time

from app.ratelimit import RateLimiter

#: Work done between the two hits. Long enough that a second boundary lands
#: inside it a few percent of the time.
WORK_S = 0.040


def test_two_quick_hits_share_a_budget_window() -> None:
    limiter = RateLimiter(budget=10)

    first = limiter.hit()
    time.sleep(WORK_S)
    second = limiter.hit()

    assert first == 1
    assert second == 2, "the second hit landed in a different bucket"
    assert limiter.remaining() == 8
