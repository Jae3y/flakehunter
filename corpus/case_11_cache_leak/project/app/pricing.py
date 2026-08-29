"""Subscription pricing, memoised because it is read on every request."""

from __future__ import annotations

import functools

#: The live rate card. Rates can be adjusted at runtime for promotions.
_RATES: dict[str, int] = {"basic": 10, "pro": 40, "enterprise": 120}


def set_rate(tier: str, amount: int) -> None:
    """Adjust the rate for ``tier``."""
    _RATES[tier] = amount


@functools.lru_cache(maxsize=None)
def price_for(tier: str) -> int:
    """The current price for ``tier``.

    Memoised: pricing is read on every request and the rate card rarely
    changes.
    """
    return _RATES[tier]
