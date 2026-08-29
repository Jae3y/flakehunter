# Unattended session log — 2026-08-29

Written at the end of an unattended run. Read the **Read this first** section
before anything else; there is one finding that changes how the rest should be
interpreted, and one hard blocker that stopped the run short.

---

## Read this first

**1. The one-shot baseline solved 10 of 12 cases, including case 12.** Given
the root-cause taxonomy and an explicit rule that widening a timing window is
not a fix, a single call to `gemini-3.6-flash` produced the *correct* repair
for the masking trap — build the index locally, assign it, then set `ready`.
Not a sleep, not a retry. The trap did not trap it.

The premise that a one-shot baseline would fail on these cases does not hold
against a current flash-tier model with a good prompt. The agent's advantage
has to be argued somewhere else, and there is a real one: **the baseline cannot
tell which 10 it got right.** It reported `high` confidence on case 07, whose
fix still fails 0.8% of the time — four failures in 500 runs, invisible to a
system that never executes anything. That is the honest framing of the value:
not "fixes more", but "knows whether it fixed it".

**2. The run was stopped by API quota, not by the code.** The Gemini free tier
allows **20 requests per day per model**. The baseline consumed that allowance;
the agent arm needs roughly 44 calls for eleven cases. The full agent arm did
not run. See "Blocking issue" below.

---

## Blocking issue

```
HTTP 429  RESOURCE_EXHAUSTED
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaValue: 20     model: gemini-3.6-flash
```

`GEMINI_API_KEY` is set and valid — this is not a credential problem. The free
tier's 20-requests-per-day-per-model cap is the constraint, and the baseline
arm alone (12 cases + 2 re-runs + probes) exhausted it.

Quota is scoped per model, so finishing the agent on a different model was
available. **I did not do that**, because comparing a baseline on one model
against an agent on another measures the models rather than the methods, which
is the confound the "same model for both arms" rule exists to prevent. Instead
the exhausted-model results are archived as-is and a fair same-model subset was
attempted separately (D-013).

**To finish the agent arm:** wait for the daily reset and run

```bash
docker compose run --rm flakehunter python scripts/run_agent.py
```

or move off the free tier. At ~4 calls per case, eleven cases need ~44 requests.

---

## What completed

| Phase | Status | Commits |
|---|---|---|
| Drift resolution | complete | `91adcab`, `5c001e7` |
| Reproduction guide + self-audit | complete | `b908c79` |
| Phase 2 — baseline arm | **complete, 12/12 cases** | `c1d4f21`, `cc41db9`, `587b768`, `0fb2cd0` |
| Phase 3 — validator + agent loop | **built and proven on case 12; arm incomplete** | `91adcab`, `71d8999` |
| Phase 4 — full evaluation | **not reached** (quota) | — |

Ten commits this session, `a2a0be6..71d8999`.

---

## Drift diagnosis — what actually caused it

The corpus rates were moving between sessions with no code change (case 01 read
47%, 20.6%, 33.4%, 18.9%, 41.2%, 52.0%). Three explanations were measured
rather than argued about.

**Sample size was not the cause.** Five back-to-back batches of 500 runs gave
overdispersion of 0.83x for case 01 and 0.53x for case 06 — at or below the
binomial expectation. Within a session the measurement is sound.

**The harness was manufacturing the phenomenon.** Serial versus 8 workers:

| case | serial | 8 workers | drift |
|---|---|---|---|
| **01 race** | **0.0%** | 25.0% | 25.0 pts |
| **10 async** | **0.0%** | 11.5% | 11.5 pts |
| 03 port | 45.5% | 33.0% | 12.5 pts |
| 06 RNG (control) | 23–27% in every condition | | ~0 |

Case 01 does not flake at all when run alone. Eight concurrent runs
oversubscribing the CPU is what forced the GIL to preempt mid-update. Its rate
tracked machine load because load was never a controlled variable. Case 06,
whose nondeterminism is intrinsic to the code under test, was unmoved by
anything.

**Fix: rebuild the cases, not the protocol.** Case 01 raised from 25,000 to
50,000 iterations per worker (each worker's loop now outlasts the 5 ms GIL
switch interval unaided) → 27.3% serial. Case 10 changed from a work gradient
to equal panel cost → 22.7% serial. All twelve now flake when run alone.

