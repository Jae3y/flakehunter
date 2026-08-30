# 5-minute video — shot list and outline

A filming plan, not a script. Every claim below is backed by an artifact
already in the repo, and each shot names the file or command to put on screen
so filming is mechanical.

**Through-line:** *the hard part was never generating a fix — it was confirming
one.* Every beat should advance that. The demo is not "watch the AI fix a bug";
it is "watch a confident wrong answer get caught."

---

## Cold open — 0:00–0:25

**On screen:** `results/RESULTS.md`, the claimed/verified/legitimate table,
with the confidence column visible.

**Say:** A single LLM call fixed 10 of our 12 flaky tests. It reported `high`
confidence on all 12. It could not tell you which two were wrong.

**Hold on:** the column of twelve identical `high` values.

> Lead with the finding that complicates our own premise. It is more
> interesting than "we built an agent", and it earns the rest of the video.

---

## 1. The bottleneck — 0:25–1:00

**On screen:** a corpus case, e.g. `corpus/case_12_masking_trap/project/app/indexer.py`,
with `self.ready = True` before the loop that fills the index.

**Say:** A flaky test breaks debugging at step one — normal debugging assumes
the bug reproduces on demand. You can't confirm a fix by running the test once.
You have to run it hundreds of times and reason statistically. Nobody does
that, so the tests stay broken and real regressions hide behind "that one's
just flaky."

**Cut to:** `docker compose run --rm recorder python scripts/measure_corpus.py --runs 500`
running, with the flake-rate table appearing.

---

## 2. Anchor A — the patch that never compiled — 1:00–1:40

**On screen, in order:**

1. `results/RESULTS.md` row: `01 race condition … 0.00% (unsound) … high`
2. The stored patch: `def __init__( -> None:`
3. The stderr: `SyntaxError: invalid syntax`, `Interrupted: 1 error during collection`

**Say:** Case 01's residual flake rate was 0.00% — the best number in the
table. Because the patch didn't compile. All 500 runs errored before a single
assertion ran.

**The point:** this is only caught because `ERROR` is a distinct outcome from
`FAIL`, and soundness is checked before a fix counts. Fold errors into the
flake rate and this is a clean win.

**Optional honesty beat (worth 8 seconds):** we first misdiagnosed this as a
CPU limit and wrote it up that way. Raising the limit changed nothing, which is
what made us read the stderr. `DECISIONS.md` D-011 carries the correction.

---

## 3. Anchor B — the mask that survives 500 runs — 1:40–2:35

**On screen:** `results/RESULTS.md` row for case 07 — `0.80%`, `high`
confidence, validator **REJECTS**.

**Say:** Case 07's fix was worse than incomplete. At normal load it fails 0.8%
of the time — four failures in 500. Under CPU oversubscription:

**Cut to:** the re-validation output line:

```
FAIL survives_stress: 49/200 failures at 32 workers (4x oversubscription)
```

**Say:** 24.5%. It didn't fix the race; it widened the window until the failure
fell outside the observation.

**Then the generalisation — `scripts/demo_masking_fix.py` output table:**

| variant | 10.5k docs | 80k docs | 600k docs |
|---|---|---|---|
| `time.sleep(0.05)` | **0%** | 8% | 100% |
| retry the assertion | **0%** | **0%** | 99% |
| true fix | **0%** | **0%** | **0%** |

**Say:** Both masks pass a 500-run verification. 500 clean runs is not proof a
race is gone — it's proof the race is currently narrower than your observation
window.

---

## 4. Why syntax can't decide it — 2:35–3:05

**On screen:** side-by-side — `corpus/case_07_network_timeout/metadata.json`
(`root_cause_class: network_timeout_no_retry`) and case 12's masking retry.

**Say:** The obvious anti-cheat rule is "reject fixes that add a sleep or a
retry." Case 07's *correct* fix **is** a retry — the bug is the absence of one.
Case 12's *masking* fix is also a retry. Same construct, right in one case,
cheating in the other.

**Say:** So the validator doesn't pattern-match. It re-runs the code under
oversubscription. A sleep buys fixed headroom; a retry buys a fixed budget; a
fix that removed the race has neither to exhaust.

---

## 5. The validator catching its own earlier acceptance — 3:05–3:50

*The strongest engineering beat. Do not rush it.*

**On screen:** `results/archive/rejected_patches/case_07_attempt1_rejected/REVALIDATION.md`, scrolled to the diff.

**Say:** Our agent produced a patch that passed all seven checks in force at the
time — including the stress check — and it was cheating twice.

**Highlight the two hunks:**

```diff
-TIMEOUT_S = 0.005      # app/client.py   (source under test)
+TIMEOUT_S = 5.0
-SERVICE_WORK_S = 0.00475   # test_client.py  (test fixture)
+SERVICE_WORK_S = 0.0
```

