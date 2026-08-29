# Unattended session log — 2026-08-29

Read **Read this first**. There is one finding that changes what the submission
should claim, and one hard blocker that stopped the agent arm at 2 of 12 cases.

---

## Read this first

**1. The comparison is claimed → verified → legitimate, and it narrows twice.**

The one-shot baseline returned a patch for **12/12** cases and attached `high`
confidence to **every one**. Two further questions cut it down:

| | Count |
|---|---|
| Patches produced | 12/12 |
| Reported `high` confidence | **12/12** |
| Verified at zero failures over 500 runs | **10** |
| Accepted by the anti-cheat validator | **10/12** |

The two that fail, and they fail for different reasons:

- **case 07** — correct root cause, `high` confidence, **0.80% residual**. The
  validator drives it to **49/200 (24.5%) at 32 workers**: not an incomplete
  fix but a **confirmed mask**, widening the timing window rather than closing
  it.
- **case 01** — the patch **never compiled** (`def __init__( -> None:`). All
  500 verification runs errored during collection and it reported a residual
  flake rate of **0.00%** — the most flattering number in the table, from code
  that never ran.

Nothing in the model's own output separated those from the ten that worked.
That separation came entirely from running the tests and from the validator,
neither of which the baseline has.

**2. The validator was defeated once, and the fix was retroactive.**

The agent's case 07 patch passed all seven checks then in force — including the
stress pass — while raising a timeout 1000× *and* setting the test fixture's
`SERVICE_WORK_S` to **0.0**, deleting the delay that produces the flakiness. It
survived the stress check because oversubscribing the CPU stretches a timing
window, and there was no window left to stretch.

Two checks were added (`test_conditions_unchanged`, `patch_parses`) and **all
14 previously-accepted patches were re-checked**, on the principle that
acceptance under a weaker rule set is not evidence. That re-check is what
surfaced case 01's syntax error and case 07's mask.

**3. I got a diagnosis wrong and corrected it on the record.** D-011 attributed
case 01's 500 error runs to `RLIMIT_CPU`, inferred from the exit code without
reading the stderr. Raising the CPU budget changed nothing, which is what
finally prompted reading it. A syntax error and a resource kill both exit
pytest 2. D-011 now carries the correction rather than being quietly edited.

**4. Quota, still.** Free-tier 20/day per model is still enforced on this key
despite billing. Nine cases have no live agent run, and case 07's requested
fresh attempt was blocked before its first call.

---

## Blocking issue — API quota, still capped after billing

```
HTTP 429  RESOURCE_EXHAUSTED
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaValue: 20     model: gemini-3.6-flash
```

Billing was enabled and **three consecutive calls on `gemini-3.6-flash`
succeeded**, so the key works. The daily 20-request free-tier cap is
nonetheless still being applied to it. The run got through case 07 plus one
further call before hitting the ceiling again.

That is the one thing genuinely outside my control. Everything else moved
forward.

**Cases with a live agent run (2):** 07 and 12, both on `gemini-3.6-flash`, the
baseline's model.

**Cases with no live agent run (9):** 02, 03, 04, 05, 06, 08, 09, 10, 11.

**Excluded from both arms (1):** case 01, for runtime (D-012).

**To finish:** `docker compose run --rm flakehunter python scripts/run_agent.py`
once the cap genuinely lifts. ~4 calls per case, ~40 for the nine.

---

## The case 07 retry — what changed

You asked for this specifically, since the node-id bug had poisoned the first
attempt's round budget.

| | First attempt | Retry |
|---|---|---|
| Model | `gemini-3.5-flash` | `gemini-3.6-flash` |
| Rounds used | 5 (hit the cap) | **2** |
| Invalid experiments | **2** (invented node ids) | **0** |
| Patch attempts | 3, all rejected | 2, second accepted |
| Wall clock | 16.8 min | 5.4 min |
| Outcome | UNRESOLVED | 0/500 verified — **then rejected on re-validation** |

**The first failure was substantially an artefact of the bug.** With real
evidence the loop converged in two rounds instead of exhausting five.

But the retry surfaced something worse, and two honest wrinkles:

