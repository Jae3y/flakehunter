# Session log — submission state

Last updated at the end of the Saturday unattended session.
**48 commits**, clean tree, **93 tests passing**, self-audit 4/4,
deliverables 15/16.

---

## What's left for the human

Everything else is done. These genuinely cannot be finished without you.

### 1. Film the video — the only hard blocker

`docs/VIDEO_OUTLINE.md` is a complete filming plan: seven timed beats, the
artifact to put on screen for each, a ten-shot capture list, and what to leave
out. Every file, command and number it names has been verified to exist and to
still contain the figure claimed:

```bash
python scripts/verify_video_shots.py     # 9/9 verified
```

Re-run that immediately before recording — the numbers move if anything is
re-measured.

### 2. Approve or reject the pending patch

`results/pending_approval/case_12_masking_trap/` — one patch, with its
root-cause writeup, the discriminating experiment (1.0% → 44.0% under
contention), seven validator checks and a 0/500 verification. **It has not been
applied.** `corpus/` is clean against HEAD, verified by the self-audit rather
than asserted.

Record your decision with:

```bash
python scripts/record_decision.py case_12_masking_trap --approve --note "your reason"
python scripts/record_decision.py case_12_masking_trap --reject  --note "your reason"
```

This **closes the human checkpoint** — it writes a `human.reviewer` turn to the
trajectory carrying `decision: approved` or `rejected` instead of leaving the
agent's `pending` open forever, drops a `DECISION.md` into the package, and
updates the case's status.

Approving does **not** install the patch. Add `--apply` when you also want it
copied over `corpus/case_12_masking_trap/project/`, so "this looks right" and
"write it into the repository" stay two separate acts.

### 3. Submit to HackerEarth

Not something I can do.

### 4. Decide how hard to lean on the headline

The measurement says: **claimed 12/12 → verified 10 → legitimate 10/12**, every
patch at `high` confidence. That is a sharper and more honest claim than "our
agent fixes more flaky tests", and it is also a less conventional one. Which
framing goes in the submission is a judgement call about audience, not a
technical question.

### 5. Optional: finish the agent arm

Nine cases are one working API allowance away. Everything is built, tested and
checkpointed; the command is `docker compose run --rm flakehunter python
scripts/run_agent.py`. Needs billing that actually lifts the free-tier cap —
enabling it once did not (see below). **Nothing in the headline finding depends
on this.**

---

## Case coverage

| Arm | Coverage |
|---|---|
| **Baseline** | **12/12 complete** |
| **Agent** | 1 complete (case 12, PENDING), 1 UNRESOLVED with full evidence (case 07), 9 checkpointed mid-loop, 1 excluded for runtime (case 01) |

All nine incomplete cases carry a **banked CONFIRM measurement** — 200-run
repeat-execution batches no future run has to pay for again. Case 05 additionally
carries 3 rounds and 2 experiments with one root-cause class eliminated.

```
case_02  confirm 67/200   case_08  confirm  9/200
case_03  confirm 56/200   case_09  confirm 44/200
case_04  confirm 14/200   case_10  confirm 20/100 (serial)
case_05  confirm 14/200, 3 rounds, 2 experiments, 1 eliminated
case_07  confirm 54/200   case_11  confirm 62/200
```

**Blocker:** Google AI Studio free tier, **20 requests/day/model**. Billing was
enabled mid-project and three calls succeeded, but the daily cap continued to
be enforced on this key.

Two changes were made to survive it rather than wait on it (`DECISIONS.md`
D-018, D-019): checkpoint/resume, and merging the two per-round LLM calls into
one. Per-round cost 2 requests → 1; a one-round success 3 → 2. The effect was
visible the same day — case 05 resumed and reached round 3 where it had
previously reached round 1, and a subsequent 8-case run took **1 minute**
instead of ~20 because checkpointed cases skip CONFIRM.

---

## The case 07 evidence-overwrite bug — fixed structurally

**What was wrong.** `run_agent.py` wrote `results: [o.to_dict() for o in
outcomes]` — this invocation's cases and nothing else. Two failure modes at
once: cases absent from a run vanished, and cases present overwrote whatever
was there regardless of whether the new record knew anything. A run in which
every case died on quota replaced case 07's two hypothesis rounds, two
experiments and three validator rejections with a bare `ERROR`.

**The fix.** `src/agent/results_store.py`. Merge per case, and **replace only
when the incoming record has equal or greater evidence depth** — ordered by
terminal status, then whether a 500-run verification exists, then experiments,
rounds, validations. A quota failure establishes nothing, ranks bottom, and
therefore cannot displace anything; no quota special-case was needed. Ties go
to the newer record so a genuine re-run still wins. A refused overwrite appends
to `superseded_attempts`, so the attempt stays visible.

**Confirmed working, live.** A simulated quota-only run over the real results
file changed **nothing** and recorded a superseded attempt on case 07.
Ten tests in `tests/test_results_store.py` pin it, including the exact
regression. Logged as D-022.

---

## Deliverables — 15/16

