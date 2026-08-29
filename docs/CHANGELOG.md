# Improvement Changelog

Appended as work happens, with real measurements. Never written retroactively.
Entries that record a failure or a removed experiment are as valuable as the
ones that record a win.

---

## 001 — Phase 0: sandbox boundary and trajectory capture

**Date:** 2026-08-28
**Status:** built, awaiting gate measurements

### What was built

`src/telemetry/tracer.py` and `src/sandbox/executor.py`, plus the container
they run in and a smoke case (`corpus/case_00_smoke`) to exercise them.

### Decisions and why

**The container is the isolation boundary, not the unit of execution.**
Arithmetic drove this. One full evaluation needs roughly 26,000 test runs
(6,000 baseline × 500, 6,000 final verification, ~2,400 CONFIRM, ~12,000
EXPERIMENT). At ~0.5 s of `docker run` startup, container-per-execution costs
~3.6 hours of pure overhead per evaluation, before a single assertion is
checked. Instead, one long-lived container is the boundary — agent-authored
code cannot reach the host — and each run inside it is a short-lived child
process in a RAM-backed scratch workdir under `RLIMIT_CPU`, `RLIMIT_AS`,
`RLIMIT_NOFILE` and a wall-clock timeout.

**The sandbox boundary is asserted, not assumed.** The Dockerfile writes
`/.flakehunter-sandbox`; `assert_sandboxed()` refuses to execute without it.
A stray invocation from the host fails loudly rather than quietly running
agent-authored code on the developer's machine.

**Two execution strategies, and a check that stops the fast one being
misused.** `SPAWN` is a full pytest subprocess (~correct for every case).
`FORK` forks from a pytest-warmed parent (~10× cheaper), but a forked child
inherits its parent's hash seed, so `PYTHONHASHSEED`-driven flakiness becomes
invisible. `strategy_preserves_hash_order()` states that limitation in code,
and the gate measures it rather than trusting the claim.

**Unknown cost is reported as `None`, not `0.0`.** `PRICING_USD_PER_MTOK` ships
empty at Phase 0 because there are no LLM calls yet. Inventing rates would put
fabricated numbers in the results table's cost column.

**`ERROR` is distinct from `FAIL`.** pytest exit code 5 (nothing collected)
means the harness was pointed at nothing. Folding that into the flake rate
would let a broken measurement report a perfect result.

### Resolved: trajectory write-failure policy

The provisional fail-loud stub is replaced with **retry → gap marker →
escalate**, decided by the maintainer:

1. Retry a failed write up to 3 times with exponential backoff (50 ms base).
   Serialisation failures skip the retry — they are deterministic, so the same
   object fails identically every time.
2. If it still will not persist, write a **synthetic gap marker** at that
   `turn_id`. The marker is itself schema-valid and occupies the failed
   record's slot, so the sequence stays contiguous and the file stays
   parseable. A dropped record would leave a hole a reader would mistake for a
   turn that never happened.
3. Escalate to `TraceWriteError` only after **3 consecutive** gaps. One blip is
   noise; three in a row is a broken volume, and a trajectory made mostly of
   holes is not evidence of anything.

The subtle requirement is the reset: a successful write clears the streak, so
three scattered blips across a 500-run loop do not accumulate into a spurious
halt. `check_trace_write_failure` was rewritten to verify exactly that — it
counts consecutive gaps and asserts escalation happens on the third, not
before — rather than the binary did-it-raise check it started as.

Serialisation is caught with a deliberately broad `except Exception`: tool
arguments are arbitrary objects and `default=str` runs user `__repr__` code
that can raise anything. Whatever it raises, the answer is a gap marker, never
an exception escaping the tracer and killing an otherwise healthy run.

### Open items

