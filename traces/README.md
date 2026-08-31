# Agent trajectories — index

One JSON object per line, one line per turn. Every LLM call and every tool
execution in this project routes through `src/telemetry/tracer.py`, so these
files are the complete record — nothing was reconstructed afterwards.

There are 61 files here because the project ran for three days. **This index
names the seven worth opening**, and what each one demonstrates. The rest are
listed at the bottom by category.

## Record schema

Eleven fields, enforced by `validate_record()` and checked by the Phase 0 gate:

```
turn_id  run_id  timestamp  agent_name  model  instruction
tool_call  tool_response  reflection  human_checkpoint  tokens
```

`turn_id` is monotonic and contiguous within a run. `reflection` is where a
tool result becomes the reason for the next step — it is the field to read if
you want to follow the agent's reasoning rather than its actions.

---

## The seven to read

### 1. A loop that worked — `agent-20260829T062228Z.jsonl` (13 turns)

Case 12, the masking trap, start to finish. The cleanest demonstration of the
architecture.

| Turn | What to look for |
|---|---|
| `agent.confirm` | 2/200 failures — the flake rate established by execution, not guessed |
| `agent.hypothesize` | Two competing candidates, each with a *discriminating* prediction |
| `agent.experiment.design` | `amplify_contention(2)` chosen because the two candidates predict different outcomes for it |
| `agent.experiment` | Observed **1.0% → 44.0%**, matching the prediction — this is the turn that converts a guess into a diagnosis |
| `agent.patch` | Minimal fix for the confirmed cause |
| `harness.validator` / `.stress` | 7/7 checks, including 0/200 at 32 workers |
| `agent.verify` | **0/500** |

### 2. Patches rejected and re-authored — `agent-20260829T063840Z.jsonl` (29 turns)

Case 07 across five hypothesis rounds. **Three `harness.validator` turns end in
rejection**, and each rejection is fed back into the next `agent.patch` call —
the loop arguing with itself, which is the point.

Read the `reflection` field on the validator turns for the reason given, then
the next `agent.patch` instruction to see it used. This run ends UNRESOLVED:
the agent declined to claim a fix it could not verify, on the same case the
baseline "fixed" with `high` confidence.

### 3. The human checkpoint — `verify-tracer.jsonl` (3 turns)

A `human_checkpoint` record in isolation, easiest to read:

```json
"human_checkpoint": {"prompted": true, "decision": "approved", "note": "..."}
```

In a live run this is emitted by `agent.approve` when a patch survives
validation and verification. The run **stops there** — `decision` is
`"pending"`, never `"approved"`, because a system approving its own work is not
a checkpoint. The patch is written to `results/pending_approval/` and
`corpus/` is left untouched.

### 4. Retroactive re-validation — `revalidate-20260829T080546Z.jsonl` (26 turns)

**The one to read if you only read one.** The current validator re-run over all
fourteen previously accepted patches, after two checks were added mid-project.

Three rejections, each for a different reason:

- `agent:case_07` — `test_conditions_unchanged`. A patch the validator had
  **already accepted** under its earlier rules, caught here: it set the test
  fixture's service delay to `0.0`, deleting the condition that produces the
  flakiness.
- `baseline:case_01` — `patch_parses`. The patch never compiled.
- `baseline:case_07` — `survives_stress`. 49/200 failures at 32 workers against
  0.80% at normal load.

### 5. The merged round, post-optimisation — `agent-20260829T184647Z.jsonl` (8 turns)

Shows `agent.round` replacing the separate `agent.hypothesize` and
`agent.experiment.design` turns — halving per-round request cost under a
20-request daily quota (`DECISIONS.md` D-019). Also shows a **resumed** case:
no `agent.confirm` turn for case 05, because its measurement was restored from
a checkpoint.

### 6. Gap markers under a failing tracer — `phase0-write-failure-20260828T232515Z.jsonl` (5 turns)

The trajectory's own failure mode. When a record cannot be persisted, a
schema-valid marker is written in its place at the same `turn_id` rather than
the record silently vanishing. `agent_name` is `telemetry.gap`; the sequence
stays contiguous. Escalates to a hard failure after three consecutive gaps.

### 7. The human decision recorded — `human-decision-20260830T150641Z.jsonl` (1 turn)

The trace that actually closed Case 12's checkpoint. Recorded by
`scripts/record_decision.py` after a human reviewed the patch held in
`results/pending_approval/case_12_masking_trap/`.

Here `human_checkpoint.decision` reads `"approved"`, not `"pending"` —
recording the human reviewer's confirmation of the root cause, the contention
experiment results, and the 0/500 verification. The patch remains unapplied to
`corpus/` (`applied_to_corpus: false`), documenting the decision without
modifying the evaluation benchmark.

---

## Everything else, by category

| Prefix | Count | What |
|---|---|---|
| `agent-*` | 9 | Agent loop runs. The three above are the informative ones; the others are quota-truncated. |
| `baseline-*` | 5 | One-shot baseline calls plus their 500-run verifications |
| `corpus-baseline-*` | 22 | Repeat-execution measurements. One turn per batch, no LLM calls. |
| `revalidate-*` | 5 | Validator re-runs over accepted patches |
| `phase0-gate-*` | 3 | 60 turns each of raw `sandbox.executor` runs — the per-run tracing mode |
| `drift-*` | 2 | Serial vs parallel measurements from the drift investigation |
| `case12-masking-*` | 4 | The masking demonstration: sleep, retry and true fix at three workloads |
| `verify-*`, `phase0-tracer-check-*` | 5 | Tracer self-checks |
| `human-decision-*` | 1 | Human reviewer decision traces recorded via `scripts/record_decision.py` |

---

## Reading them

```bash
python -c "import json,sys; [print(json.loads(l)['agent_name'], '|', json.loads(l)['reflection'][:100]) for l in open(sys.argv[1],encoding='utf-8')]" traces/agent-20260829T062228Z.jsonl
```

Or the full first turn:

```bash
head -1 traces/agent-20260829T062228Z.jsonl | python -m json.tool
```

## A note on granularity

The agent never calls `run_once`; it asks for "run this 500 times and report
the failure rate". That batch is the tool, so the batch gets the turn — one
`agent.verify` record standing for 500 executions. Emitting 500 turns would be
affordable on disk (722 bytes each, measured) but would bury the instructions,
reflections and checkpoints these files exist to show. Per-run tracing is
available behind `SandboxExecutor(trace_each_run=True)`; the
`phase0-gate-*` files are what it looks like.
