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
