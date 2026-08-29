# case_10_async_ordering

Panels are collected in completion order, not layout order.

**Root cause class:** `async_ordering`
**Difficulty:** easy
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

`asyncio.as_completed` yields whichever executor thread finishes first, and equal-sized CPU work finishes in scheduling order.

## The real fix

`preserve_submission_order` -- Use `asyncio.gather(*tasks)`, which returns results in submission order regardless of completion order.

Expected to touch: `app/pipeline.py`

## The tempting wrong fix

Sort the results in the test, or compare as sets. Both weaken the assertion that order is part of the output.

## Baseline flake rate

**27.0%** (135/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:17:18Z.
