# Decisions

Judgment calls made without the maintainer present, during the unattended run
started 2026-08-29. Each records the tension, the choice, the reasoning, and
what would make it worth revisiting.

Decisions made *with* the maintainer (the trajectory write-failure policy, the
concurrency threshold rule) live in `docs/CHANGELOG.md` where they were agreed.

---

## D-001 — Call Gemini through the standard library, not a new dependency

**Tension.** The obvious move is `pip install google-genai`. But the brief
requires fully pinned dependencies and reproducibility from a clean clone, and
says to ask before adding any dependency. There is nobody to ask.

**Chosen.** Call the REST API with `urllib.request` from the standard library.
No new dependency; `requirements.txt` stays at four pinned packages.

**Why.** The surface actually needed is one POST to `generateContent` and one
GET to `models`. An SDK would add a dependency tree to the reproducibility
story in exchange for convenience this project does not need. Declining to
add a dependency is also the reversible choice — adding one later is easy,
removing one that has spread through the code is not.

**Revisit if.** Streaming, function calling, or automatic retry/backoff
handling is needed, at which point hand-rolling starts costing more than the
dependency would.

---

## D-002 — Model selected by probing, not by assumption

**Tension.** Hard-coding a model name is simplest, but `gemini-2.5-flash`
appears in `ListModels` and then returns HTTP 404 on `generateContent` with
"no longer available to new users". A listed model is not a callable model.

**Chosen.** `scripts/check_llm.py` walks a preference list, *calling* each
candidate until one actually generates, and prints the winner. Selected:
**`gemini-3.6-flash`**, pinned in `.env` as `FLAKEHUNTER_MODEL`.

**Why.** A hard-coded name that silently 404s would have surfaced as a
phase-long failure inside the agent loop rather than as a one-line config
error. Flash tier because the agent loop runs 12 cases x up to 5 hypothesis
rounds, so per-call cost compounds; the maintainer's fairness requirement is
that *both arms use the same model*, which this satisfies.

**Revisit if.** Flash proves too weak for hypothesis generation or patch
authoring — the brief explicitly allows reserving a stronger model for those
steps, provided baseline and agent still share one model for the comparison.

---

## D-003 — The trajectory field stays named `model`, not `model_identifier`

**Tension.** The unattended instructions refer to `telemetry/tracer.py`'s
`model_identifier` field. The field is actually named `model` — that is the
name the original brief specified in the required record schema, and
`RECORD_FIELDS` plus `validate_record` enforce exactly eleven names.

**Chosen.** Keep the field named `model`. Ensure it carries the real Gemini
model string (`gemini-3.6-flash`) rather than a placeholder, which is the
substance of what was asked.

**Why.** Renaming would break the schema the brief mandates and the validation
that guards it, for a cosmetic gain. The intent behind the instruction — no
leftover placeholder in the model column — is satisfied either way.

**Revisit if.** The maintainer confirms the schema itself should change, in
which case `RECORD_FIELDS`, `validate_record`, the Phase 0 gate and the tests
all move together.

---

## D-004 — `ANTHROPIC_API_KEY` removed rather than kept alongside `GEMINI_API_KEY`

**Tension.** Leaving the old variable in `.env.example` costs nothing and
would help if the project ever moves back to Anthropic.

**Chosen.** Removed entirely. `.env.example` now documents only
`GEMINI_API_KEY` and `FLAKEHUNTER_MODEL`.

**Why.** A key name in an example file is an instruction to whoever sets the
project up. Two credential names, one of which nothing reads, is a setup trap
in a project whose reproducibility is being scored. The executor still strips
`ANTHROPIC_*` from test-run environments — that is defence in depth against a
stale variable in someone's shell, and costs nothing to keep.

**Revisit if.** Multi-provider support becomes a goal, which would want a
`FLAKEHUNTER_PROVIDER` switch rather than two bare key names.

---

## D-005 — A shared `src/llm/` module rather than per-arm clients

