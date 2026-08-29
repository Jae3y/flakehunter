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
case 01 -- adding a lock to the shared counter, which is the *correct* fix --
produced **500 `error` runs and zero failures**. An eight-thread case burns CPU
seconds roughly eight times faster than wall-clock seconds, so the newly
serialised counter hit the 10-second CPU ceiling in about two seconds of wall
time, took SIGXCPU, and exited pytest with code 2 before any assertion ran.

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
