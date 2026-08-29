# Unattended session log — 2026-08-29

Read **Read this first**. There is one finding that changes what the submission
should claim, and one hard blocker that stopped the agent arm at 2 of 12 cases.

---

## Read this first

**1. The baseline is far stronger than the premise assumed — and that turned
out to be the interesting result, not a problem.**

A single call to `gemini-3.6-flash`, given the taxonomy and an explicit rule
that widening a timing window is not a fix, produced a patch for **12/12**
cases and reported `high` confidence on **every single one**. It even got case
12, the masking trap, correct — the real publication-ordering fix, not a sleep,
not a retry.

Re-running each of those patches 500 times: **10 of 12 were actually fixes.**

| | Count |
|---|---|
| Patches produced | 12/12 |
| Reported `high` confidence | **12/12** |
| Verified at zero failures | **10/12** |
| Confident patches that were **not** fixes | **2** |

The two false greens:

- **case_07** — correct root cause, plausible retry fix, **0.80% residual**
  (4 failures in 500 runs). One execution would never surface it.
- **case_01** — the correct fix (a lock), but every verification run *errored*
  on a CPU limit. A `0.00%` residual that meant nothing.

Nothing distinguished those two from the ten that worked except running the
test hundreds of times. **That is the gap the harness fills, and it is a
sharper claim than "the agent fixes more".** The baseline's problem is not
competence, it is that it cannot audit itself.

The live agent run on case 07 is the counterpart: it spent five rounds, had
three patches rejected by the validator, and **declined to declare success** on
the same case the baseline called `high` confidence.

**2. Stopped by API quota, not by the code.** Free tier is 20 requests/day/model.
Nine cases never got a live agent run. Details below.

---

## Blocking issue — API quota

```
HTTP 429  RESOURCE_EXHAUSTED
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaValue: 20
```

`GEMINI_API_KEY` is set and valid. Several live calls succeeded on two models
before each hit its cap, so this is a **quota ceiling, not a credential
problem** — but the ceiling is the free-tier 20/day, not a Pro allowance. If the
key is meant to be on an active Google AI Pro subscription, **it is not
receiving that quota**; that is the one piece genuinely outside my control.

**Cases with a live agent run (2):**

| Case | Model | Outcome |
|---|---|---|
| 12 masking trap | `gemini-3.6-flash` | **PENDING** approval, 0/500 |
| 07 network timeout | `gemini-3.5-flash` | **UNRESOLVED**, 5 rounds |

**Cases with no live agent run (9):** 02, 03, 04, 05, 06, 08, 09, 10, 11 —
all `NOT RUN (quota)` or `ERROR (quota)` in the results table.
**Excluded from both arms (1):** case 01, for runtime (D-012).

I did not switch models to finish the remaining cases: same-model-both-arms is
non-negotiable, and a baseline on one model against an agent on another
measures the models rather than the methods (D-013).

**To finish:** `docker compose run --rm flakehunter python scripts/run_agent.py`
after the quota resets, or on a paid tier. ~4 calls per case, ~44 for eleven.

---

## Results table

Full version in `results/RESULTS.md`, regenerate with `scripts/run_compare.py`.