- **It eliminated the correct hypothesis on its own bad prediction.** Round 1
  proposed `network_timeout_no_retry` — the recorded class — and predicted
  `serialize_execution` would eliminate the failure. Observed 11.5% → 6.0%,
  classified *unchanged*. The prediction was wrong, so the loop discarded the
  right answer and settled on `clock_dependence` in round 2.
- **The patch it then produced was a mask**, and the validator of the day
  accepted it. See point 2 above.

So case 07 has now defeated the agent twice, for two different reasons. It is
the hardest case in the corpus and remains UNRESOLVED — while the baseline
"fixed" it with `high` confidence at 0.80% residual. It stays the anchor
example.

---

## Results table — all 12 cases

Full version with the claimed-vs-verified analysis in `results/RESULTS.md`.

| Case | Root cause | Corpus flake | Baseline after fix | Baseline verified? | Agent after fix | Cause? B/A | Agent status |
|---|---|---|---|---|---|---|---|
| 01 race condition | `race_condition` | 33.40% | 0.00% *(unsound)* | **no** | – | Y / – | EXCLUDED (runtime) |
| 02 test order dependency | `test_order_dependency` | 32.80% | 0.00% | yes | – | Y / – | quota |
| 03 port collision | `resource_leak_port_collision` | 38.80% | 0.00% | yes | – | Y / – | quota |
| 04 clock dependence | `clock_dependence` | 4.00% | 0.00% | yes | – | Y / – | quota |
| 05 set iteration order | `hash_iteration_order` | 2.20% | 0.00% | yes | – | Y / – | quota |
| 06 unseeded randomness | `unseeded_randomness` | 28.00% | 0.00% | yes | – | Y / – | quota |
| **07 network timeout** | `network_timeout_no_retry` | 5.80% | **0.80%** | **no** | 0.00% *(patch rejected)* | Y / n | **UNRESOLVED** |
| 08 tempfile collision | `tempfile_collision` | 5.40% | 0.00% | yes | – | Y / – | quota |
| 09 float tolerance | `float_tolerance` | 12.00% | 0.00% | yes | – | Y / – | quota |
| 10 async ordering | `async_ordering` | 27.00% | 0.00% | yes | – | Y / – | quota |
| 11 cache leak | `cache_leak` | 31.60% | 0.00% | yes | – | Y / – | quota |
| **12 masking trap** | `publication_ordering` | 3.20% | 0.00% | yes | **0.00%** | Y / Y | **PENDING** |

| Metric | Baseline | Agent |
|---|---|---|
| Cases attempted | 12/12 | 2/12 |
| Verified at zero failures | **10/12** | 1/2 attempted |
| Root cause identified | 12/12 | 1/2 attempted |
| Tokens | 79,455 | 45,587 |

Both arms on `gemini-3.6-flash`. Validator rejections: **1** in-loop, plus
**1 on re-validation** (case 07).

---

## The two live agent runs, in detail

**case_12 — PENDING, 1 round.** CONFIRM 2/200 (1.0%) → two competing
hypotheses (`publication_ordering` vs `race_condition`), each with a
distinguishing prediction → `amplify_contention(2)` predicted *increased*,
observed **1.0% → 44.0%** → confirms publication ordering → patch → **7/7
validator checks** including 0/200 at 32 workers → **VERIFY 0/500** → approval
package written, nothing applied.

**case_07 — UNRESOLVED, 5 rounds, 16.8 min.** Five hypotheses, five
experiments, three patch attempts, all three rejected (`survives_stress` ×2,
`modifies_source` ×1). Correct outcome: the agent refused a fix it could not
verify, on the exact case where the baseline claimed success.

---

## A bug the live run exposed

case_07 round 4 ran `isolate_test(test_network_timeout)` against a case whose
only test is `test_status_is_fetched_from_a_healthy_service`. pytest collected
nothing, all 150 runs **errored**, `flake_rate` read **0.0%**, and the loop
scored that as *eliminated* — manufacturing evidence for whichever hypothesis
the experiment targeted, and sending it after the wrong root-cause class for
the remaining rounds.

