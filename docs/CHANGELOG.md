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

---

## 003 — Drift resolved: contention was manufacturing the flakiness

**Date:** 2026-08-29 (unattended run)
**Status:** diagnosed, two cases rebuilt, protocol locked

### The question

Corpus rates moved between measurements with no code change. Case 01 read
47%, 20.6%, 33.4%, 18.9%, 41.2% and 52.0% across sessions; case 12 read 38%,
6.2%, 3.2%. Sample size, machine state and ordering all predict different
things, so they were measured rather than argued about.

### Sample size was never the problem

Five back-to-back batches of 500 runs, one container, `scripts/diagnose_drift.py`:

| case | batch rates | observed sd | binomial sd | overdispersion |
|---|---|---|---|---|
| 01 race | 17.6 / 21.4 / 18.4 / 18.8 / 18.4% | 1.45% | 1.75% | **0.83x** |
| 03 port | 37.4 / 44.0 / 42.6 / 41.4 / 44.8% | 2.90% | 2.21% | 1.32x |
| 06 RNG | 24.6 / 26.8 / 26.6 / 25.2 / 24.8% | 1.03% | 1.95% | 0.53x |
| 12 masking | 1.4 / 1.0 / 0.8 / 7.0 / 3.0% | 2.59% | 0.72% | 3.61x |

Within a session, batches are close to binomial. So `n` was not the issue —
and the same script's position test then measured case 01 again after ~10,000
runs of load and got **41.2%, then 52.0%**, against the 18.9% it had just
recorded.

### The cause: the harness was creating the phenomenon

`scripts/concurrency_drift.py`, serial versus 8 workers:

| case | serial | 8 workers | drift |
|---|---|---|---|
| **01 race** | **0.0%** | 25.0% | 25.0 pts |
| 03 port | 45.5% | 33.0% | 12.5 pts |
| 04 clock | 6.0% | 2.0% | 4.0 pts |
| 07 network | 2.5% | 8.0% | 5.5 pts |
| 08 tempfile | 1.0% | 4.5% | 3.5 pts |
| 09 float | 15.5% | 13.5% | 2.0 pts |
| **10 async** | **0.0%** | 11.5% | 11.5 pts |
| 12 masking | 0.0% | 0.0% | 0.0 pts |

Case 01 does not flake at all when run alone. Eight concurrent runs
oversubscribing the CPU is what forced the GIL to preempt mid-update. The
harness was manufacturing the phenomenon it was measuring, and the rate
tracked machine load because load was never controlled. Case 10 the same.

The control confirms it: case 06, whose nondeterminism is intrinsic to the
code under test rather than to thread timing, sat at 23–27% in every condition
tried while case 01 wandered from 18.9% to 56.8%.

### A removed experiment: the idle CPU probe

`--mode interleave` timed a fixed single-threaded loop between batches to test
"the machine is thermally throttling". It found **no systematic slowdown**
(−4.2% over ten cycles) while case 01 varied 36.8–56.8%, correlation +0.29.
The probe was measuring single-core speed with the workers idle, which cannot
see the all-core frequency drop that sustained parallel load causes. The
approach was abandoned: a probe that runs when the load is absent cannot
measure what the load does.

### The fix: rebuild the cases, not the protocol

Both cases were rebuilt so their nondeterminism is intrinsic:

| case | change | serial before | serial after |
|---|---|---|---|
| 01 | 25,000 → 50,000 iterations per worker, so each worker's loop outlasts the 5 ms GIL switch interval unaided | 0.0% | 27.3% |
| 10 | equal panel cost instead of a work gradient, so completion order is decided by scheduling not by size | 0.0% | 22.7% |

All twelve now flake when run alone (serial, n=100): 96.0, 25.0, 59.0, 4.0,
4.0, 24.0, 34.0, 24.0, 22.0, 62.0, 32.0, 20.0%.

### What is still not fixed, stated plainly

Rebuilding removed the *manufactured* component. It did not make the host
stable. Between the drift sweep and the serial sweep several hours later, with
no code change:

| case | earlier serial | later serial |
|---|---|---|
| 07 network | 2.5% | 34.0% |
| 08 tempfile | 1.0% | 24.0% |
| 01 race | 27.3% | 96.0% |
| 03 port | 45.5% | 59.0% |

The host got materially slower over the session, and every timing-sensitive
case moved with it. This is not something a measurement protocol on this
machine can remove.

