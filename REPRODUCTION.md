# Reproduction

Everything runs inside Docker. The host needs Docker and nothing else — no
Python, no virtualenv. All runtimes below are measured on the reference
machine, not estimated.

**Reference machine:** Windows 11 + WSL2 + Docker Desktop, 16 logical cores,
7.96 GB allocated to Docker, Python 3.11.9 inside the container.

---

## 1. Setup

```bash
git clone <repo> && cd flakehunter
```

```bash
cp .env.example .env
```

Fill in `GEMINI_API_KEY`. Get one from https://aistudio.google.com/apikey.
`.env` is gitignored and never committed.

```bash
docker compose build
```

Build takes **~25 s** cold, most of it `pip install` of four pinned packages.

---

## 2. Verify the sandbox before spending anything

```bash
docker compose run --rm flakehunter python -m pytest tests -q
```

52 tests, **~8 s**.

```bash
docker compose run --rm flakehunter python scripts/phase0_gate.py
```

**~2 min.** Proves the tracer writes a valid trajectory, the executor
classifies pass/fail/timeout correctly, and — importantly — that the executor
preserves inter-process nondeterminism rather than flattening it.

```bash
docker compose run --rm flakehunter python scripts/verify_phase0.py isolation
```

**~10 s.** Probes the sandbox from inside a test run: no host path reachable,
no Docker socket, non-root, resource caps applied, credentials stripped,
read-only mounts genuinely read-only.

```bash
docker compose run --rm flakehunter python scripts/check_llm.py
```

**~30 s.** Confirms the credential authenticates and finds a model that
actually generates. Do this before any phase that spends tokens — a model can
appear in `ListModels` and still return 404 on `generateContent`.

---

## 3. Measure the corpus baseline

```bash
docker compose run --rm recorder python scripts/measure_corpus.py --runs 500
```

**~6.5 min** at 8 workers (383 s measured). Writes the measured flake rate
into each case's `metadata.json` and to `results/corpus_baseline.json`.

Note the service name: `recorder`, not `flakehunter`. The default service
mounts `corpus/` read-only so agent-authored code cannot alter the cases it is
measured against. `recorder` is the tooling-only service that can write
metadata, and neither the agent nor the evaluation uses it.

---

## 3a. Budget your API quota before you start

**This is the constraint that will stop you, not CPU.** The Gemini free tier
allows **20 requests per day, per model, per project**:

```
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaValue: 20
```

Request counts for a full evaluation:

| Step | Requests |
|---|---|
| `check_llm.py` | 2–8 (probes models until one generates) |
| Baseline arm | 1 per case = 12 |
| Agent arm | ~3–5 per case = 36–60 for twelve cases |
| **Total** | **~50–80** |

That is three to four days of free-tier allowance on a single model. Options:

- **Move off the free tier.** Simplest, and what a full run needs.
- **Split across days**, same model both arms. `run_baseline.py` merges results
  per case and `run_agent.py` writes after every case, so both resume cleanly.
- **Run a subset**, both arms, same model. Do *not* run the baseline on one
  model and the agent on another to dodge the cap — that compares the models
  rather than the methods and invalidates the result.

The client distinguishes the two 429s: a per-minute rate limit's `retryDelay`
is honoured up to 90 s, while a per-day quota raises immediately with the quota
id rather than sleeping through retries it cannot outlast.

Note also that a model can appear in `ListModels` and still return 404 on
`generateContent` — `gemini-2.5-flash` does, for new keys. `check_llm.py` calls
candidates rather than trusting the listing, which is why it costs requests.

---

## 4. Run the two arms

```bash
docker compose run --rm flakehunter python scripts/run_baseline.py
```

One LLM call per case, then a 500-run verification of whatever it produced.

```bash
docker compose run --rm flakehunter python scripts/run_agent.py
```

The full loop per case. Results are written after every case, so an
interrupted run still leaves usable output in `results/agent_results.json`.

```bash
docker compose run --rm flakehunter python scripts/run_compare.py
```

Emits `results/RESULTS.md`.

---

## 5. Where the outputs land

| Path | What |
|---|---|
| `traces/<run_id>.jsonl` | Turn-by-turn trajectory, one JSON object per turn |
| `results/corpus_baseline.json` | Measured flake rate per case |
| `results/baseline_results.json` | Baseline arm |
| `results/agent_results.json` | Agent arm |
| `results/pending_approval/<case>/` | Patch + root-cause writeup + evidence, awaiting human approval |
| `results/RESULTS.md` | The comparison table |
| `docs/CHANGELOG.md` | Improvement changelog, measurements recorded as taken |
| `DECISIONS.md` | Judgment calls and their reasoning |

---

## Measured runtimes

Recorded as observed, on the reference machine.

| Step | Wall clock |
|---|---|
| `docker compose build` (cold) | ~25 s |
| Unit tests (52) | 8 s |
| Phase 0 gate | ~2 min |
| Isolation probe | ~10 s |
| LLM provider check | ~30 s |
| Corpus baseline, 12 cases x 500 runs, 8 workers | 383 s |
| Corpus baseline, 12 cases x 100 runs, serial | see note below |
| Drift repeatability, 4 cases x 5 x 500 runs + position test | ~35 min |
| Interleaved probe, 10 cycles x 1000 runs | ~25 min |
| Case 12 masking demo, 4 variants x 3 workloads x 300 runs | ~5 min |

**On serial cases.** Cases 01 and 10 are pinned to one worker (see
`src/harness/protocol.py`), which costs roughly eight times the wall clock per
run. Case 01 is the most expensive case in the corpus: 8 threads x 50,000
iterations per run.

---

## Cost

Reported in tokens, not dollars. Published per-token rates for
`gemini-3.6-flash` were not available to this project, and a fabricated dollar
figure in a results table is worse than an honest token count — see
`DECISIONS.md` D-007. Every trajectory records prompt and output tokens per
turn, so a rate can be applied retrospectively without re-running anything.

Gemini bills reasoning tokens as output. The recorded output count sums
`candidatesTokenCount` and `thoughtsTokenCount`; reporting only the visible
completion would understate spend substantially.

| Step | Tokens |
|---|---|
| One baseline call (case 05, measured) | 1,006 in / 3,932 out |

Totals are filled in from `results/RESULTS.md` after a full run.

---

## Determinism, and what is honestly not deterministic

Seeds are used where seeds are appropriate, but this project measures
nondeterminism, so some numbers move between runs and pretending otherwise
would be dishonest:

- **Flake rates carry sampling error.** At 500 runs and a rate near 30%, the
  binomial standard deviation is about 2 percentage points.
- **Timing-sensitive cases also carry machine-state variation.** This was
  measured, diagnosed, and largely removed by rebuilding the two cases whose
  flakiness was being manufactured by the harness's own concurrency rather
  than by the bug. See `docs/CHANGELOG.md` 003. The residual is why the
  worker count is pinned per case and identical across both arms.
- **The primary metric is unaffected.** Residual flake rate after a real fix
  is zero, and zero is zero under any machine state. Machine-state sensitivity
  affects the precision of the *before* numbers, not the *after* ones.