**Tension.** The repository layout in the brief has no `src/llm/`. Both
`baseline/one_shot.py` and the agent need traced LLM calls, so the code has to
live somewhere.

**Chosen.** One `src/llm/client.py`, used by both arms.

**Why.** The fairness requirement is that the baseline and the agent get the
same model and the same file access. Sharing one client makes that structural
rather than a thing to remember: they cannot drift apart, because there is one
implementation. Duplicating it per arm would make an unfair comparison a
one-line edit away. It also guarantees every LLM call routes through the
tracer, which the brief requires without exception.

**Revisit if.** The arms genuinely need different call semantics, at which
point the difference should be an explicit parameter rather than a fork.

---

## D-006 — The Phase 3 validator must test behaviour, not syntax

Carried from `docs/PHASE3_REQUIREMENTS.md` and promoted here because it is now
a build instruction rather than a note.

**Tension.** The obvious anti-cheat rule is "reject a fix that adds `sleep()`
or a retry". Case 07's *correct* fix is a retry with backoff — its root cause
class is literally `network_timeout_no_retry`. Case 12's *masking* fix is also
a retry. The same construct is right in one case and cheating in the other, so
no pattern match can separate them.

**Chosen.** The validator must re-run the cause-isolating experiment against
the patched code and confirm the discriminating signal is gone, in addition to
the structural checks (assertions intact, not skipped, source modified, no
change to `protected_paths`).

**Why.** Measured evidence, not taste: both masking fixes reached 0/300
failures at the corpus workload and would pass a 500-run verification. What
separates them from the true fix is that they stop working when the workload
grows — 8% for the sleep at 80k documents, 99% for the retry at 600k, versus
0% for the true fix at both. Behaviour under a changed workload discriminates
where syntax cannot.

**Revisit if.** A case appears whose true fix legitimately depends on
workload-sensitive timing, which would make the stress dimension produce false
rejections.

---

## D-007 — Cost is reported in tokens, not dollars

**Tension.** The brief asks for API cost per case, and the results table has a
cost column. Published per-token rates for `gemini-3.6-flash` are not
available to this project.

**Chosen.** Report prompt and output tokens, measured. Leave `cost_usd` as
`null` in the trajectory and omit a dollar column from the results table.

**Why.** This is the same call made at Phase 0 for the pricing table, and for
the same reason: a fabricated number in a cost column is worse than an honest
token count. Tokens are what was actually measured; a rate can be multiplied
through later, and cannot be recovered from a made-up dollar figure. Gemini
bills reasoning tokens as output, so `billed_output_tokens` deliberately sums
`candidatesTokenCount` and `thoughtsTokenCount` -- reporting only the visible
completion would understate spend, sometimes by an order of magnitude.

**Revisit if.** Official rates for the model become available, at which point
`PRICING_USD_PER_MTOK` is populated and every recorded trajectory can be
re-costed without re-running anything.

---

## D-008 — One locked protocol module, per-case worker counts

**Tension.** The maintainer's rule was to pin drifting case classes to a lower
worker count. Implementing that as a default argument in each script would
work, and would also let the two arms drift apart the first time someone
passed `--workers` to one of them.

**Chosen.** `src/harness/protocol.py` owns the run counts and the per-case
worker counts. Both arms read from it.

**Why.** The comparison between arms is the product. A comparison whose arms
were measured under different conditions is not evidence, so "both arms use
the same settings" should be a property of the code rather than a thing to
remember. Verification stays at 500 runs for every case including the pinned
serial ones -- the headline number is not allowed to get cheaper for some
cases than others. Confirm/experiment/stress counts halve for serial cases,
since those only have to separate hypotheses, not prove a fix.

**Revisit if.** A case's drift changes after it is patched, which would mean
the fix altered its contention profile and the pinning needs re-deriving.

---

## D-009 — Two corpus cases rebuilt rather than measured around

