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

Gate passed, twice consecutively. 27 unit tests pass. Host: Windows 11 +
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
