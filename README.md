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

**The bottleneck** is specific, and it is not diagnosis. Normal debugging
assumes the bug reproduces on demand; a flaky test breaks that assumption at
step one. You cannot confirm a fix by running the test once — you have to run
it hundreds of times and reason statistically. That is tedious enough that
almost nobody does it, so the fix never gets confirmed and the test stays on
the ignore list.

**Why it matters.** A suite with ignored tests is a suite that has stopped
being a safety net. Every "just rerun it" trains the team to discount red, and
a genuine regression arriving in that same test is indistinguishable from the
noise everyone has agreed to skip. The cost is not the flaky test — it is the
real failure that will one day hide behind it, plus the CI minutes and the
attention spent re-running rather than fixing.

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
  time. Four failures in 500 runs. Under CPU oversubscription the validator
  drives it to **24.5%**: a confirmed mask, not a near-miss.
- **case 01** — the patch **never compiled** (`def __init__( -> None:`). All
  500 runs errored during collection, and it reported a `0.00%` residual — the
  most flattering number in the table, from code that never ran.

Nothing separated those two from the ten that worked except running the test
hundreds of times and checking the patch.

**So the value is not "the agent fixes more". It is that the agent knows
whether it fixed anything.** The control cannot: it reported the same `high`
confidence on all twelve. Separating them took 500-run verification and an
anti-cheat validator, neither of which a single call has.

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

**Structural** (AST, no execution) — the patch parses; assertions not deleted or
made trivially true; no skip/xfail/flaky marker; exact comparisons not loosened
to `approx`; source actually modified; `conftest.py` untouched; **test-level
condition constants unchanged**; a bare sleep is not the whole patch.

The last two were added *because a patch got past the validator*. The agent
once produced a fix that passed every check then in force — including the
stress pass — by setting a test fixture's `SERVICE_WORK_S` to `0.0`, deleting
the delay that produces the flakiness. It survived the stress check because
oversubscribing the CPU stretches a timing *window*, and there was no window
left to stretch. When a check is added, every previously accepted patch is
re-run against it (`scripts/revalidate_pending.py`); acceptance under a weaker
rule set is not evidence.

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

---

## The tool found a flaky test in itself

> *The clearest evidence that this generalises, and it was not planned.*

Late in the build, `test_no_patch_is_produced_when_stuck` — one of our own unit
tests — passed on its own and failed once in a full-suite run. The reflex is to
rerun it. That reflex is the entire problem this project exists to attack, so
we did what the tool does instead.

**What was happening.** The test drives the agent loop with a scripted model
that always proposes the same root cause and always designs an experiment that
*cannot* confirm it — pinning the timezone has no bearing on a
random-number-driven failure. The loop should therefore always eliminate the
hypothesis and never reach the PATCH step.

**Why 40 runs was not enough.** The case under test fails about **20%** of the
time. The experiment ran **40 times**. The loop classifies an observed rate at
or below 8% as `reduced`, and treats `reduced` as partial support for a
predicted `eliminated` — deliberately, so a real signal moving the right way is
not discarded on a threshold. At n=40 and p=0.20, the chance of landing at or
below 8% by sampling alone is roughly **13%**.

So the experiment confirmed a hypothesis that could not be true, the loop
proceeded to PATCH, and the scripted model raised on a request it does not
answer. Roughly one run in eight.

**The test was not wrong about the behaviour. It was drawing a conclusion from
too few runs** — the exact failure mode the twelve corpus cases were built to
demonstrate, occurring in the code that demonstrates them.

**The fix was the project's own thesis.** Not a rerun, not a retry decorator,
not a `sleep`: raise the sample until the threshold sits about three standard
deviations out. Experiment runs 40 → 120, confirm runs 60 → 80. It then passed
twice consecutively, and the fixture carries a comment explaining why the
numbers are what they are so nobody trims them back for speed.

