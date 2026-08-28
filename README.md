# FlakeHunter

> **Status: Phase 0 complete.** Sandbox and trajectory capture are built and
> measured. The corpus, harness, baseline and agent do not exist yet. This
> README is a stub; the full write-up (user, bottleneck, value, Improvement
> Changelog, failure mode, hot take) lands at Phase 4.

A flaky test passes most of the time and fails intermittently with no code
change. Developers click "rerun" instead of investigating, because normal
debugging assumes the bug reproduces on demand — and a flaky test breaks that
assumption at step one. Suites accumulate flaky tests everyone has agreed to
ignore, which is also where real regressions hide.

FlakeHunter takes a flaky test, diagnoses the root cause through
hypothesis-driven experimentation, writes a real code fix, and then proves the
fix by re-running the test 500 times. The deliverable is a merge-ready patch
plus a root-cause writeup — not a diagnosis.

## Running Phase 0

Requires Docker only. Nothing is installed on the host, and no Python is
needed there.

```bash
docker compose build
```

```bash
docker compose run --rm flakehunter python -m pytest tests -q
```

```bash
docker compose run --rm flakehunter python scripts/phase0_gate.py
```

The gate writes `results/phase0_gate.json` and a trajectory to
`traces/phase0-gate-<timestamp>.jsonl`. It exits non-zero if any check fails.

## What exists so far

| Path | Purpose |
|---|---|
| `src/telemetry/tracer.py` | JSONL trajectory capture. Every LLM call and tool execution routes through it. |
| `src/sandbox/executor.py` | One test run: fresh scratch workdir, wall-clock timeout, `RLIMIT_CPU`/`AS`/`NOFILE`, process-group kill. |
| `corpus/case_00_smoke/` | Plumbing case for the gate. **Not** one of the twelve; excluded from all results. |
| `scripts/phase0_gate.py` | The Phase 0 gate: schema, outcomes, cost, fidelity, concurrency. |
| `docs/CHANGELOG.md` | Improvement Changelog, with measurements recorded as they were taken. |

## Safety properties, and how they are enforced

**Consequential actions run in a sandbox.** The container is the isolation
boundary: the orchestrator, the agent, and every execution of agent-authored
code run inside it. This is enforced, not asserted in prose — the Dockerfile
writes `/.flakehunter-sandbox` and `assert_sandboxed()` refuses to execute
without it, so a stray invocation from the host fails loudly instead of
quietly running agent-authored code on your machine.

Within the container: `src/` and `corpus/` are mounted read-only so the agent
cannot rewrite its own source or dirty the pristine corpus; scratch workdirs
are RAM-backed and destroyed after every run; the orchestrator's
`ANTHROPIC_API_KEY` is stripped from every test-run environment; and `init:
true` reaps the orphaned threads and servers that half the corpus creates by
construction.

**A qualified human reviews anything consequential.** Patches are written
outside the sandbox only through the mounted `results/` directory, and only
after an explicit approval gate (Phase 3). Approvals and rejections are
recorded in the trajectory's `human_checkpoint` field.

## Measured so far

At 8 workers, one full 26,000-run evaluation projects to **~42 minutes** —
down from 220 minutes serial. See `docs/CHANGELOG.md` for the breakdown, the
fidelity checks that justify parallelism, and the measurements that do *not*
yet mean anything.