**Tension.** Case 01 and case 10 read 0.0% serially and 25%/11.5% at eight
workers. The threshold rule says pin them to a lower worker count -- but
pinning case 01 to serial makes it 0.0% flaky, which fails the corpus rule
that a case flaking 0/500 is broken and useless for evaluation. Following one
rule literally would break the other.

**Chosen.** Rebuild both so their nondeterminism is intrinsic: case 01 from
25,000 to 50,000 iterations per worker, case 10 from a work gradient to equal
panel cost. Measured after: 27.3% and 22.7% serial.

**Why.** A case whose flakiness only exists under the harness's own
concurrency means the agent would be diagnosing our measurement setup, not the
bug. Worse, the agent's experiment vocabulary includes `serialize_execution` --
it would have run that, seen the failure vanish, and correctly concluded "CPU
contention", which is the wrong root cause for a case whose intended answer is
a data race. The corpus has to contain the bug it claims to contain.

This is also the root cause of the drift that started the investigation. Rates
produced by contention track machine load; rates intrinsic to the code do not.
Case 06 sat at 23-27% in every condition tested while case 01 wandered between
18.9% and 56.8%.

**Revisit if.** A rebuilt case turns out to be slow enough to threaten the
runtime budget -- case 01 at 50,000 iterations is the most expensive case in
the corpus and is pinned serial, which compounds it.

---

## D-010 — A wedged measurement job was killed rather than waited out

**Tension.** The 12-case serial drift sweep ran for four hours with no output.
Killing it discards whatever it had computed; waiting risks losing the session
to a job that may never finish.

**Chosen.** Killed it, and re-ran a narrower measurement that answered the
same question.

**Why.** The decisive comparisons -- serial versus parallel for the two
suspect cases, and the intrinsic-versus-manufactured distinction -- had already
come back from the earlier eight-case run. The full sweep was confirmation,
not discovery, so its loss cost nothing that mattered. The apparent silence was
partly an artefact: `grep` in the output pipeline buffers, so progress was
invisible even when jobs were healthy. Later runs use `python -u` and avoid
piping through `grep`.

**Revisit if.** Nothing to revisit; recorded so the gap in wall-clock time in
the trajectory has an explanation.

---

## D-011 — The CPU limit scales with the thread budget

**Tension.** `RLIMIT_CPU` was a flat 10 seconds against a 15-second wall
clock. That reads as a sane guard until you notice the two are not measured in
the same units: the wall clock is elapsed time, `RLIMIT_CPU` is CPU seconds
summed across every thread in the process.

**Chosen.** Default `cpu_seconds` to four times the wall-clock limit, and
expose `FLAKEHUNTER_RUN_CPU_S` to override it.

**Why.** Found by a false result, not by inspection. The baseline's patch for
case 01 produced **500 `error` runs and zero failures** -- pytest exit 2 on
every run.

> **Correction (later the same session).** The reasoning below about
> `RLIMIT_CPU` was **the wrong diagnosis**. I inferred it from the exit code
> without reading the captured stderr. The actual cause was that the patch did
> not compile: it contained `def __init__( -> None:`, the model having dropped
> `self`, so the module never imported and pytest errored during collection.
> Raising the CPU budget changed nothing, which is what finally prompted
> reading the stderr. See D-017 and the `patch_parses` check.
>
> The change itself still stands on its own reasoning -- CPU time genuinely is
> summed across threads, and a flat 10s against a 15s wall clock genuinely is
> inconsistent for a multi-threaded case -- but it was made for a reason that
> turned out not to apply, and the honest record says so.

The original reasoning, left as written: an eight-thread case burns CPU seconds
roughly eight times faster than wall-clock seconds, so a newly serialised
counter could hit a 10-second CPU ceiling in about two seconds of wall time,
take SIGXCPU, and exit pytest with code 2 before any assertion ran.

Zero failures. A residual flake rate of 0.00%. A perfect-looking fix in which
nothing executed.