**Why this matters more than the corpus.** The twelve cases are seeded — we
wrote them, we knew the answers, and a sceptic can reasonably ask whether the
approach works on anything we did not plant. This one we did not plant. It
appeared in our own test suite, was diagnosed by the same reasoning the tool
applies, and was fixed the same way. See `DECISIONS.md` D-020.

A related near-miss, in the same spirit: a unit test once leaked a **60-run**
measurement into a checkpoint directory a live run would read from. It was
caught because every recorded number carries its conditions, so `12/60` stood
out beside seven cases reading `n/200`. It reached no reported result, and the
verification of that is written out in `DECISIONS.md` D-021 rather than
asserted.

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

Appended live with real measurements, never reconstructed. Eight entries in
`docs/CHANGELOG.md`; the ones recording a failure are the useful ones.

| # | What changed | Measured effect |
|---|---|---|
| **001** | Sandbox + tracer. Container-per-execution priced and rejected; tmpfs staging; SPAWN vs FORK fidelity | 3.6 h of pure overhead avoided; 127 ms/run saved (622 → 508 ms); FORK shown to *destroy* hash-order flakiness, so gated in code |
| **002** | 12-case corpus + repeat-execution harness | Seven cases **rebuilt, not retuned**; failure-signature grouping fixed from one signature *per run* to one *per bug* |
| **003** | Drift diagnosis | The harness was **creating** the flakiness: case 01 flaked **0.0% serially, 25% at 8 workers**. Two cases rebuilt to be intrinsically flaky (27.3%, 22.7% serial) |
| **004** | Baseline arm | 12/12 causes identified, 10/12 verified. Two harness bugs caught *by measurement*: reply truncation, and a 0.00% residual where every run errored |
| **005** | Agent loop + validator | Case 12 resolved in 1 round, 0/500. A live run exposed invented test node ids scoring as evidence — fixed both ways |
| **006** | Case 07 retry after the node-id fix | 5 rounds → **2**; invalid experiments 2 → **0**; 16.8 min → 5.4 min |
| **007** | Validator evolution + retroactive re-check | Two checks added *because patches defeated the validator*. All 14 accepted patches re-checked: agent 1/2, baseline 10/12 |
| **008** | Designing around 20 requests/day | Checkpoint/resume + merged round call. Per round **2 requests → 1**; case 05 reached round 3 where it previously reached round 1 |

### The removed experiment

Tuning case 09's float flakiness by operand magnitude. The theory: catastrophic
cancellation scales smoothly with operand size. It does not — precision loss is
a **threshold at 2^53**, so the case jumped 0% ↔ 100% with nothing usable
between. Three analytical guesses failed identically, because they reasoned
about *what fraction of permutations are wrong* when observed completion orders
turned out to be heavily concentrated rather than uniform. Histogramming the
actual totals settled it in one step: `[0.1, 0.7, 1.1, 2.3]` yields 4.2 in 66%
of runs and 4.199999999999999 in 34%.

The failed approach was analytical. The one that worked was empirical. That is
the same lesson as the rest of the project, arriving from a different direction.

### A correction, left visible

`DECISIONS.md` D-011 attributed case 01's 500 error runs to a CPU limit,
inferred from the exit code without reading the captured stderr. Raising the
CPU budget changed nothing, which is what finally prompted reading it — the
patch had a syntax error. A syntax error and a resource kill both exit pytest
with code 2. The entry carries the correction rather than being quietly edited,
and the validator gained the check that would have named it in one line.

---

## Primary Failure Mode

**Our verification can still be fooled, and case 07 is the proof.**

The architecture rests on one claim: run the test enough times and you will
know whether the fix worked. Case 07 shows the claim is incomplete. The
baseline's patch passed **500 runs at the normal worker count** with a residual
of 0.80% — four failures, easily read as noise. Under 32 workers the same patch
failed **49 out of 200: 24.5%**.

The patch had not fixed the race. It had widened the timing window until the
failure fell outside the observation. And 500 runs did not catch it, because
**run count is only one of two sampling dimensions, and it is the one we sample
well.**