**A removed experiment.** An idle-CPU probe timing a fixed single-threaded loop
between batches found no systematic slowdown (−4.2% over ten cycles) while case
01 varied 36.8–56.8%. It was measuring single-core speed with the workers idle,
which cannot see the all-core frequency drop sustained parallel load causes.
Abandoned.

**What is still not fixed.** Rebuilding removed the manufactured component, not
the host's instability. Between two sweeps hours apart with no code change,
case 07 moved 2.5% → 34.0% serial and case 08 1.0% → 24.0%. Consequences:
absolute "before" rates are session-local; comparisons must be paired within a
session; **the primary metric is untouched**, because a real fix gives zero in
any machine state.

---

## Locked measurement protocol

`src/harness/protocol.py`, read by both arms so they cannot be measured
differently:

- Verification: **500 runs**, every case, no exceptions.
- Workers: 8, except case 01 and case 10 pinned serial (measured drift past the
  10–15 point threshold).
- Confirm/experiment/stress halved for serial cases; verification never.

---

## Baseline arm — complete

`gemini-3.6-flash`, 500-run verification per case, locked protocol.

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

**Cause identified 12/12. Fixed 10/12.** 79,455 tokens, 25 min.

---

## Agent arm — proven on case 12, incomplete overall

Case 12 ran end to end on `gemini-3.6-flash` before quota ran out, and it is
worth reading as the demonstration that the loop works:

- **CONFIRM** — 2/200 failures (1.0%), one distinct signature.
- **HYPOTHESIZE** — two competing candidates: `publication_ordering` (ready set
  before the loop populates) and `race_condition` (unsynchronised dict access),
  each with a prediction that separates it from the other.
- **EXPERIMENT** — `amplify_contention(2)`, chosen because the two hypotheses
  predict different things about it. Predicted *increased*; observed **1.0% →
  44.0%**. Confirms publication ordering, eliminates the race.
- **PATCH → VALIDATE** — all seven checks passed first attempt, including the
  behavioural one: **0/200 failures at 32 workers (4× oversubscription)**.
- **VERIFY** — **0/500**.
- **APPROVE** — written to `results/pending_approval/case_12_masking_trap/`
  and marked PENDING. Not applied.

| Case | Status | Cause | Residual | Notes |
|---|---|---|---|---|
| 12 masking trap | **PENDING** | correct | **0.00%** (0/500) | 1 round, 1 experiment |
| 07 network timeout | ERROR | — | — | HTTP 429 quota |
| 01 race condition | excluded | — | — | runtime, D-012 |
| 02–06, 08–11 | not run | — | — | quota |

---

## The validator — the load-bearing piece

Case 07's correct fix *is* a retry (its root cause class is
`network_timeout_no_retry`); case 12's masking fix is *also* a retry. No
pattern match separates them, so the validator does not try.

**Structural checks** (AST, no execution): assertions not removed or made
trivially true, no skip/xfail/retry marker, no loosened comparison
(`pytest.approx` introduced where an exact one stood), source actually
modified, `conftest.py` untouched, a bare sleep is not the whole patch.

**Behavioural check**: re-verify under 4× CPU oversubscription. A sleep buys a
fixed headroom; a retry buys a fixed budget; a fix that removed the race has
neither to exhaust. Justified by measurement — `scripts/demo_masking_fix.py`
showed both masking fixes reaching 0/300 at the corpus workload (they would
pass a 500-run verification) and returning at 8% and 99% as the workload grew,
while the true fix held at 0% throughout.

---

## Decisions taken without you

Thirteen entries in `DECISIONS.md`, each with tension / choice / reasoning /
revisit trigger. Summary:

| # | Decision |
|---|---|
| D-001 | Call Gemini via stdlib `urllib`, adding no dependency |
| D-002 | Select the model by *calling* candidates, not by assuming — a listed model can still 404 |
| D-003 | Keep the trajectory field named `model` (brief-mandated schema) carrying the real Gemini string |
| D-004 | Remove `ANTHROPIC_API_KEY` entirely rather than leave a dead second credential |
| D-005 | One shared `src/llm/` client so the arms cannot drift apart |
| D-006 | Validator tests behaviour, not syntax (case 07 vs case 12 retry) |
| D-007 | Report cost in tokens; no published rates exist for these models |
| D-008 | One protocol module owns run counts and worker counts for both arms |
| D-009 | Rebuild cases 01 and 10 rather than measure around manufactured flakiness |
| D-010 | Kill a wedged 4-hour job rather than wait it out |
| D-011 | Scale `RLIMIT_CPU` with the thread budget |
| D-012 | Leave case 01 expensive, flag it, order it last |
| D-013 | Quota: a fair same-model subset, never a mixed comparison |

---

## Two bugs measurement caught that review would not have

**A false pass.** Case 01's baseline patch — adding a lock, the *correct* fix —
reported **0/500 failures with 500 error runs**. `RLIMIT_CPU` is summed across
threads, so eight threads hit a flat 10-second ceiling in ~2 s of wall time and
pytest exited 2 before any assertion ran. Zero failures, 0.00% residual,
nothing executed. Caught only because `ERROR` is a distinct outcome from `FAIL`
and `is_sound` is checked before a fix counts — a Phase 0 decision that had
never earned its keep until this moment.

**Truncated replies.** Cases 02 and 04 failed with `Unterminated string`. Not
malformed output — Gemini 3.x draws reasoning tokens from the same
`maxOutputTokens` budget, and 8,192 was spent almost entirely on thinking,
leaving ~478 characters of JSON. Raised to 32,768 with a retry at double on a
`MAX_TOKENS` finish; both cases then fixed cleanly.

---

## Stuck loops

None. The repeated-hypothesis detector never fired, because only one case
completed the loop and it resolved in a single round. The mechanism is
implemented and unit-covered but is **not yet evidenced by a real run** — treat
it as untested in anger.

---

## Cost

Reported in tokens. No published per-token rates for `gemini-3.6-flash` or
`gemini-3.7-flash` were available, and a fabricated dollar figure in a results
table is worse than an honest token count (D-007). Output counts include
reasoning tokens, which Gemini bills as output.

| Arm | Prompt | Output | Total |
|---|---|---|---|
| Baseline, 12 cases | 13,616 | 65,839 | 79,455 |
| Agent, case 12 | 3,758 | 7,643 | 11,401 |
| Agent, quota-blocked attempts | 1,207 | 822 | 2,029 |
| **Total** | **18,581** | **74,304** | **92,885** |

Excludes provider probes and dry runs. Every trajectory records per-turn
tokens, so a rate can be applied retrospectively without re-running anything.

---

## Self-audit

Run with `python scripts/self_audit.py`, which inspects files and git rather
than asserting compliance.

**4/4 passed**, but read the third with the caveat below.

| Check | Result |
|---|---|
| Changelog entries carry real measurements | **PASS** — 001: 18, 002: 68, 003: 67, 004: 17 measurements |
| Decisions carry tension/choice/reasoning/revisit | **PASS** — 13 entries, all four sections |
| Every finished case PENDING or UNRESOLVED with hypotheses | **PASS as written** — 1 ERROR, 1 PENDING with a complete package |
| No patch applied outside the sandbox | **PASS** — `corpus/` clean against HEAD; the only patch is held in `results/pending_approval/` |

The audit found one real gap and I fixed it rather than argued with it: D-012
had a "Remedy" section but no "Revisit if." trigger, so the decisions check
failed until it was added.

**Caveat on the third check.** It passes because `ERROR` is an accepted
terminal status, but that flatters the run. The checklist assumed the agent arm
would finish; cases blocked by quota are neither PENDING nor UNRESOLVED. Calling
them UNRESOLVED would misrepresent an infrastructure limit as a reasoning
failure, so they are left as ERROR and the shortfall is stated here instead of
being absorbed by a green tick.

---

## What to review first

**`results/pending_approval/case_12_masking_trap/`** — the one patch awaiting
your approval. `ROOT_CAUSE.md` carries the diagnosis, the discriminating
experiment (1.0% → 44.0% under contention), the seven validator checks, and the
0/500 verification. The patch is in `patched_files/`; nothing was applied.

Then the "Read this first" section above — the baseline being this strong
changes what the submission should claim, and that is a call for you, not me.
