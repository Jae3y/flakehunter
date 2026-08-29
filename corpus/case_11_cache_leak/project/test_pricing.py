"""Pricing lookups: memoisation, invalidation, and promotional rate changes."""

from __future__ import annotations

from app.pricing import price_for, set_rate

PROMOTIONAL_BASIC = 25


def test_price_lookup_is_memoised() -> None:
    """Repeated lookups must not re-read the rate card."""
    first = price_for("basic")
    second = price_for("basic")

    assert first == second
    assert price_for.cache_info().hits >= 1


def test_price_cache_can_be_cleared() -> None:
    price_for("basic")
    price_for.cache_clear()

    assert price_for.cache_info().currsize == 0


def test_promotional_rate_takes_effect() -> None:
    set_rate("basic", PROMOTIONAL_BASIC)

    assert price_for("basic") == PROMOTIONAL_BASIC