The second dimension is *stress* — how hard the fix is pushed relative to the
headroom it bought. We sample that with a single point: 4× CPU
oversubscription. The masking demonstration shows exactly how arbitrary that
is:

| mask | corpus workload | 80k docs | 600k docs |
|---|---|---|---|
| `time.sleep(0.05)` | 0% | 8% | 100% |
| retry the assertion | 0% | **0%** | 99% |

The retry survived a workload **eight times** the corpus one and only broke at
sixty times. Had we stopped at 80k — the obvious first stress level — we would
have certified it as a real fix and said so with 500-run evidence behind us.

So the honest statement of the limit: **we catch masks whose headroom is
smaller than the stress we happen to apply, and we do not know where that
ceiling sits.** A fix that buys ten seconds of slack passes everything in this
repo. The verification is strictly better than one execution and strictly worse
than a proof.

**A second escape the stress check cannot see at all.** Our own agent, on this
same case, produced a patch that set the test fixture's service delay to `0.0`
— deleting the condition that produces the flakiness. It passed the stress
check because oversubscription stretches a timing *window*, and there was no
window left to stretch. That needed a structural check
(`test_conditions_unchanged`), added only after the patch got through. There is
no reason to believe that was the last such gap.

### Secondary: measurements are session-local

Corpus rates moved between sessions with no code change — case 01 read 47%,
20.6%, 33.4%, 18.9%, 41.2%, 52.0%. Not sampling error (within-session
overdispersion was 0.83×, at or below binomial): **our own harness was
manufacturing the phenomenon.** Case 01 flaked 0.0% serially and 25% at 8
workers, so its rate tracked machine load rather than the bug. Two cases were
rebuilt to be intrinsically flaky, which removed the manufactured component but
not the host's own drift — case 07 moved 2.5% → 34.0% serial across a few
hours, untouched.

This costs precision on "before" numbers, not on the primary metric: a real fix
gives zero in any machine state.

### Third: scope

Twelve seeded cases, one language, one known root cause each. Real suites have
interacting causes and nothing here has been tested against those. The one
piece of evidence that this generalises is the flaky test we found in our own
suite — which we did not plant.

---

## Hot Take / Insights

**Confidence is not evidence, and the industry is shipping confidence.**

Our control produced twelve patches and rated every single one `high`
confidence. Two were wrong. It was not being reckless — it reasoned its way to
a correct root cause on both, and both fixes were plausible enough that a
reviewer would have merged them. It simply had no mechanism that could
distinguish *"I fixed it"* from *"I believe I fixed it"*, because it never ran
anything.

The reflex response is a better model. The measurement says otherwise: this
model was already good enough to solve **10 of 12, including the trap case
built specifically to bait it into a `sleep()`**. Capability was not the
binding constraint. Self-knowledge was.

Three things follow, and they generalise past flaky tests:

**1. A confidence score derived from the same forward pass as the answer is
not an independent check.** It is the model's fluency reported back to you.
Ours was perfectly calibrated to how plausible the reasoning felt and
perfectly uncorrelated with whether the code worked. Any agent whose only
quality signal is self-reported confidence has no quality signal.

**2. The verifier has to be able to say no in a way the generator cannot argue
with.** Ours rejected four patches across both arms — including one from its
own agent that had already passed an earlier, weaker version of itself. That
only worked because the verifier ran the code rather than reading it. A
verifier built from the same model, reading the same diff, would have agreed
with the generator, because it shares the reasoning that produced the mistake.

**3. Adding a check means re-checking everything it already accepted.** When we
hardened the validator, we re-ran it over all fourteen previously accepted
patches; two more failed. Acceptance under a weaker rule set is not evidence,
and a system that only applies new checks going forward is quietly carrying
whatever the old checks missed.

The uncomfortable corollary: **an agent's most valuable component may be the
part that tells it it's wrong.** It is unglamorous — a test harness, an
anti-cheat validator, a run counter — and it is where essentially all of the
engineering in this project went. The model was the easy part.

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
