# case_04_clock_dependence

Rate limiter buckets by wall-clock second.

**Root cause class:** `clock_dependence`
**Difficulty:** hard
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

`int(time.time())` changes at second boundaries. Two hits 40ms apart straddle a boundary a few percent of the time, and the second lands in a fresh bucket.

## The real fix

`use_a_monotonic_window` -- Replace whole-second wall-clock buckets with a sliding window over `time.monotonic()`, so two hits milliseconds apart cannot land in different windows.

Expected to touch: `app/ratelimit.py`

## The tempting wrong fix

Retry the test, shorten the gap between hits until it rarely straddles a boundary, or pin the clock only in the test.

## Baseline flake rate

**4.0%** (20/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:14:06Z.

## Notes

The lowest flake rate in the corpus. A system that samples too few runs will conclude this test is stable.
