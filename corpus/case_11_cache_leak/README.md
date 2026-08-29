# case_11_cache_leak

lru_cache holds a price across a rate change.

**Root cause class:** `cache_leak`
**Difficulty:** medium
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

Collection order is derived from per-process string hashing. When the memoisation test runs first it warms the cache with the old price, and the promotional test then reads a stale value.

## The real fix

`invalidate_cache_on_mutation` -- Have `set_rate` invalidate the memo -- `price_for.cache_clear()` -- so a rate change is visible to later lookups.

Expected to touch: `app/pricing.py`

## The tempting wrong fix

Add an autouse fixture that clears the cache between tests. The suite goes green; production still serves stale prices after a promotion.

## Baseline flake rate

**31.6%** (158/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:17:48Z.
