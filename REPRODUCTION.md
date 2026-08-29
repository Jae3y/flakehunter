# Reproduction

Every command below was run on the reference machine and its runtime measured,
not estimated. Docker is the only host requirement — no Python, no virtualenv.

## Environment

| | |
|---|---|
| Host OS | Windows 11 Pro 26200 + WSL2 |
| Docker | 29.5.3, Compose v5.1.4 |
| Docker resources | 16 logical cores, 7.96 GB |
| Container base | `python:3.11.9-slim-bookworm` |
| Python in container | 3.11.9 |
| Pinned deps | `pytest==8.3.4`, `pluggy==1.5.0`, `packaging==24.2`, `iniconfig==2.0.0` |
| LLM provider | Google AI Studio (Gemini), free tier |
| Model | `gemini-3.6-flash` — **both arms** |

No LLM SDK is installed. The provider is called over REST with `urllib` from
the standard library, so `requirements.txt` is four packages and a clean clone
resolves identically (`DECISIONS.md` D-001).

---

## The constraint that will actually stop you

**Google AI Studio's free tier allows 20 requests per day, per model, per
project.** Not CPU, not wall clock — requests.

```
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaValue: 20
```

| Phase | Requests |
|---|---|
| `check_llm.py` (calls candidates until one generates) | 2–8 |
| Baseline arm, 12 cases | **12** |
| Agent arm, per case | **2–6** (1 per round + 1 per patch attempt) |
| Agent arm, 11 attemptable cases | **~30–50** |
| **Full evaluation** | **~45–70** |

Three to four days of free-tier allowance. Options, in order of preference:

1. **Enable billing** on the API project — and *verify it took effect*. In this
   project's own run, billing was enabled, three calls succeeded, and the daily
   cap continued to be enforced.
2. **Split across days.** Both arms resume cleanly: `run_baseline.py` merges
   results per case, and the agent checkpoints after every round to
   `results/checkpoints/`, so a resumed case skips CONFIRM entirely and starts
   from the hypotheses it has already ruled out.
3. **Run a subset**, both arms on the same model.

**Do not** run the baseline on one model and the agent on another to dodge the
cap. That compares the models rather than the methods (`DECISIONS.md` D-013,
D-015).

A model can appear in `ListModels` and still return 404 on `generateContent` —
`gemini-2.5-flash` does, for new keys. `check_llm.py` calls candidates rather
than trusting the listing, which is why it costs requests.

---

## 1. Setup — ~30 s, 0 requests

```bash
git clone <repo> && cd flakehunter
```

```bash
cp .env.example .env
```

