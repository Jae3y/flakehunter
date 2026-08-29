# FlakeHunter

Diagnoses a flaky test by experiment, writes a real fix, and then proves the
fix by running the test 500 times. The deliverable is a merge-ready patch plus
a root-cause writeup — not a report.

---

## The user and the bottleneck

A developer or platform engineer maintaining a test suite in CI. A flaky test
passes most of the time and fails intermittently with no code change, so the
rational move is to click "rerun" rather than investigate. Suites accumulate
tests everyone has agreed to ignore, and real regressions hide behind "oh,
that one's just flaky".

The reason this is hard is specific: **normal debugging assumes the bug
reproduces on demand.** A flaky test breaks that assumption at step one. You
cannot confirm a fix by running the test once — you have to run it many times
and reason statistically. That is tedious enough that almost nobody does it,
which is why the tests stay broken.

---

## What we actually found

We built the obvious control — one LLM call, same model, same files, "diagnose
and fix this flaky test" — expecting it to fail on the harder cases. It did
not. Given a root-cause taxonomy and an explicit rule that widening a timing
window is not a fix, a single call produced a patch for **12/12 cases** and
reported `high` confidence on **every one**. It even solved the trap case
designed to bait a `sleep()`.

Then we re-ran each patch 500 times.

| | Count |
|---|---|
| Patches produced | 12/12 |
| Reported `high` confidence | **12/12** |
| Verified at zero failures | **10/12** |
| Confident patches that were **not** fixes | **2** |

The two false greens:

- **case 07** — correct root cause, plausible fix, still fails **0.80%** of the
  time. Four failures in 500 runs. One execution would never surface it.
- **case 01** — the correct fix, but every verification run *errored* on a
  resource limit. A `0.00%` residual that meant nothing.

Nothing separated those two from the ten that worked except running the test
hundreds of times.

**So the value is not "the agent fixes more". It is that the agent knows
whether it fixed anything.** On case 07 the agent spent five rounds, had three
patches rejected by its own validator, and declined to declare success — on the
exact case the control called `high` confidence.

That is the shape of the problem in the field too. The bottleneck was never
generating a plausible fix; it was confirming one.

---

## How it works

```
CONFIRM      run N times; empirical flake rate + distinct failure signatures
HYPOTHESIZE  ranked candidates, each with a DISCRIMINATING prediction
EXPERIMENT   the cheapest manipulation that separates the top two
OBSERVE      observed vs predicted → confirm or eliminate; loop (cap 5)
PATCH        minimal fix for the confirmed cause
VALIDATE     anti-cheat; reject with reasons, re-author
VERIFY       500 runs; any failure reopens the loop
APPROVE      package patch + evidence for a human
REPORT       emit the artifact
```

Experiments come from a **closed vocabulary** (`pin_hash_seed`,
`serialize_execution`, `amplify_contention`, `isolate_test`,
`force_test_order`, …) rather than free-form code. Three reasons: nothing
model-authored reaches a shell, a trajectory that says `pin_hash_seed(0)` is
readable evidence, and a closed set makes it *visible* when a case has no
discriminating experiment instead of letting the agent invent one that does not
discriminate.

### The validator is the load-bearing part

The naive anti-cheat rule is "reject patches that add `sleep` or a retry". That
rule is wrong here, and the corpus proves it: **case 07's correct fix *is* a
retry** (its root cause is literally `network_timeout_no_retry`), while **case
12's masking fix is also a retry**. The same construct is right in one case and
cheating in the other. Syntax cannot separate them.

So validation has two layers:

**Structural** (AST, no execution) — assertions not deleted or made trivially
true, no skip/xfail/flaky marker, exact comparisons not loosened to `approx`,
source actually modified, `conftest.py` untouched, a bare sleep is not the whole
patch.

**Behavioural** — re-verify under 4× CPU oversubscription. A sleep buys fixed
headroom; a retry buys a fixed budget; a fix that removed the race has neither
to exhaust. This is measured, not assumed:

| variant | 10.5k docs | 80k docs | 600k docs |
|---|---|---|---|
| baseline (broken) | 3% | 100% | 100% |
| `time.sleep(0.05)` | **0%** | 8% | 100% |
| retry the assertion | **0%** | **0%** | 99% |
| true fix | **0%** | **0%** | **0%** |

Both masking fixes reach zero at the corpus workload — **both would pass a
500-run verification** — and both come back as the workload grows. Two stress
levels were needed: at 80k the retry still looks legitimate.

---

## The baseline, and its documented resource difference

The baseline gets the **same model**, the **same complete view of the project**,
the **same taxonomy**, and the **same rules about what counts as a fix**. All of
that lives in one shared module (`src/llm/prompts.py`) so the two arms cannot
drift apart.

**The one thing it does not get is the repeat-execution harness.** It gets one
call and never sees a test run. That absence is the independent variable, and it
is the only intended difference between the arms.

Both arms also read their run counts and worker counts from one locked protocol
module (`src/harness/protocol.py`), so a comparison cannot be accidentally run
under different conditions.

---

## Safety properties, enforced rather than asserted

**Consequential actions run in a sandbox.** The container *is* the isolation
boundary. The Dockerfile writes `/.flakehunter-sandbox` and `assert_sandboxed()`
refuses to execute without it, so a stray invocation from the host fails loudly
instead of quietly running agent-authored code on your laptop. Verified from
inside a test run: no host path reachable, no Docker socket, non-root, resource
caps applied, credentials stripped, `src/` and `corpus/` read-only.

**A qualified human reviews anything consequential.** A patch that survives
validation and 500-run verification is written to
`results/pending_approval/<case>/` with its root-cause writeup and evidence —
and **is not applied**. A person decides. `corpus/` is verified clean against
HEAD by the self-audit.

---

## Results, and what they cover

**The baseline arm is complete: all 12 cases.** Claimed 12/12 → verified 10 →
legitimate 10/12.

**The agent arm is not.** The API runs on the Google AI Studio free tier —
**20 requests per day per model** — and a case costs 2–6 requests. One case is
complete (case 12, awaiting approval); nine are checkpointed mid-loop and
resume where they stopped; case 01 is excluded for runtime (D-012). Attempts
were ordered to spread across root-cause classes rather than numerically, so
partial coverage shows breadth rather than five variants of one failure type.

**The headline finding does not depend on the agent arm.** Claimed → verified →
legitimate is measured from the baseline's twelve patches, the 500-run
verifications, and the anti-cheat validator. None of those three need the agent
to have run. Case 01's patch that never compiled and case 07's mask — 0.80% at
normal load, 24.5% under oversubscription — are anchors of the baseline's own
record.

What incomplete agent coverage *does* cost is the other direction of the
comparison: how often the loop reaches a verified fix where the baseline does
not. One case is not a rate.

Full table in `results/RESULTS.md`; regenerate with `scripts/run_compare.py`.
Session narrative in `SESSION_LOG.md`. Per-iteration measurements in
`docs/CHANGELOG.md`.

---

## Improvement Changelog

Appended live with real numbers, never reconstructed. Five entries; the ones
that record a failure are the useful ones.

| # | What it records |
|---|---|
| 001 | Sandbox and tracer. Container-per-execution priced at 3.6 h of pure overhead and rejected; tmpfs staging cut 127 ms/run; SPAWN vs FORK fidelity measured, not assumed |
| 002 | 12-case corpus. Seven cases **rebuilt, not retuned**. Failure-signature grouping set by measurement (one signature per *run* → one per bug) |
| 003 | **Drift diagnosis.** The harness was *creating* the flakiness it measured |
| 004 | Baseline arm. Two harness bugs caught by measurement, not review |
| 005 | Agent loop. A bug the live run exposed in the experiment layer |

### The removed experiment