That it was caught rather than published is entirely down to `ERROR` being a
distinct outcome from `FAIL`, and to `BatchReport.is_sound` being consulted
before a fix counts. Had errors been folded into the flake rate, case 01 would
have gone into the results table as a clean win for the baseline.

The multiplier is a guard, not a licence: a genuine runaway loop still dies,
just at 60 CPU-seconds instead of 10.

**Revisit if.** A case legitimately needs more than four times its wall clock
in CPU -- more than four busy threads sustained for the whole run -- at which
point the limit wants setting per case in the protocol module rather than
globally.

---

## D-012 — case_01 is left too expensive, and flagged rather than re-tuned

**Tension.** The brief requires corpus cases to execute in milliseconds. Case
01 at 50,000 iterations per worker takes roughly a second per run unpatched
and around four seconds once the correct fix -- a lock -- serialises 400,000
increments across eight threads. Pinned serial, a 500-run verification of the
*fixed* case takes over half an hour. That is three orders of magnitude over
the stated budget, and the fix makes it worse rather than better.

Re-tuning it downward would invalidate the baseline measurement already taken
against it, and the two are not comparable across a case change.

**Chosen.** Leave case 01 as measured for this session, order it **last** in
the agent run, and record the problem with a concrete remedy rather than
quietly absorbing it.

**Why.** The measurements taken so far are internally consistent: the corpus
baseline, the baseline arm and the agent arm all see the same case. Changing
it mid-session would produce a results table whose rows were measured against
different code, which is worse than a table with one honestly-flagged
expensive row. Ordering it last means a truncated run loses only this case.

The tension is real and was created by an earlier fix: case 01 was raised from
25,000 to 50,000 iterations (D-009) so that its nondeterminism would be
intrinsic rather than manufactured by harness concurrency. That was the right
call for validity and the wrong one for cost, and both cannot be had with a
GIL race -- the race needs each worker's loop to outlast the 5 ms switch
interval, which sets a floor on how cheap the case can be.

**Revisit if.** The corpus is re-measured at all -- this case should be made
cheaper first, since every future run pays its cost twice (once per arm).

**Remedy for the next session.** Either drop the thread count from 8 to 2-3
(fewer threads still interleave, and the lock contention that makes the fixed
version slow scales with thread count), or accept a lower serial flake rate
and pin the case at a modest worker count where the rate is still non-zero.
Re-measure the corpus baseline and both arms afterwards; do not mix.

---

## D-013 — Quota exhaustion: a fair subset on a fresh model, not a mixed comparison