Same bug as counting an ERROR run as a pass during verification. The rule
existed since Phase 0; it had never been applied in the experiment layer.
Fixed both ways: unsound batches now yield **no evidence**, and the designer is
handed the case's **real node ids** so it cannot invent one. (D-014)

The validator contained the damage — all three resulting patches were rejected
and nothing wrong was accepted — but four rounds were burned on false evidence.

---

## Stuck-loop detector — now actually tested

It had never fired in a live run. `tests/test_orchestrator_stuck.py` drives it
with a scripted model that names the same root cause every round and designs an
experiment that cannot confirm it. **Zero API calls**; real sandbox, harness and
corpus. Five tests, all passing:

- fires as UNRESOLVED with a reason naming the repeated hypothesis
- fires on **round 3 of 5** — before the arbitrary cap, so the signal is
  distinguishable from the cap
- records the hypotheses and experiments it tried
- produces **no patch** and reaches no approval directory
- leaves a contiguous, readable trajectory

**73 tests pass** overall, including 16 that drive the validator directly —
both that it accepts a legitimate fix containing a retry, and that it rejects
each cheat it exists for.

---

## Drift — resolved

The corpus rates were moving between sessions. Cause: **our harness was
creating the flakiness, not amplifying it.** Case 01 flaked **0.0% serially,
25% at 8 workers**; case 10 the same. Case 06, whose nondeterminism is
intrinsic, sat at 23–27% in every condition — the control that proved it.

Within-session repeatability was fine (overdispersion 0.83× and 0.53×), so
sample size was never the issue. Both cases were rebuilt so the nondeterminism
is intrinsic (27.3% and 22.7% serial). Protocol locked in
`src/harness/protocol.py`, read by both arms.

**Removed experiment:** an idle-CPU probe found no slowdown (−4.2%) while case
01 varied 36.8–56.8%. It measured single-core speed with the workers idle,
which cannot see all-core throttling under load. Abandoned.

**Not fixed:** the host itself drifts. Case 07 moved 2.5% → 34.0% serial across
hours with no code change. Absolute "before" numbers are session-local;
comparisons must be paired. The primary metric is untouched — a real fix gives
zero in any machine state.

---

## The validator — and its own evolution

Case 07's correct fix *is* a retry (`network_timeout_no_retry`); case 12's
masking fix is *also* a retry. No pattern match separates them, so the
validator does not try.

**Structural** (AST, no execution): patch parses; assertions not removed or
made trivially true; no skip/xfail/flaky marker; exact comparisons not loosened
to `approx`; source actually modified; `conftest.py` untouched; **test-level
condition constants unchanged**; a bare sleep is not the whole patch.

**Behavioural**: re-verify at 4x CPU oversubscription, with three outcomes —
clean, failures returned, or **inconclusive** (the batch errored, so nothing
was learned). The third used to be reported as "the failure returns under
load", a false accusation about a run in which no test executed.

Two checks were added mid-session, each because a patch got past the validator:

| Check | Added because |
|---|---|
| `test_conditions_unchanged` | Agent set `SERVICE_WORK_S` to 0.0, deleting the flakiness condition, and passed the stress pass — there was no window left to stretch |
| `patch_parses` | Baseline's case 01 patch never compiled; 500 runs errored and reported 0.00% residual |

### Retroactive re-check — all 14 accepted patches, stress on

| Arm | Case | Verdict | Cause |
|---|---|---|---|
| agent | 07 | **REJECTED** | `test_conditions_unchanged` |
| agent | 12 | valid | — |
| baseline | 01 | **REJECTED** | `patch_parses` — line 16, invalid syntax |
| baseline | 07 | **REJECTED** | `survives_stress` — 49/200 (24.5%) at 32 workers |
| baseline | 02–06, 08–12 | valid | — |

**Agent 1/2. Baseline 10/12.** `scripts/revalidate_pending.py` runs this in one
command and writes a `REVALIDATION.md` — carrying the complete diff — into any
package that no longer passes, so a verdict can be checked rather than trusted.

---

## Decisions taken without you — 17 in `DECISIONS.md`

