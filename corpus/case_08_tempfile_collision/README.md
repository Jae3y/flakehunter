# case_08_tempfile_collision

Staging file name is predictable, so writers collide.

**Root cause class:** `tempfile_collision`
**Difficulty:** easy
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

The staging path is derived from the current second, so every concurrent writer in that second picks the same file. Whether that corrupts a write depends on the interleaving.

## The real fix

`use_unique_temp_names` -- Create the staging file with `tempfile.mkstemp` in the destination's directory, so every writer gets a private path and the rename stays on one filesystem.

Expected to touch: `app/cache.py`

## The tempting wrong fix

Retry on failure, or serialise the writes in the test with a lock.

## Baseline flake rate

**5.4%** (27/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:16:09Z.
