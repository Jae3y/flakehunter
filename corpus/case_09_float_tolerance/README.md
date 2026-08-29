# case_09_float_tolerance

Shard totals are folded in completion order.

**Root cause class:** `float_tolerance`
**Difficulty:** hard
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

Floating-point addition is not associative. `as_completed` yields shards in whatever order the workers finish, and roughly a third of those orders total to 4.199999999999999 rather than 4.2.

## The real fix

`deterministic_accumulation` -- Accumulate in submission order and use `math.fsum`, so the total does not depend on which worker finishes first.

Expected to touch: `app/billing.py`

## The tempting wrong fix

Change the assertion to `pytest.approx`. That is a weakened test, not a fix -- the aggregate is still order-dependent.

## Baseline flake rate

**12.0%** (60/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:16:42Z.
