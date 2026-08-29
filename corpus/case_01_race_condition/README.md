# case_01_race_condition

Two threads lose updates on a shared counter.

**Root cause class:** `race_condition`
**Difficulty:** medium
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

CPython switches threads every 5ms. A worker's loop spans several switch intervals, so two workers can read the same total and both write back the same incremented value, losing one update.

## The real fix

`add_mutual_exclusion` -- Guard the read-modify-write in `RequestCounter.record` with a `threading.Lock`, so the read of `total` and the write back are one atomic step.

Expected to touch: `app/counter.py`

## The tempting wrong fix

Run the workers sequentially, cut the iteration count until the interleaving stops happening, or relax the assertion to `<=`.

## Baseline flake rate

**33.4%** (167/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:12:32Z.