| # | Decision |
|---|---|
| D-001 | Gemini via stdlib `urllib`, no new dependency |
| D-002 | Select the model by *calling* candidates — a listed model can still 404 |
| D-003 | Keep the trajectory field named `model` (brief-mandated schema) |
| D-004 | Remove `ANTHROPIC_API_KEY` rather than leave a dead credential |
| D-005 | One shared `src/llm/` client so the arms cannot drift apart |
| D-006 | Validator tests behaviour, not syntax |
| D-007 | Cost in tokens; no published rates exist for these models |
| D-008 | One protocol module owns settings for both arms |
| D-009 | Rebuild cases 01 and 10 rather than measure around manufactured flakiness |
| D-010 | Kill a wedged 4-hour job rather than wait it out |
| D-011 | Scale `RLIMIT_CPU` with the thread budget |
| D-012 | Leave case 01 expensive, flag it, order it last |
| D-013 | Quota: a fair same-model subset, never a mixed comparison |
| D-014 | An unsound experiment produces no evidence, not a zero |
| D-015 | Agent arm returns to the baseline's model once billing allowed it |
| D-016 | Re-validate **every** previously accepted patch, not just the one that failed |
| D-017 | The validator checks that a patch compiles (**corrects D-011's misdiagnosis**) |

---

## Cost — total for the session

Tokens, not dollars (D-007). Output includes reasoning tokens, which Gemini
bills as output.

| Run | Model | Prompt | Output |
|---|---|---|---|
| Baseline, 12 cases | `gemini-3.6-flash` | 13,616 | 65,839 |
| Agent, this run (07 + blocked) | `gemini-3.6-flash` | 10,778 | 27,106 |
| Agent, case 12 | `gemini-3.6-flash` | 3,758 | 7,643 |
| Agent, superseded 3.5 subset | `gemini-3.5-flash` | 15,024 | 50,006 |
| **Total** | | **43,176** | **150,594** |

**193,770 tokens** across the session, excluding provider probes and dry runs.

---

## Self-audit — 4/4

`python scripts/self_audit.py`. Inspects files and git rather than asserting.

| Check | Result |
|---|---|
| Changelog entries carry real measurements | **PASS** — 001: 18, 002: 68, 003: 67, 004: 17, 005: 15, 006: 9 |
| Decisions carry tension/choice/reasoning/revisit | **PASS** — 15 entries, all four sections |
| Every finished case PENDING or UNRESOLVED | **PASS** — 1 UNRESOLVED, 4 ERROR (quota) |
| No patch applied outside the sandbox | **PASS** — `corpus/` clean against HEAD; **1 awaiting approval** (case 12), **1 rejected on re-validation and marked do-not-apply** (case 07) |

The audit found and I fixed two real gaps of its own this session: D-012 was
missing a "Revisit if." trigger, and the corpus check was counting a rejected
package alongside a valid one, overstating how much is actually ready to apply.

**Caveat, still standing:** the third check passes because `ERROR` is an
accepted terminal status, which flatters the run. Quota-blocked cases are
neither PENDING nor UNRESOLVED; calling them UNRESOLVED would misrepresent an
infrastructure limit as a reasoning failure.

---

## Commits

16 this session, `a2a0be6..HEAD`. Highlights: `91adcab` drift diagnosis,
`cc41db9` CPU limit, `c1d4f21` truncation, `bad13ee` stuck-loop tests +
experiment soundness, `e8215fc` results table.

---

## What to review first

**1. `results/RESULTS.md`, the claimed → verified → legitimate table.** Twelve
patches, twelve `high` confidences, ten that hold. That is the submission's
strongest claim, now backed by the validator as well as the 500-run
verifications.

**2. `results/archive/rejected_patches/case_07_attempt1_rejected/REVALIDATION.md`**
— the catch. The agent's patch passed every check in force at the time while
doing two masking things, and the record carries the full diff so you can
disagree with the verdict.

**3. `results/pending_approval/case_12_masking_trap/`** — the one patch still
standing. 7/7 checks, 0/200 at 32 workers, 0/500 verification, re-validated
against current rules. Nothing applied; `corpus/` clean against HEAD.

**4. The quota.** Free-tier 20/day is still enforced on this key despite
billing. Nine cases plus case 07's fresh attempt are one working allowance away
from a complete agent arm; everything to run them is built, tested and
committed.