The consequences, and they are narrower than they look:

1. **Absolute "before" rates are session-local.** A corpus flake rate is only
   meaningful alongside when it was taken.
2. **Comparisons must be paired within a session.** Both arms measure the same
   case adjacently under the same machine state, so the difference between
   them survives even when the absolute numbers do not. This is why
   `src/harness/protocol.py` owns the settings for both arms.
3. **The primary metric is untouched.** Residual flake rate after a real fix
   is zero, and zero is zero under any machine state. Machine speed changes
   how *often* a race is observed, not whether it exists.

Recorded rather than smoothed over, because a table of absolute flake rates
that silently depended on what else the laptop was doing would be the exact
failure this project exists to attack.

---

## 004 — Phase 2: the one-shot baseline, and a result that complicates the thesis

**Date:** 2026-08-29 (unattended run)
**Model:** `gemini-3.6-flash`, both arms
**Status:** complete for 11 of 12 cases

### What it does

One LLM call per case. Same model, same complete view of the project, same
root-cause taxonomy, same rules about what a fix must be — all of it shared
with the agent through `src/llm/prompts.py`, so the two arms cannot drift
apart. The one thing the baseline does not get is the repeat-execution
harness. That absence is the independent variable.

### Results — 500-run verification per case, locked protocol

| Case | Cause identified | Residual after fix | Sound | Fixed |
|---|---|---|---|---|
| 01 race condition | yes | 0.0% | **no** | no |
| 02 test-order dependency | yes | 0.0% | yes | **yes** |
| 03 port collision | yes | 0.0% | yes | **yes** |
| 04 clock dependence | yes | 0.0% | yes | **yes** |
| 05 set iteration order | yes | 0.0% | yes | **yes** |
| 06 unseeded randomness | yes | 0.0% | yes | **yes** |
| 07 network timeout | yes | **0.8%** | yes | no |
| 08 tempfile collision | yes | 0.0% | yes | **yes** |
| 09 float tolerance | yes | 0.0% | yes | **yes** |
| 10 async ordering | yes | 0.0% | yes | **yes** |
| 11 cache leak | yes | 0.0% | yes | **yes** |
| 12 masking trap | yes | 0.0% | yes | **yes** |

**Root cause identified: 12/12. Fixed: 10/12.**
79,455 tokens (13,616 prompt / 65,839 output). 21.4 min plus a 3.5 min re-run.

### The uncomfortable result

**The baseline got case 12 right.** Given the taxonomy and an explicit rule
that widening a timing window is not a fix, it produced exactly the correct
repair — build the index into a local dict, assign it, and only then set
`ready`. Not a sleep. Not a retry. The trap case did not trap it.

This matters, and burying it would be dishonest. On this corpus, a single
call to a current flash-tier model with a good prompt fixes 10 of 12 cases.
The gap the agent has to justify is narrower than the project's premise
assumed.

What the baseline still cannot do is **know which 10**. It reported `high`
confidence on case 07, whose fix left the test failing 0.8% of the time — four
failures in 500 runs that one execution would never have surfaced. The
baseline cannot tell that case apart from the eleven others it was equally
confident about. Its value proposition is "usually right, never sure"; the
harness is what converts that into "sure".

That reframes the comparison from *can it fix them* to *can it tell whether it
fixed them* — which is closer to the real bottleneck anyway, since the whole
reason flaky tests get rerun instead of fixed is that nobody can confirm a fix
without running it many times.

### Two harness bugs, both caught by measurement rather than review

**Truncated replies.** Cases 02 and 04 failed with `Unterminated string
starting at line 8 column 22`. The model was not producing malformed output —
it ran out of room. Gemini 3.x draws reasoning tokens from the same
`maxOutputTokens` budget as the answer, and 8,192 was spent almost entirely on
thinking, leaving ~478 characters of JSON. Raised to 32,768 with a retry at
double the budget on a `MAX_TOKENS` finish. Both cases then fixed cleanly.

**A false pass.** Case 01's patch — adding a lock, the *correct* fix — reported
**0/500 failures with 500 error runs**. `RLIMIT_CPU` is summed across threads,
so eight threads hit the flat 10-second ceiling in about two seconds of wall
time and pytest exited 2 before any assertion ran. Zero failures. 0.00%
residual. A perfect-looking fix in which nothing executed.