| Case | Root cause | Corpus flake | Baseline after fix | Agent after fix | Cause? B/A | Agent status |
|---|---|---|---|---|---|---|
| 01 race condition | `race_condition` | 33.40% | 0.00% *(unsound)* | – | Y / – | EXCLUDED (runtime) |
| 02 test order dependency | `test_order_dependency` | 32.80% | 0.00% | – | Y / – | ERROR (quota) |
| 03 port collision | `resource_leak_port_collision` | 38.80% | 0.00% | – | Y / – | NOT RUN (quota) |
| 04 clock dependence | `clock_dependence` | 4.00% | 0.00% | – | Y / – | NOT RUN (quota) |
| 05 set iteration order | `hash_iteration_order` | 2.20% | 0.00% | – | Y / – | NOT RUN (quota) |
| 06 unseeded randomness | `unseeded_randomness` | 28.00% | 0.00% | – | Y / – | NOT RUN (quota) |
| **07 network timeout** | `network_timeout_no_retry` | 5.80% | **0.80%** | – | Y / n | **UNRESOLVED** (5 rounds) |
| 08 tempfile collision | `tempfile_collision` | 5.40% | 0.00% | – | Y / – | NOT RUN (quota) |
| 09 float tolerance | `float_tolerance` | 12.00% | 0.00% | – | Y / – | NOT RUN (quota) |
| 10 async ordering | `async_ordering` | 27.00% | 0.00% | – | Y / – | NOT RUN (quota) |
| 11 cache leak | `cache_leak` | 31.60% | 0.00% | – | Y / – | ERROR (quota) |
| **12 masking trap** | `publication_ordering` | 3.20% | 0.00% | **0.00%** | Y / Y | **PENDING** |

| Metric | Baseline | Agent |
|---|---|---|
| Cases attempted | 12/12 | 2/12 |
| Verified at zero failures | **10/12** | 1/2 attempted |
| Root cause identified | 12/12 | 1/2 attempted |
| Tokens | 79,455 | 72,733 |

Validator rejections (patches refused and re-authored): **3**.

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

## Decisions taken without you — 14 in `DECISIONS.md`

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

---

## Cost

Tokens, not dollars — no published rates for these models, and a fabricated
figure is worse than an honest count (D-007). Output includes reasoning tokens,
which Gemini bills as output.

| Arm | Prompt | Output | Total |
|---|---|---|---|
| Baseline, 12 cases | 13,616 | 65,839 | 79,455 |
| Agent, 2 live cases + blocked attempts | ~18,900 | ~53,800 | 72,733 |
| **Total** | | | **~152,000** |

Excludes provider probes and dry runs.

---

## Self-audit — 4/4

Run `python scripts/self_audit.py`. It inspects files and git rather than
asserting compliance.

| Check | Result |
|---|---|
| Changelog entries carry real measurements | **PASS** — 001: 18, 002: 68, 003: 67, 004: 17, 005: 15 |
| Decisions carry tension/choice/reasoning/revisit | **PASS** — 14 entries, all four sections |
| Every finished case PENDING or UNRESOLVED | **PASS** — 1 PENDING (with package), 1 UNRESOLVED, 2 ERROR |
| No patch applied outside the sandbox | **PASS** — `corpus/` clean against HEAD; the only patch is held in `results/pending_approval/` |

It caught one real gap earlier — D-012 had a "Remedy" section but no "Revisit
if." trigger — which I fixed rather than argued with.

**Caveat on the third check:** it passes because `ERROR` is an accepted
terminal status, which flatters the run. Quota-blocked cases are neither
PENDING nor UNRESOLVED; calling them UNRESOLVED would misrepresent an
infrastructure limit as a reasoning failure, so they stay ERROR and the
shortfall is stated here.

---

## Commits

16 this session, `a2a0be6..HEAD`. Highlights: `91adcab` drift diagnosis,
`cc41db9` CPU limit, `c1d4f21` truncation, `bad13ee` stuck-loop tests +
experiment soundness, `e8215fc` results table.

---

## What to review first

1. **`results/pending_approval/case_12_masking_trap/`** — the one patch awaiting
   you. `ROOT_CAUSE.md` has the diagnosis, the discriminating experiment
   (1.0% → 44.0%), all seven validator checks, and the 0/500 verification.
   Nothing was applied.

2. **The claimed-versus-verified section** in `results/RESULTS.md`. Twelve
   confident patches, two of them wrong, indistinguishable without the harness.
   That is a better framing for the submission than "the agent fixes more", and
   choosing how to pitch it is your call.

3. **The quota ceiling.** Nine cases are one working API allowance away from a
   complete result. Everything needed to run them is built and committed.