- `trace_each_run` defaults to `True` (the literal reading of "every tool
  execution routes through the tracer"), which produces 500 records per
  measurement. At 722 bytes per record this costs ~18 MB for a full
  evaluation — affordable, so the default stands unless Phase 1 finds a
  reason to change it.
- **Concurrency policy for Phase 1** (agreed, pending measurement): re-measure
  serial vs. 8-worker flake-rate drift on the race-condition and
  async-ordering cases. If drift exceeds ~10–15% for a case class, drop that
  class to a lower worker count; the rest of the corpus stays at 8.
  **Baseline and agent must never run the same case at different
  concurrency** — the comparison is the product, and a concurrency difference
  between arms would invalidate it.

### Measurements

Gate passed, twice consecutively. 33 unit tests passed at the time of this
entry (52 after the Phase 1 harness tests). Host: Windows 11 +
WSL2 + Docker Desktop, 16 cores, 7.96 GB allocated to Docker.

**Where a run's time actually goes** (median of 20, measured before optimising):

| Component | Median |
|---|---|
| `copytree` from the `/app` Windows bind mount → tmpfs | 127.4 ms |
| `copytree` tmpfs → tmpfs | 1.2 ms |
| Bare interpreter start (`python -c pass`) | 24.9 ms |
| pytest import + collect + run, in tmpfs | 491.1 ms |
| …with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` | 465.4 ms |

**Improvement 1 — stage each case into tmpfs once per batch.** The bind mount
was charging 127 ms per run to copy four files. Staging turns that into a
per-batch cost. SPAWN median fell from **621.8 ms → 507.9 ms** per run.

**Improvement 2 — parallelism.** pytest's ~465 ms is irreducible, so the only
remaining lever is running batches concurrently.

| Strategy | Median per run | Projected 26,000-run evaluation |
|---|---|---|
| SPAWN, serial | 507.9 ms | 220 min |
| FORK, serial | 205.9 ms (2.47×) | 89 min |
| **SPAWN, 8 workers** | **97.9 ms (5.7×)** | **42 min** |

**Fidelity checks — both strategies were tested for whether they preserve the
phenomenon under study, not just for speed:**

- SPAWN reproduces hash-order flakiness: rate 0.75–0.87 across gate runs, i.e.
  it varies, which is the point.
- FORK produces a *uniform* outcome (0.0 in one gate run, 1.0 in another —
  whichever way the parent's inherited seed happened to fall). The documented
  hash-seed limitation is real and now measured. **FORK must not be used for
  hash-order or import-order cases**, and `strategy_preserves_hash_order()`
  encodes that.
- Parallelism at 8 workers moved the hash-order flake rate by 0.067
  (0.800 → 0.867), inside binomial sampling noise at n=60. Necessary but not
  sufficient: hash order is timing-independent, so Phase 1 must re-check drift
  against the race-condition and async-ordering cases, whose rates are
  expected to be contention-sensitive.

**Trajectory volume:** 722 bytes per record. A 500-run batch with
`trace_each_run=True` costs ~350 KB; a full 26,000-run evaluation ~18 MB.
Affordable, so the literal "every tool execution routes through the tracer"
reading survives Phase 0 on cost grounds.

**Per-run tracing overhead is not separable from noise at n=60:** measured
+41.6 ms, −2.3 ms and +47.4 ms across three gate runs. Not a real number yet;
do not quote it.

### A bug the gate caught in itself

The first gate run passed; the second failed with `expected 3 records, found
6`. `traces/` is a persistent bind mount and the tracer appends by design, so
a fixed `run_id` accumulated records across invocations. Fixed by stamping
gate run ids. Worth recording because it is the failure mode this whole
project is about: a check that passes the first time and fails the second is
exactly what nobody investigates.

---

## 002 — Phase 1: the 12-case corpus and the repeat-execution harness

**Date:** 2026-08-29
**Status:** complete, measured, gate open for review

### What was built

`src/harness/runner.py` (repeat execution, flake rate, failure signatures),
twelve corpus cases, `scripts/measure_corpus.py`, `scripts/concurrency_drift.py`,
`scripts/verify_phase0.py`, and `scripts/demo_masking_fix.py`.
No LLM or agent code. Pure harness and data.

### Measured baselines — 500 runs each, 8 workers

| Case | Root cause | Flake rate | Failures | Signatures |
|---|---|---|---|---|
| 01 | race condition | 33.4% | 167/500 | 1 |
| 02 | test-order dependency | 32.8% | 164/500 | 1 |
| 03 | port collision (TOCTOU) | 38.8% | 194/500 | 1 |
| 04 | clock dependence | 4.0% | 20/500 | 1 |
| 05 | set iteration order | 2.2% | 11/500 | 1 |
| 06 | unseeded randomness | 28.0% | 140/500 | 1 |
| 07 | network timeout, no retry | 5.8% | 29/500 | 1 |
| 08 | tempfile collision | 5.4% | 27/500 | 1 |
| 09 | float tolerance | 12.0% | 60/500 | 1 |
| 10 | async ordering | 27.0% | 135/500 | 1 |
| 11 | cache leak | 31.6% | 158/500 | 1 |
| 12 | masking trap | 3.2% | 16/500 | 1 |

Every case is sound (zero `ERROR` runs), none is 0/500 or 500/500, and every
case resolves to exactly one failure signature. Full sweep: 383s.

### Seven cases were rebuilt, not retuned

The first 500-run pass put seven cases outside the 2–40% target: 02 (48.6%),
05 (94.0%), 07 (1.6%), 08 (95.4%), 09 (78.4%), 10 (51.0%), 11 (49.0%).

**A two-test order dependency is 50% by construction.** Cases 02 and 11 each
had two tests where one ordering fails, which is a coin flip and cannot be
tuned below 50%. Adding a third test that *resets* the leaked state (a
registry `reset()`, an `lru_cache.cache_clear()`) drops it to 1/3: the failure
now needs the polluting test to precede the asserting one *and* the reset not
to fall between them. 2 of 6 orderings, and both landed near 33%.

**Set-iteration cases are naturally near-100%.** Asserting an exact order over
a set of n fails (n−1)/n of the time. Case 05 was rebuilt so that iteration
order picks a *winner* rather than an order: five config sources, four
agreeing, one stale legacy source that wins only when it lands first. Uniform
ordering would give 20%; identity hashing is biased enough to give 2.2% — and
that bias is itself the lesson, since it is what lets this class of bug
survive review.

**A removed experiment: tuning case 09 by float magnitude.** The first design
used 1e16-scale balances cancelling against small line items, on the theory
that catastrophic cancellation would flake at a moderate rate. It gave 78.4%.
Shrinking the magnitudes did not scale the rate down — the loss is a threshold
around 2^53, so the case jumped between 0% and 100% with nothing usable in
between. Two further guesses (`[0.1,0.2,0.3,0.4]` → 0%,
`[0.03,1.7,2.9,11.13]` → 100%) confirmed that reasoning about *what fraction
of permutations are wrong* predicts nothing, because observed completion
orders are heavily concentrated rather than uniform. Measuring the actual
distribution of totals settled it in one step: `[0.1,0.7,1.1,2.3]` yields 4.2
in 66% of runs and 4.199999999999999 in 34%. Recorded because the failed
approach was analytical and the successful one was empirical.

### Case 12: the masking fix, demonstrated

`scripts/demo_masking_fix.py` builds three variants and measures each at three
workloads (300 runs per cell):

| variant | 10,500 docs | 80,000 docs | 600,000 docs |
|---|---|---|---|
| baseline | 3% | 100% | 100% |
| masked_sleep (`time.sleep(0.05)`) | **0%** | 8% | 100% |
| masked_retry (retry the assertion) | **0%** | **0%** | 99% |
| true_fix (publish before signalling) | **0%** | **0%** | **0%** |

Both masking fixes reach zero failures at the corpus workload. Both would pass
a 500-run verification. Neither removes the publication race — they buy
headroom, and the amount of headroom is the only difference between them.

Two stress levels were needed, not one. At 80,000 documents the sleep has
already broken while the retry still looks like a real fix; only at 600,000 —
where the build outlasts the retry's whole 100 ms budget — does the retry come
back. A single stress column would have produced a confident, wrong conclusion
that the retry was legitimate.

**This is the evidence for the verification thesis.** 500 clean runs at one
workload is not proof a race is gone; it is proof the race is currently
narrower than the observation window.

### Two design decisions worth recording

**Failure-signature grouping was set by measurement, not taste.** The first
implementation kept observed values verbatim and produced one signature *per
run* for the race case (`assert 390666 == (8 * 50000)`) and ten signatures for
one bug in the sharding case. Collapsing numbers, quoted strings and bracketed
sequences to placeholders while preserving exception type, location and
assertion structure took every case to exactly one signature — and kept case
05's two distinct test functions distinct. See `normalise_message`.

**The harness traces batches, not runs.** The agent never calls `run_once`; it
asks for "run this 500 times". Emitting 500 turns per measurement is
affordable on disk (722 B each, measured at Phase 0) but would bury the
instructions, reflections and human checkpoints a trajectory exists to show.
One turn per batch carries the aggregate and the signatures; per-run tracing
stays behind `trace_each_run` for debugging.

### A safety property that fired on its own tooling

The first 500-run recording pass crashed with `Read-only file system` on
`corpus/`, which is mounted `:ro` so agent-authored code cannot alter the cases
it is measured against — including the recorded baseline it will be compared
to. Rather than relax the mount, a separate `recorder` service was added that
the agent and the evaluation never use, and the error message now names it.

### Still open for Phase 2

- `scripts/concurrency_drift.py` is written but not yet run. Timing-sensitive
  rates moved substantially between measurement conditions (case 01: 47% at
  n=100 alone, then 20.6% and 33.4% at n=500 in full sweeps; case 12: 38% →
  6.2% → 3.2%), which is consistent with the cliff found while tuning case 12
  and needs quantifying before the baseline/agent comparison depends on it.
- Every case's `masking_fix_available` is `True`, which the validator will
  have to contend with on all twelve, not just case 12.
