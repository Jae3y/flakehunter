# case_05_hash_iteration_order

Config sources live in a set, so precedence is arbitrary.

**Root cause class:** `hash_iteration_order`
**Difficulty:** hard
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

`resolve` returns the first enabled source that defines the setting, iterating a set. Set order for these objects derives from identity hashing, which varies with each process's memory layout. Four sources agree on 'production'; one stale legacy source still reports 'staging' and wins whenever it lands first.

## The real fix

`impose_deterministic_ordering` -- Give sources an explicit precedence: resolve over an ordered sequence, or sort by a declared priority, rather than iterating a set.

Expected to touch: `app/config.py`

## The tempting wrong fix

Pin `PYTHONHASHSEED` for the test run, or drop the legacy source from the fixture. Both leave `resolve` picking arbitrarily in production.

## Baseline flake rate

**2.2%** (11/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:14:36Z.

## Notes

One of the two lowest-rate cases. Uniform ordering would fail 20% of the time; identity hashing is biased enough that it fails far less, which is exactly what makes this kind of bug survive review.
