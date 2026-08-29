# case_06_unseeded_randomness

Shard assignment is drawn from the global RNG.

**Root cause class:** `unseeded_randomness`
**Difficulty:** easy
**A symptom-masking fix is available:** yes

## Where the nondeterminism comes from

`random.randrange` draws from the process-wide RNG, seeded from the OS at startup, so the layout differs on every call.

## The real fix

`make_the_algorithm_deterministic` -- Assign shards by a stable hash of the item -- e.g. `zlib.crc32(item.encode()) % shard_count` -- or take an injected `random.Random` the caller seeds.

Expected to touch: `app/sharding.py`

## The tempting wrong fix

Call `random.seed(0)` in the test. The assignment becomes repeatable for the test only; production stays unbalanced.

## Baseline flake rate

**28.0%** (140/500 runs, 8 workers, 1 distinct failure signature(s)), measured 2026-08-29T00:15:05Z.