Add `GEMINI_API_KEY` (from https://aistudio.google.com/apikey). `.env` is
gitignored and never committed.

```bash
docker compose build
```

**~25 s** cold, most of it `pip install` of four pinned packages.

---

## 2. Verify before spending anything — ~4 min, 2–8 requests

```bash
docker compose run --rm flakehunter python -m pytest tests -q
```

**82 tests, ~90 s.** Slower than a pure unit suite because several drive the
sandbox and the full agent loop end to end against a scripted model.

```bash
docker compose run --rm flakehunter python scripts/phase0_gate.py
```

**~2 min, 0 requests.** Proves the tracer writes a valid trajectory, the
executor classifies pass/fail/timeout correctly, and — the one that matters —
that the executor *preserves* inter-process nondeterminism rather than
flattening it.

```bash
docker compose run --rm flakehunter python scripts/verify_phase0.py isolation
```

**~10 s, 0 requests.** Probes the sandbox from inside a test run: no host path
reachable, no Docker socket, non-root, resource caps applied, credentials
stripped, read-only mounts genuinely read-only.

```bash
docker compose run --rm flakehunter python scripts/check_llm.py
```

**~30 s, 2–8 requests.** Confirms the credential authenticates and finds a
model that actually generates.

---

## 3. Measure the corpus baseline — 383 s, 0 requests

```bash
docker compose run --rm recorder python scripts/measure_corpus.py --runs 500
```

12 cases × 500 runs under the locked protocol. Writes each case's measured
flake rate into its `metadata.json` and to `results/corpus_baseline.json`.

Note the service: **`recorder`, not `flakehunter`**. The default service mounts
`corpus/` read-only so agent-authored code cannot alter the cases it is
measured against — including the baseline it will be compared to. `recorder` is
the tooling-only service that can write metadata; neither the agent nor the
evaluation uses it.

---

## 4. Baseline arm — 21.4 min, 12 requests

```bash
docker compose run --rm flakehunter python scripts/run_baseline.py
```

One LLM call per case, then a 500-run verification of whatever it produced.
**79,455 tokens** (13,616 prompt / 65,839 output).

Results are written after every case, so an interrupted run leaves usable
output. Re-running specific cases merges rather than overwrites:

```bash
docker compose run --rm flakehunter python scripts/run_baseline.py --cases 02 04
```

---

## 5. Agent arm — 5–17 min and 2–6 requests per case

```bash
docker compose run --rm flakehunter python scripts/run_agent.py
```

The runner **honours the case order you pass**, which matters when quota is
tight — put the cases you most want covered first:

```bash
docker compose run --rm flakehunter python scripts/run_agent.py --cases 12 07 05 02
```

Wall clock is dominated by the 500-run verification. Cases pinned to a single
worker (01, 10) cost roughly eight times as much per run.

Interrupted cases checkpoint and resume from their established evidence:

```bash
cat results/checkpoints/case_05_hash_iteration_order.json
```

A resumed case skips CONFIRM (free of requests, minutes of CPU) and starts from
the hypotheses already eliminated. LLM-derived state is only restored when the
checkpoint came from the same model; a CONFIRM measurement is restored
regardless, because a flake rate is a property of the code (`DECISIONS.md`
D-018).

---

## 6. Validate and compare — ~12 min, 0 requests

```bash
docker compose run --rm flakehunter python scripts/revalidate_pending.py --stress
```

Re-runs the **current** validator over every previously accepted patch, both
arms. Necessary because the validator gained checks mid-project, and acceptance
under a weaker rule set is not evidence. Writes a `REVALIDATION.md` — carrying
the full diff — into any package that no longer passes.

```bash
docker compose run --rm flakehunter python scripts/run_compare.py
```

**~5 s.** Emits `results/RESULTS.md`.

```bash
python scripts/self_audit.py
```

Runs on the host. Checks the compliance checklist by inspecting files and git
rather than by assertion.

---

## Optional: the masking demonstration — ~5 min, 0 requests

```bash
docker compose run --rm flakehunter python scripts/demo_masking_fix.py --runs 300
```

Builds a sleep-based mask, a retry-based mask and the true fix for case 12, and
measures all three at three workloads. This is the evidence behind the
validator's behavioural check, and the clearest single demonstration in the
repo of why 500 clean runs is not proof.

---

## Where the outputs land

| Path | What |
|---|---|
| `traces/<run_id>.jsonl` | Turn-by-turn trajectory, one JSON object per turn |
| `results/corpus_baseline.json` | Measured flake rate per case |
| `results/baseline_results.json` | Baseline arm |
| `results/agent_results.json` | Agent arm |
| `results/checkpoints/<case>.json` | Resumable per-case state |
| `results/pending_approval/<case>/` | Patch + writeup + evidence, awaiting human approval |
| `results/revalidation.json` | Validator verdicts over previously accepted patches |
| `results/RESULTS.md` | The comparison table |
| `docs/CHANGELOG.md` | Improvement changelog, measurements recorded as taken |
| `DECISIONS.md` | 21 judgment calls: tension, choice, reasoning, revisit trigger |

---

## Measured runtimes, all phases

| Step | Wall clock | Requests |
|---|---|---|
| `docker compose build` (cold) | 25 s | 0 |
| Test suite (82) | 90 s | 0 |
| Phase 0 gate | 2 min | 0 |
| Isolation probe | 10 s | 0 |
| LLM provider check | 30 s | 2–8 |
| Corpus baseline, 12 × 500 runs | 383 s | 0 |
| Baseline arm, 12 cases | 21.4 min | 12 |
| Baseline re-run, 2 cases | 3.5 min | 2 |
| Agent arm, per case | 5–17 min | 2–6 |
| Re-validation, 14 patches with stress | ~12 min | 0 |
| Comparison table | 5 s | 0 |
| Masking demo, 12 cells × 300 runs | 5 min | 0 |
| Drift repeatability study | ~35 min | 0 |
| Interleaved machine probe | ~25 min | 0 |

---

## Cost

**Reported in tokens, not dollars.** No published per-token rates for
`gemini-3.6-flash` were available to this project, and a fabricated dollar
figure in a results table is worse than an honest token count (`DECISIONS.md`
D-007). Every trajectory records prompt and output tokens per turn, so a rate
can be applied retrospectively without re-running anything.

Gemini bills **reasoning tokens as output**. The recorded output count sums
`candidatesTokenCount` and `thoughtsTokenCount`; reporting only the visible
completion would understate spend substantially — on short structured replies
the reasoning is often the larger part.

| Phase | Prompt | Output | Total |
|---|---|---|---|
| Baseline arm, 12 cases | 13,616 | 65,839 | **79,455** |
| Agent arm, live cases | ~29,700 | ~85,600 | **~115,300** |
| **Session total** | ~43,200 | ~150,600 | **~193,800** |

Typical single calls: a baseline diagnosis is ~1,000 prompt / ~4,000 output; an
agent round is ~2,500 / ~5,000.

---

## Determinism, and what is honestly not deterministic

Seeds are used where seeds are appropriate. But this project *measures*
nondeterminism, so some numbers move between runs and pretending otherwise
would be dishonest:

- **Flake rates carry sampling error.** At 500 runs and a rate near 30%, the
  binomial standard deviation is about 2 percentage points.
- **Timing-sensitive cases also carry machine-state variation.** Measured and
  diagnosed: two corpus cases were flaking *only* because the harness's own
  concurrency created the race, and were rebuilt so their nondeterminism is
  intrinsic (`docs/CHANGELOG.md` 003). The residual is why worker counts are
  pinned per case and identical across both arms.
- **The host itself drifts.** Case 07 moved 2.5% → 34.0% serial across a few
  hours with no code change. Absolute "before" numbers are session-local, and
  comparisons must be paired within a session.
- **The primary metric is unaffected.** Residual flake rate after a real fix is
  zero, and zero is zero under any machine state. Machine speed changes how
  *often* a race is observed, not whether it exists.
