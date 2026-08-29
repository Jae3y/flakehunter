# case_02_test_order_dependency

Importing the metrics plugin registers it as a side effect.

**Root cause class:** `test_order_dependency`
**Difficulty:** medium
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

conftest.py orders tests by `hash()` of their name. Python randomises string hashing per process, so collection order genuinely differs between runs with no use of `random`.

## The real fix

`remove_import_time_side_effect` -- Delete the module-level `register('metrics')` call in `app/metrics.py` and expose an explicit `install()` the application calls.

Expected to touch: `app/metrics.py`

## The tempting wrong fix

Delete or neuter the shuffling conftest so the order is fixed, or add a reset fixture to the test file alone. Both leave the import-time side effect in place.

## Baseline flake rate

**32.8%** (164/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:13:02Z.