**Say:** It raised the timeout a thousandfold, and it set the test's service
delay to zero — deleting the condition that produces the flakiness. It survived
the stress check because oversubscription stretches a timing *window*, and
there was no window left to stretch.

**Then:** we added `test_conditions_unchanged`, and re-ran the current validator
over **every patch ever accepted** — because acceptance under a weaker rule set
isn't evidence.

**On screen:** the re-validation summary — `agent 1/2`, `baseline 10/12`.

---

## 6. The tool found a flaky test in itself — 3:50–4:30

*Likely the most memorable 40 seconds. It is the answer to "does this
generalise beyond your seeded corpus?"*

**On screen:** README section **"The tool found a flaky test in itself"**.

**Say:** Late in the build, one of our own unit tests passed alone and failed
once in a full run. The reflex is to rerun it — which is the whole problem we'd
been attacking. So we did what the tool does.

**Show the arithmetic on screen:**

- base failure rate ≈ **20%**
- experiment ran **40 times**
- threshold for "reduced" ≈ **8%**
- P(landing there by chance) ≈ **13%**

**Say:** The test wasn't wrong about the behaviour. It was drawing a conclusion
from too few runs — the exact failure mode our twelve corpus cases were built
to demonstrate, happening in the code that demonstrates them.

**Say:** We fixed it the way the tool fixes things. Not a rerun, not a retry:
40 runs → 120. Passed twice consecutively.

**Land it:** The corpus is seeded — we wrote it, we knew the answers. This one
we didn't plant.

---

## 7. Human approval + close — 4:30–5:00

**On screen:** `results/pending_approval/case_12_masking_trap/ROOT_CAUSE.md` —
diagnosis, the discriminating experiment (1.0% → 44.0% under contention), the
seven validator checks, the 0/500 verification.

**Then:** `git status --porcelain -- corpus/` returning empty.

**Say:** Every patch that survives lands here with its evidence, and is not
applied. A person decides. The corpus is clean against HEAD — verified by the
self-audit, not asserted.

**Closing line (the hot take):** Confidence isn't evidence, and the industry is
shipping confidence. The model was already good enough to solve 10 of 12,
including the trap case built to fool it. What it lacked was any way to check
its own work. An agent's most valuable component may be the part that tells it
it's wrong.

---

## Shots to capture in advance

| # | Shot | Source |
|---|---|---|
| 1 | Claimed/verified/legitimate table | `results/RESULTS.md` |
| 2 | `def __init__( -> None:` + SyntaxError stderr | baseline patch, case 01 |
| 3 | `49/200 failures at 32 workers` | `results/revalidation.json` |
| 4 | Masking demo 3×3 table | `scripts/demo_masking_fix.py` |
| 5 | The two-hunk cheating diff | `results/archive/rejected_patches/case_07_attempt1_rejected/REVALIDATION.md` |
| 6 | Re-validation summary, both arms | `scripts/revalidate_pending.py` output |
| 7 | The 13% arithmetic | README self-flake section |
| 8 | Approval package + clean `git status` | `results/pending_approval/` |
| 9 | Corpus measurement running | `measure_corpus.py --runs 500` |
| 10 | A trajectory JSONL scrolling | `traces/agent-*.jsonl` |

---

## What to leave out

- The architecture diagram. Nobody remembers boxes; they remember the diff that
  set a delay to zero.
- The full 12-case corpus tour. Two cases carry the argument — 07 and 12.
- Drift diagnosis and the locked protocol. Genuinely good work, but it is a
  methodology story and there is no room. One sentence at most, or leave to the
  README.
- **Agent-arm coverage numbers.** Free-tier quota capped the agent at one
  completed case. Every claim in this outline comes from the baseline arm, the
  500-run verifications and the validator — none of which depend on agent
  coverage. Do not build a beat that needs a number we do not have.

---

## Tone

Understated. The findings are strong enough that overselling them would cost
credibility. Say "we misdiagnosed this at first" out loud — it is a
30-word admission that makes every other claim in the video more believable.


---

## Shot-list verification

Every artifact named above was checked to exist and to contain what this
outline claims, by script rather than by memory:

| Shot | Verified |
|---|---|
| 1 Claimed/verified/legitimate table | `Claimed 12/12` present in `results/RESULTS.md` |
| 2 `def __init__( -> None:` | found verbatim in the stored case 01 patch |
| 3 49/200 at 32 workers | `results/revalidation.json`, workers=32, failures=49 |
| 4 Masking demo table | 4 variants x 3 workloads in `results/case12_masking_demo.json` |
| 5 The cheating diff | ` ```diff ` block with `SERVICE_WORK_S` in the rejection record |
| 7 The 13% arithmetic | README section present with the figure |
| 8 Approval package + clean corpus | package exists; 0 corpus **code** changes |
| 10 Trajectory index | `traces/README.md` present |

Re-run the check before filming — numbers move if anything is re-measured:

```bash
python scripts/verify_video_shots.py
```
