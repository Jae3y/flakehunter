# case_12_masking_trap

The ready flag is set before the index is filled in.

**Root cause class:** `publication_ordering`
**Difficulty:** hard
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

`_run` publishes an empty dict, flips `ready`, and fills the dict afterwards. Whether a waiter sees a complete index depends on where its poll lands inside the build.

## The real fix

`publish_atomically_after_construction` -- Build the index into a local dict, assign it to `self.index`, and only then set `ready` -- ideally via a `threading.Event` set after publication. A waiter can then never observe a partial index.

Expected to touch: `app/indexer.py`

## The tempting wrong fix

Sleep before asserting, or retry the assertion until it passes. Either makes 500 runs go green while leaving the publication race exactly where it was -- a slower machine or a bigger corpus brings it straight back.

## Baseline flake rate

**3.2%** (16/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:18:20Z.

## Notes

This is the case that separates systems that verify from systems that look confident. The obvious fix removes the symptom and not the nondeterminism, and it will survive a 500-run verification.