`python scripts/deliverables_check.py`

| Deliverable | Status |
|---|---|
| **1. Solution code** | 8/8 core modules; Improvement Changelog (8 entries); **Primary Failure Mode** and **Hot Take** as their own labelled sections; user/bottleneck/why-it-matters explicit at the top |
| **2. Reproduction guide** | 17 command blocks, pinned versions, runtime **and request cost** per phase — **and actually tested in a clean clone** |
| **3. Video** | Outline + verified shot list (9/9). **The video itself is not done** — needs filming |
| **4. Agent trajectories** | 58 JSONL files, indexed in `traces/README.md`, showing tool calls, responses, reflections, retries, validator rejections and human checkpoints |

### Reproduction guide — tested, not asserted

Cloned to a clean directory and every step run in order. **It did not work
perfectly as written.** Three corrections, now applied:

1. Test count was 82, actually **92** (93 now); runtime needed a range, not a
   point (90–200 s, machine-load dependent).
2. Corpus baseline documented at 383 s; the clean-clone run took **552 s**.
   Now a range, both figures real.
3. **`self_audit.py` reported a false failure after step 3.** `measure_corpus`
   writes measured baselines into `corpus/*/metadata.json` by design, and the
   audit flagged *any* corpus change as a possible patch application. Anyone
   following the guide would have hit a scary and wrong result. Now separates
   code changes from re-measured metadata.

Also documented: `--stress` on `revalidate_pending.py` is load-bearing. Without
it the clean-clone run reported `legitimate 11/12` instead of `10/12`, because
the behavioural check is exactly what catches case 07's mask.

Steps 4–5 (the two LLM arms) could not run in the clean clone — quota.

### Trajectories — a gap found and closed

The agent loop **never emitted a `human_checkpoint` turn**. Only the Phase 0
demos did, yet the APPROVE step *is* the checkpoint. `agent.approve` now
records one, with `decision: "pending"` rather than `"approved"` — a system
approving its own work is not a checkpoint.

`traces/README.md` indexes the six files worth opening out of 58: a loop that
worked end to end, three validator rejections fed back into re-authoring, a
human checkpoint, the retroactive re-validation, the merged round call, and gap
markers under a failing tracer.

---

## Results

| | Baseline | Agent |
|---|---|---|
| Cases attempted | 12/12 | 2/12 |
| Verified at zero failures | **10/12** | 1/2 |
| Accepted by the validator | **10/12** | 1/2 |
| Root cause identified | 12/12 | 1/2 |

**Claimed 12/12 → verified 10 → legitimate 10/12.** Every patch carried `high`
confidence.

The two failures, for different reasons:

- **case 01** — the patch **never compiled** (`def __init__( -> None:`). All 500
  runs errored during collection; it reported a `0.00%` residual, the most
  flattering number in the table, from code that never ran.
- **case 07** — a **confirmed mask**: 0.80% at the normal worker count, **49/200
  (24.5%) at 32 workers**. It widened the timing window rather than closing it.

---

## Decisions — 22 in `DECISIONS.md`

Since the last log: **D-018** checkpointing with model provenance, **D-019**
merged round call, **D-020** the flaky test in our own suite, **D-021** the case
06 contamination blast radius (none — verified four ways), **D-022** the
evidence-merge rule.

Two corrections are recorded rather than quietly edited: **D-011** misattributed
case 01's errors to a CPU limit without reading stderr (it was a syntax error),
and the case 06 checkpoint leak reached no reported number, confirmed by four
independent checks rather than asserted.

---

## Self-audit — 4/4

`python scripts/self_audit.py`

| Check | Result |
|---|---|
| Changelog entries carry measurements | **PASS** — 8 entries |
| Decisions carry tension/choice/reasoning/revisit | **PASS** — 22 entries |
| Every finished case PENDING or UNRESOLVED | **PASS** |
| No patch applied outside the sandbox | **PASS** — no corpus *code* modified; 1 patch awaiting approval |

The audit has caught three of its own author's mistakes this project: a
decision missing its revisit trigger, a rejected package being counted as
awaiting approval, and the false failure after reproduction step 3.

---

## Known limitations, stated plainly

1. **Verification can still be fooled.** We sample run count well (500) and
   stress with a single arbitrary point (4× oversubscription). The masking demo
   shows a retry surviving a workload 8× the corpus and only breaking at 60×.
   We catch masks whose headroom is smaller than the stress we happen to apply,
   and we do not know where that ceiling sits. Written up as the README's
   Primary Failure Mode.
2. **Agent-arm coverage is 1 complete case.** Quota, not capability. The
   headline finding does not depend on it — it is measured from the baseline
   arm, the 500-run verifications and the validator.
3. **Absolute flake rates are session-local.** Case 07 moved 2.5% → 34.0%
   serial across hours with no code change. Comparisons must be paired within a
   session; the primary metric (zero after a real fix) is unaffected.
4. **Twelve seeded cases, one language.** The one piece of evidence that this
   generalises is the flaky test found in our own suite, which we did not plant.