**Tension.** The Gemini free tier allows **20 requests per day per model**
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`). The baseline arm plus
probes and re-runs consumed that allowance on `gemini-3.6-flash`, and the agent
arm needs roughly four calls per case — about 44 for eleven cases. The agent
run died on case 07 with HTTP 429 after completing case 12.

Quota is scoped per model, and four other models had untouched allowances. The
tempting move is to finish the agent arm on a different model. That would have
produced a results table comparing a baseline on one model against an agent on
another, which measures the models as much as the methods — exactly the
confound the maintainer's "same model for both arms" rule exists to prevent.

**Chosen.** Keep the two bodies of evidence separate and honestly labelled:

1. The complete 12-case **baseline** on `gemini-3.6-flash`, plus the one agent
   case that finished on it (case 12), archived as
   `results/agent_results_gemini-3.6-flash.json`.
2. A **fair head-to-head subset** — both arms, same model
   (`gemini-3.7-flash`), same cases — sized to fit one model's daily
   allowance.

**Why.** A small comparison where both arms are matched supports a real
conclusion. A large one where they are not supports none, and would be worse
than reporting less, because the number would look authoritative while meaning
nothing. Cases 07, 11 and 02 were chosen because they are the ones where
execution feedback should matter most: 07 is where the baseline produced a fix
that still failed 0.8% of the time while reporting high confidence.

**Revisit if.** The project moves off the free tier, at which point the full
12-case agent arm should run on the same model as the baseline and this subset
becomes redundant.

**Also fixed.** The client now reads the structured quota detail on a 429. A
per-minute limit's `retryDelay` is honoured up to 90 s; a per-day quota raises
immediately with a message naming the quota and the three ways out, rather
than sleeping through four retries and reporting a generic failure.

---

## D-014 — An unsound experiment produces no evidence, not a zero

**Tension.** `classify_effect` read the flake rate and nothing else. An
experiment whose runs all *errored* has a flake rate of 0.0%, because errors
are not failures — so a manipulation that stopped the test from executing
scored as "eliminated", the strongest possible support for whichever
hypothesis it targeted.

The alternative reading is that a batch is a batch and 0 failures is 0
failures. That reading is what produced four wasted rounds on case_07.

**Chosen.** `run_experiment` checks `report.is_sound` before interpreting
anything. An unsound batch returns `actual_effect="invalid"`,
`matches_prediction=False`, and a note explaining that the test never ran.
Separately, the experiment designer is given the case's real test node ids.

**Why.** Found in a live run, not by review. case_07 round 4 ran
`isolate_test(test_network_timeout)` against a case whose only test is
`test_status_is_fetched_from_a_healthy_service`. pytest collected nothing, 150
runs errored, the rate read 0.0%, and the loop concluded the failure had been
eliminated by isolation — which pointed it at `test_order_dependency`, the
wrong class, for the remaining rounds.

This is the same bug as counting an ERROR run as a pass during verification,
which Phase 0 had already decided against. The rule existed; it had simply not
been applied in the experiment layer. Both places now share it: **errors are a
broken measurement, never a result.**

The node-id fix addresses the cause rather than the symptom — the model was
inventing plausible names because it had never been shown the real ones.

**Revisit if.** A manipulation is added whose *expected* behaviour is to make
collection fail, which would make unsoundness informative rather than a bug.
None currently exist.

---

## D-015 — Billing enabled: the agent arm moves back onto the baseline's model

**Tension.** D-013 split the evidence in two because free-tier quota was
per-model: the baseline had consumed `gemini-3.6-flash`, so the only way to run
more agent cases was a different model, which would have compared models rather
than methods. With billing enabled that constraint is gone, but there is now a
choice about what to do with the results already gathered on
`gemini-3.5-flash`.

**Chosen.** Re-run every remaining case on **`gemini-3.6-flash`**, the
baseline's model, including case 07 from scratch. The 3.5-flash subset is
archived as `results/agent_results_gemini-3.5-flash-subset.json` and kept out of
the headline table.

**Why.** The comparison is the product, and it is only worth anything if both
arms saw the same model. Now that they can, the mixed-model rows should not
survive into the results — keeping them would leave a table whose rows mean
subtly different things, which is worse than a table with fewer rows.

Case 07 is reset rather than resumed for a separate reason: the node-id bug
(D-014) poisoned at least one of its five rounds with manufactured evidence,
and the round budget it spent chasing that false signal is not recoverable by
continuing. A clean re-run answers the question the first attempt could not:
whether case 07 is genuinely hard, or was only hard because the harness was
lying to it. Either answer is worth having, and the second is worth more.

**Revisit if.** Nothing pending. The archived 3.5-flash run stays on disk as a
record of what was attempted under the quota constraint, and because its
case 07 trajectory is the evidence for D-014.


---

## D-016 — Re-validate every previously accepted patch, not just the one that failed

**Tension.** The `test_conditions_unchanged` check was added because one patch
defeated the validator. The minimal response is to re-check that patch. The
maximal one is to re-check everything ever accepted, which costs CPU and might
turn up nothing.

**Chosen.** Re-check every patch accepted this session -- both arms, 14 patches
-- against the current validator, with the stress pass on.

**Why.** The blind spot was not specific to case 07. `test_conditions_unchanged`
did not exist, so *no* patch accepted before it was written had ever been
checked for editing test conditions. "It passed the validator" means "it passed
the validator as it stood that day", and acceptance under a weaker rule set is
not evidence. Leaving the others unchecked would have meant reporting a number
whose basis I already knew had a hole in it.

It also cost nothing to be wrong about: if the re-check had turned up nothing,
the result would still have been worth having, because it would have bounded
the blast radius of the bug rather than leaving it open.

It found two more problems, both in the arm nobody had validated at all:

| Arm | Case | Verdict | Cause |
|---|---|---|---|
| agent | 07 | REJECTED | `test_conditions_unchanged` |
| agent | 12 | valid | — |
| baseline | 01 | REJECTED | `patch_parses` -- the patch never compiled |
| baseline | 07 | REJECTED | `survives_stress` -- 49/200 failures under load |
| baseline | other 10 | valid | — |

**Revisit if.** The validator gains another check. The rule this establishes is
that adding a check means re-running it over everything already accepted, and
`scripts/revalidate_pending.py` exists so that is one command rather than a
project.

---

## D-017 — The validator checks that a patch compiles

**Tension.** Obvious in hindsight, and absent for most of the project. The
argument against adding it is that a patch which does not parse will fail its
verification anyway, so the check is redundant.

**Chosen.** Added `patch_parses`, running `ast.parse` over every changed Python
file before anything is executed.

**Why.** It is not redundant, because *failing verification* and *failing
verification for a legible reason* are different things. The baseline's case 01
patch contained `def __init__( -> None:`. Every one of its 500 verification runs
errored during collection, and the run reported a residual flake rate of
**0.00%** -- the most flattering number in the table, produced by code that
never ran.

Nothing else in the validator would have caught it: no assertion was removed,
no marker added, source was genuinely modified. Only `is_sound` stopped it
being recorded as a fix, and `is_sound` is a downstream guard that says
"something was wrong" without saying what. I then spent a decision entry
(D-011) misattributing it to a CPU limit, because a syntax error and a resource
kill both surface as pytest exit 2.

A one-line check would have said "line 16: invalid syntax" immediately.

**Revisit if.** Nothing pending. Cheap, deterministic, and it names its cause.

---

## D-018 — Checkpoint each case, and refuse to inherit another model's reasoning

**Tension.** A case cut off by quota mid-loop had established real things — an
empirical flake rate, hypotheses ruled out by experiment — and threw all of it
away. The next attempt re-spent requests rediscovering them. On a 20-request
daily budget that is the difference between finishing a case and not.

The obvious implementation restores everything. The question is whether
everything *should* be restored.

**Chosen.** Checkpoint after every round: CONFIRM measurement, hypotheses,
experiments, eliminated classes, history. On resume, skip CONFIRM entirely and
start from what is already ruled out. But restore **LLM-derived state only when
the checkpoint was written by the same model**.

**Why.** The two kinds of state are not alike. A flake rate and an experiment
outcome are properties of the code: 23 failures in 200 runs is 23 failures in
200 runs regardless of which model asked for the measurement. A hypothesis is a
property of the model that proposed it.

Letting a run on one model inherit another's reasoning would make the agent arm
a blend of two models while the baseline is one, which is exactly the confound
D-013 and D-015 exist to prevent — and it would arrive silently, through a
cache, rather than as a visible choice. So `CaseCheckpoint.from_dict` drops
hypotheses and experiment designs on a model mismatch, keeps the CONFIRM
measurement, and records why in the checkpoint's note.

This bit immediately: case 07 and case 11 had rounds recorded against
`gemini-3.5-flash`. Their reasoning was discarded and only their CONFIRM
measurements seeded.

A round also only counts as completed if its **experiment actually ran**. A
round that produced hypotheses and then died to quota has established nothing —
an unrun experiment is not evidence — so cases 02, 03 and 04 seeded with
`rounds_completed=0` despite having hypotheses on record.

**Revisit if.** Checkpoints start outliving corpus changes. A checkpoint is
keyed on case name, so editing a case would leave a stale CONFIRM measurement
describing code that no longer exists. Nothing currently invalidates them; a
content hash of the case would.

---

## D-019 — Merge hypothesis generation and experiment design into one request

**Tension.** They were two calls: propose ranked candidates, then choose the
manipulation that separates them. Splitting them gives the model a dedicated
pass to think about experiment choice. Merging them saves a third of every
round's request cost, which on a 20-request budget is the difference between
three cases a day and four or five.

**Chosen.** One call per round returning both, via `ROUND_SCHEMA`. Per-round
cost drops from 2 requests to 1; a case that resolves in one round now costs 2
requests (round + patch) instead of 3.

**Why.** The split was not buying the deliberation it appeared to. The
hypothesis schema already requires a `discriminating_prediction` for every
candidate — the model was *already* being made to reason about what would
separate them. The second call then asked it to choose a separator from a
summary of reasoning it had just produced and no longer had in front of it.
Merging lets the choice be made with the reasoning still in hand, which is
arguably the better arrangement independent of cost.

The risk is that a single response has less room to deliberate on the
experiment specifically. Mitigated by keeping the experiment's full structure
in the schema — manipulation, parameter, target, rationale, predicted effect —
so it is still a considered object rather than an afterthought, and by keeping
the node-id list and manipulation catalogue in the prompt.

**Revisit if.** Experiment quality drops measurably — the signal would be a
rise in experiments whose prediction matches no matter what, or a return of
invented node ids. Both are visible in the trajectory. `design_experiment` and
`propose_hypotheses` remain in the codebase and the split can be restored.

---

## D-020 — This project's own test suite had a flaky test, fixed the same way the corpus is

**Tension.** `test_no_patch_is_produced_when_stuck` passed in isolation and
failed once in a full-suite run. The cheap response is a rerun; the honest one
is to find out why, in a project whose entire premise is that reruns are how
flaky tests survive.

**Chosen.** Diagnosed it, then raised the sample size: experiment runs 40 → 120,
confirm runs 60 → 80.

**Why.** The scripted model designs an experiment that *cannot* confirm its
hypothesis — pinning the timezone has no bearing on an RNG-driven failure — so
the loop should always eliminate and never reach PATCH. But case 06 fails
around 20% of the time, and at 40 experiment runs there is roughly a **13%
chance** the observed rate lands at or below 8% purely by sampling. That
classifies as `reduced`, which the loop accepts as partial support for a
predicted `eliminated`, so the hypothesis is confirmed, PATCH is called, and
the scripted client raises on an agent name it does not answer.

The test was not wrong about the behaviour. It was drawing a conclusion from
too few runs — the exact failure mode the corpus is built to demonstrate, in
the code that demonstrates it.

At 120 runs the same threshold sits about three standard deviations out, and
the suite passed twice consecutively. The comment in the fixture says why the
number is what it is, so nobody trims it back for speed.

**Revisit if.** The `reduced`-partially-supports-`eliminated` rule changes. That
tolerance exists so a real signal moving the predicted direction is not
discarded on a threshold, and it is also what makes this test statistical
rather than deterministic. Both are consequences of the same deliberate choice.

---

## D-021 — Blast radius of the case 06 checkpoint contamination: none

**Tension.** A contaminated measurement was found and removed. The cheap
response is to say "it was caught early" and move on. But "caught early" is
exactly the kind of claim that is impossible to check later, and a reader six
months from now has no way to tell whether the leaked 60-run CONFIRM reached a
reported number or not.

**Chosen.** Verify it four independent ways, each of which someone else can
re-run, and record the verification rather than the reassurance.

**Why.** The question: did the leaked 60-run CONFIRM for case 06 ever feed a
reported baseline or agent number?

**No. It was caught by the guard before it reached any measurement.** Four
confirmations, each checkable:

1. **case 06 has no agent result in any results file.** Searched
   `results/agent_results*.json` and `results/archive/agent_results*.json`: no
   entry exists. The agent never completed a run on that case, so no agent
   number could have been derived from the checkpoint.

2. **The baseline arm cannot read checkpoints.** `grep -rn checkpoint
   src/baseline/ scripts/run_baseline.py` returns nothing. Checkpointing is
   agent-loop machinery only, so contamination of a baseline number was
   structurally impossible rather than merely unobserved.

3. **case 06's reported corpus baseline is a genuine 500-run measurement:**
   140/500 = 28.0% at 8 workers, recorded in `metadata.json` by
   `scripts/measure_corpus.py`, which also does not touch checkpoints.

4. **The stale file was deleted** and a guard added: a checkpointed CONFIRM is
   reused only when its run count is at least the run's configured budget, and
   discarding one is recorded in `CaseOutcome.resumed_from` rather than
   happening silently.

**How it happened.** The stuck-loop tests exercise the real orchestrator
against a real case with `confirm_runs=60`. Before `AgentConfig.checkpoint_root`
existed, they wrote to the default location, so a test's undersized measurement
landed in the same directory a live run would read from.

**Why it was caught.** Not by review. The checkpoint inventory printed after
the quota cutoff showed `case_06 confirm=12/60` beside seven cases reading
`n/200`, and the odd number out was visible at a glance. That is the same
property the project relies on everywhere else: record the conditions next to
the number, and a wrong condition announces itself.

The near-miss is the useful part. Had it gone unnoticed, a later resumed run
would have reasoned from a 60-run sample while reporting as though it had 200 —
quietly weaker evidence, with nothing in the output saying so.

**Revisit if.** A checkpoint is ever read by something other than the agent
loop. The second confirmation above ("the baseline arm cannot read
checkpoints") is a property of the current code, not a guarantee; if the
baseline or `measure_corpus.py` ever gained checkpoint awareness, the blast
radius of a bad checkpoint would widen and this entry would need re-deriving
rather than citing.

---

## D-022 — Agent results merge by evidence depth; a shallower run can never overwrite

**Tension.** `run_agent.py` wrote `results: [o.to_dict() for o in outcomes]` —
this invocation's cases and nothing else. That is the simplest correct-looking
thing, and it is wrong in two ways at once: cases absent from the run vanish
entirely, and cases present overwrite whatever was there regardless of whether
the new record knows anything.

It cost real evidence. A run in which every case died on API quota replaced a
case_07 record carrying two hypothesis rounds, two experiments and three
validator rejections with a bare `ERROR`. It was restored by hand from an
archive, which is not a fix — the same run tomorrow would do it again.

**Chosen.** `src/agent/results_store.py`. Merge existing and incoming per case,
and **replace only when the incoming record has equal or greater evidence
depth**. Depth is ordered: terminal status, then whether a 500-run verification
exists, then experiments run, hypothesis rounds, validator verdicts.

**Why.** The rule is one-directional on purpose. A quota failure establishes
nothing, ranks bottom, and therefore cannot displace anything — no special case
for quota is needed, because "knows nothing" is already the bottom of the
ordering. A completed run outranks an interrupted one and replaces it, so the
rule does not freeze progress. Ties go to the newer record, so a genuine re-run
that reaches the same depth still wins.

Depth leads with **terminal status rather than raw counts**. A case that
reached PENDING in one round is a better record than one that flailed through
five and died; ranking by experiment count alone would get that backwards.

A refused overwrite is not silent — it appends to `superseded_attempts` on the
surviving record, so a quota-blocked re-run remains visible instead of leaving
the file looking untouched.

Ten tests in `tests/test_results_store.py` pin this, including the exact
regression: a run where every case errors on quota leaves an existing
two-experiment record intact.

**Revisit if.** A record needs *demoting* — as happened when the hardened
validator invalidated case_07's accepted patch. That is a deliberate
invalidation, not a shallower attempt, and it belongs in
`revalidate_pending.py` where the reason is recorded. If demotion ever moves
into the agent loop, this rule would block it and would need an explicit
override rather than a loosened comparison.