It was caught only because `ERROR` is a distinct outcome from `FAIL` and
`is_sound` is consulted before a fix counts — a Phase 0 decision that had, until
this moment, never earned its keep. Had errors been folded into the flake rate,
case 01 would have entered the results table as a clean baseline win.

### Case 01 excluded from both arms

Once fixed, case 01 takes roughly four seconds per run — the lock serialises
400,000 increments across eight threads — so a 500-run serial verification
exceeds half an hour. It was cut from both arms rather than measured in one,
keeping the comparison symmetric. See `DECISIONS.md` D-012 for the remedy.

---

## 005 — Phase 3: the agent loop, the validator, and what two live cases showed

**Date:** 2026-08-29 (unattended run)
**Status:** loop complete and exercised; arm blocked at 2 of 12 cases by API quota

### Live results

Two cases ran the full loop against a real model before quota ran out.

**case_12 masking trap — PENDING approval, `gemini-3.6-flash`, 1 round.**

| Step | Result |
|---|---|
| CONFIRM | 2/200 = 1.0%, one signature |
| HYPOTHESIZE | `publication_ordering` vs `race_condition`, each with a distinguishing prediction |
| EXPERIMENT | `amplify_contention(2)`, predicted *increased* → observed **1.0% → 44.0%** |
| OBSERVE | confirms publication ordering, eliminates the race |
| VALIDATE | 7/7 checks, including **0/200 at 32 workers** |
| VERIFY | **0/500** |
| APPROVE | written to `results/pending_approval/`, not applied |

**case_07 network timeout — UNRESOLVED, `gemini-3.5-flash`, 5 rounds, 16.8 min.**
Five hypotheses, five experiments, three patch attempts, all three rejected by
the validator (`survives_stress` twice, `modifies_source` once). The agent
declined to declare success.

That is the correct outcome, and it is the interesting one: **the baseline
"fixed" this same case with `high` confidence, and its patch still fails 0.80%
of the time.**

### Claimed versus verified — the headline

The baseline returned a patch for **12/12** cases and reported `high`
confidence on **every one**. Re-running each patch 500 times:

| | Count |
|---|---|
| Patches produced | 12/12 |
| Reported `high` confidence | 12/12 |
| Actually reached zero failures | **10/12** |
| Confident patches that were not fixes | **2** |

- **case_07** — correct root cause, plausible retry fix, **0.80% residual**
  (4 failures in 500 runs). Invisible to one execution.
- **case_01** — correct fix (a lock), but every verification run errored on the
  CPU limit. `0.00%` residual that meant nothing until `is_sound` was checked.

Two false greens out of twelve, indistinguishable from the ten real ones
without running the test hundreds of times. That is the gap the harness fills.

### A bug the live run exposed in the agent

case_07 round 4 ran `isolate_test(test_network_timeout)` — against a case whose
only test is `test_status_is_fetched_from_a_healthy_service`. pytest collected
nothing, all 150 runs errored, `flake_rate` read **0.0%**, and the loop scored
that as *eliminated*.

An invented node id was manufacturing evidence for whichever hypothesis the
experiment happened to target. It is the same failure as counting an ERROR run
as a pass during verification, in a layer that had not been given the same
guard. Two fixes:

1. `run_experiment` returns **no evidence** when the batch is unsound, with a
   note saying so, instead of a rate.
2. The experiment designer is handed the case's **real node ids**, so it cannot
   invent one.

The validator contained the damage — all three resulting patches were rejected,
nothing wrong was accepted — but the agent burned four rounds on false evidence
before hitting the cap.

### The stuck-loop detector, finally tested

It had never fired in a live run. Five tests (`tests/test_orchestrator_stuck.py`)
now drive it with a scripted model that names the same root cause every round
and designs an experiment that cannot confirm it. Zero API calls; real sandbox,
harness and corpus.

It fires on **round 3 of 5** — before the arbitrary cap, which matters, because
a detector that only ever tripped at the cap would be indistinguishable from
the cap. It records its hypotheses and experiments, produces no patch, reaches
no approval directory, and leaves a contiguous trajectory.

### Quota

Free tier is **20 requests per day per model**. The baseline consumed
`gemini-3.6-flash`; case_07's five rounds consumed `gemini-3.5-flash`.
Nine cases could not be attempted. They are carried in the results table as
explicit `NOT RUN (quota)` rows rather than dropped.