Tuning case 09's float flakiness by magnitude. The theory was that catastrophic
cancellation would scale smoothly with the size of the operands. It does not —
precision loss is a **threshold at 2^53**, so the case jumped 0% ↔ 100% with
nothing usable between. Three analytical guesses failed the same way, because
they reasoned about *what fraction of permutations are wrong* when the observed
completion orders turned out to be heavily concentrated rather than uniform.
Histogramming the actual totals settled it in one step. The failed approach was
analytical; the one that worked was empirical.

---

## Failure mode

**The absolute flake rates are session-local, and we can prove it.**

Corpus rates moved between sessions with no code change — case 01 read 47%,
20.6%, 33.4%, 18.9%, 41.2%, 52.0%. The cause was not sampling error
(within-session overdispersion was 0.83×, at or below binomial). It was that
**our own harness was manufacturing the phenomenon**: case 01 flaked **0.0%
serially and 25% at 8 workers**, so its rate tracked machine load rather than
the bug. Case 06, whose nondeterminism is intrinsic, was unmoved by any
condition tested.

Two cases were rebuilt so their nondeterminism is intrinsic. That removed the
manufactured component but **not** the host's own drift: case 07 moved 2.5% →
34.0% serial across a few hours, untouched.

What this costs us: a "before" number is only meaningful with its session
attached, and comparisons must be paired within a session. What it does **not**
cost us: the primary metric. A real fix gives zero failures in any machine
state. Machine speed changes how *often* a race is observed, not whether it
exists.

The second failure mode is scope: twelve seeded cases in one language, each
with a single known root cause. Real suites have interacting causes, and
nothing here has been tested against those.

---

## Hot take

**Confidence is not evidence, and the industry is currently shipping
confidence.**

Our control produced twelve patches and rated every single one `high`
confidence. Two were wrong. It was not being reckless — it had genuinely
reasoned its way to a correct root cause on both, and the fixes were plausible.
It simply had no mechanism that could distinguish "I fixed it" from "I believe
I fixed it", because it never ran anything.

The reflex response to that is a better model. The measurement says otherwise:
the model was already good enough to solve 10 of 12, *including the trap case
built specifically to fool it*. What it lacked was a way to check its own work.
Adding one turned a system that is right 83% of the time and confident 100% of
the time into one that reports what it actually verified — and, on case 07,
correctly refuses to claim a fix at all.

The uncomfortable corollary is that **an agent's most valuable component may be
the part that tells it it's wrong.** That is unglamorous, it is mostly a test
harness and an anti-cheat validator, and it is where the actual engineering
went.

---

## Quick start

Docker is the only host requirement.

```bash
cp .env.example .env    # add GEMINI_API_KEY
```

```bash
docker compose build && docker compose run --rm flakehunter python -m pytest tests -q
```

```bash
docker compose run --rm flakehunter python scripts/check_llm.py
```

Full guide, measured runtimes, and the API quota budget: `REPRODUCTION.md`.

---

## Layout

| Path | What |
|---|---|
| `src/telemetry/tracer.py` | JSONL trajectory capture. Every LLM call and tool execution routes through it |
| `src/sandbox/executor.py` | One test run: fresh workdir, timeout, rlimits, process-group kill |
| `src/harness/runner.py` | Run N times → flake rate + failure signatures |
| `src/harness/validator.py` | Anti-cheat: structural + behavioural |
| `src/harness/protocol.py` | The locked measurement protocol, shared by both arms |
| `src/baseline/one_shot.py` | The fair baseline |
| `src/agent/` | orchestrator, hypotheses, experiments, patcher |
| `src/evaluate/compare.py` | Results table + claimed-vs-verified analysis |
| `corpus/case_XX_*/` | 12 seeded cases, each with metadata and a README |
| `traces/`, `results/` | Trajectories and outputs |
| `DECISIONS.md` | Judgment calls: tension, choice, reasoning, revisit trigger |
