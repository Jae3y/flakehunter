"""Phase 0 gate: prove the sandbox and the tracer work, and price the budget.

Run inside the container:

    docker compose run --rm flakehunter python scripts/phase0_gate.py

Five checks, in order of what they de-risk:

1. **Tracer schema** -- records carry the exact twelve fields, and a turn that
   raises is still written.
2. **Executor correctness** -- pass, fail and hang are classified correctly,
   and a hang is killed rather than blocking the run.
3. **Execution cost** -- per-run wall clock under SPAWN and FORK, extrapolated
   to the ~26,000 runs a full evaluation needs. This decides what Phase 3 can
   afford.
4. **Tracing overhead** -- the same benchmark with per-run tracing off, so the
   cost of `trace_each_run` is a measured number rather than a guess.
5. **Nondeterminism fidelity** -- SPAWN must reproduce hash-order flakiness;
   FORK must be shown not to. A fast strategy that silently removes the
   phenomenon under study is worse than a slow one.

Writes ``results/phase0_gate.json`` and prints a summary. Exit code 0 only if
every check passed.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sandbox.executor import (  # noqa: E402
    Outcome,
    SandboxExecutor,
    Strategy,
    assert_sandboxed,
    strategy_preserves_hash_order,
)
from src.telemetry.tracer import (  # noqa: E402
    GAP_AGENT_NAME,
    MAX_CONSECUTIVE_GAPS,
    Tracer,
    TraceWriteError,
    validate_record,
)

SMOKE_PROJECT = REPO_ROOT / "corpus" / "case_00_smoke" / "project"

#: Runs per strategy in the cost benchmark. Large enough for a stable median,
#: small enough that the gate itself stays quick.
BENCHMARK_RUNS = 60

#: Runs used to detect hash-order flakiness. At a fail rate near 4/5, sixty
#: runs make a false "perfectly stable" verdict vanishingly unlikely.
FIDELITY_RUNS = 60

#: The execution budget one full evaluation needs. See the Phase 0 notes:
#: 6k baseline + 6k verify + 2.4k confirm + ~12k experiment.
FULL_EVALUATION_RUNS = 26_000

#: Worker count probed by the concurrency check. Eight of sixteen cores leaves
#: headroom for the orchestrator and for tests that spawn threads of their own.
CONCURRENCY_UNDER_TEST = 8

#: Runs per arm of the concurrency check.
CONCURRENCY_RUNS = 60


@dataclass
class Check:
    """One gate check and its evidence."""

    name: str
    passed: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def check_tracer_schema(trace_dir: Path, stamp: str) -> Check:
    """Write turns of every shape and validate them back off disk.

    The run id carries ``stamp`` because ``traces/`` is a persistent bind
    mount and the tracer appends by design. A fixed id would make the second
    invocation of this gate read six records where it expected three.
    """
    tracer = Tracer(trace_dir=trace_dir, run_id=f"phase0-tracer-check-{stamp}")

    with tracer.turn("gate", "n/a", "a plain traced turn") as turn:
        turn.call("noop", detail="nothing").respond(stdout="ok", exit_code=0)
        turn.reflect("tool returned cleanly")

    with tracer.turn("gate", "n/a", "a turn with a human checkpoint") as turn:
        turn.checkpoint(prompted=True, decision="approved", note="gate rehearsal")
        turn.spend(prompt=100, completion=25)

    # A turn that raises must still be persisted -- this is the property that
    # stops the most interesting turns from being the missing ones.
    raised = False
    try:
        with tracer.turn("gate", "n/a", "a turn that raises") as turn:
            turn.call("explode")
            raise ValueError("deliberate")
    except ValueError:
        raised = True

    lines = tracer.path.read_text(encoding="utf-8").strip().splitlines()
    problems: list[str] = []
    if not raised:
        problems.append("the raising turn did not propagate its exception")
    if len(lines) != 3:
        problems.append(f"expected 3 records, found {len(lines)}")

    turn_ids: list[int] = []
    for index, line in enumerate(lines):
        record = json.loads(line)
        turn_ids.append(record["turn_id"])
        problems.extend(f"record {index}: {p}" for p in validate_record(record))

    if turn_ids != sorted(turn_ids) or len(set(turn_ids)) != len(turn_ids):
        problems.append(f"turn_id not monotonic and unique: {turn_ids}")
    if lines and "deliberate" not in json.loads(lines[-1])["reflection"]:
        problems.append("the raising turn did not record its exception")

    return Check(
        name="tracer schema",
        passed=not problems,
        detail="; ".join(problems) or f"{len(lines)} records, all 12 fields valid",
        data={"records": len(lines), "path": str(tracer.path)},
    )


def check_trace_write_failure(trace_dir: Path, stamp: str) -> Check:
    """Exercise the gap-marker policy end to end.

    The policy: retry, then record an honest gap rather than dropping the
    record or halting; escalate only after three consecutive gaps. This check
    verifies all three behaviours, because the interesting property is not
    "does it fail" but "does the counter reset". A gap counter that never
    resets would turn three scattered blips across a 500-run loop into a
    spurious halt.
    """
    tracer = Tracer(trace_dir=trace_dir, run_id=f"phase0-write-failure-{stamp}")

    class Unserialisable:
        """A payload json.dumps cannot render, even via ``default=str``."""

        def __repr__(self) -> str:
            raise RuntimeError("cannot repr")

    def write_bad() -> None:
        with tracer.turn("gate", "n/a", "unserialisable payload") as turn:
            turn.call("bad", payload=Unserialisable())

    def write_good() -> None:
        with tracer.turn("gate", "n/a", "a healthy turn") as turn:
            turn.reflect("fine")

    problems: list[str] = []

    # One blip must be survivable, not fatal.
    try:
        write_bad()
    except TraceWriteError:
        problems.append("a single gap halted the run; it should be survivable")
    if tracer.consecutive_gaps != 1:
        problems.append(f"expected 1 consecutive gap, got {tracer.consecutive_gaps}")

    # A record that lands must clear the streak.
    write_good()
    if tracer.consecutive_gaps != 0:
        problems.append("a successful write did not reset the consecutive gap count")

    # Three in a row must escalate -- on the third, not before.
    escalated_at: int | None = None
    for attempt in (1, 2, 3):
        try:
            write_bad()
        except TraceWriteError:
            escalated_at = attempt
            break
    if escalated_at != MAX_CONSECUTIVE_GAPS:
        problems.append(
            f"escalated at gap {escalated_at}, expected {MAX_CONSECUTIVE_GAPS}"
        )

    # Every marker must itself be schema-valid, or the trajectory stops parsing.
    records = [
        json.loads(line)
        for line in tracer.path.read_text(encoding="utf-8").strip().splitlines()
    ]
    markers = [r for r in records if r["agent_name"] == GAP_AGENT_NAME]
    for marker in markers:
        problems.extend(f"gap marker: {p}" for p in validate_record(marker))
    if len(markers) != 4:
        problems.append(f"expected 4 gap markers on disk, found {len(markers)}")

    turn_ids = [r["turn_id"] for r in records]
    if turn_ids != sorted(turn_ids):
        problems.append(f"gap markers broke turn_id ordering: {turn_ids}")

    return Check(
        name="trace gap-marker policy",
        passed=not problems,
        detail=(
            "; ".join(problems)
            or (
                f"survives blips, resets on success, escalates at "
                f"{MAX_CONSECUTIVE_GAPS} consecutive; {len(markers)} markers, "
                "all schema-valid, sequence contiguous"
            )
        ),
        data={
            "markers": len(markers),
            "total_gaps": tracer.total_gaps,
            "escalated_at": escalated_at,
        },
    )


def check_executor_outcomes(executor: SandboxExecutor) -> Check:
    """Pass, fail and hang must classify correctly, and the hang must die."""
    expectations: list[tuple[str, Outcome]] = [
        ("test_pass.py", Outcome.PASS),
        ("test_fail.py", Outcome.FAIL),
        ("test_hang.py", Outcome.TIMEOUT),
    ]
    problems: list[str] = []
    observed: dict[str, str] = {}

    for target, expected in expectations:
        started = time.perf_counter()
        result = executor.run_once(SMOKE_PROJECT, pytest_args=[target])
        elapsed = time.perf_counter() - started
        observed[target] = result.outcome.value
        if result.outcome is not expected:
            problems.append(
                f"{target}: expected {expected.value}, got {result.outcome.value}"
                f" (exit={result.exit_code})"
            )
        if expected is Outcome.TIMEOUT and elapsed > executor.limits.wall_clock_s * 3:
            problems.append(f"{target}: took {elapsed:.1f}s to be killed")

    return Check(
        name="executor outcomes",
        passed=not problems,
        detail="; ".join(problems) or "pass/fail/timeout all classified correctly",
        data=observed,
    )


def _benchmark(
    executor: SandboxExecutor, strategy: Strategy, runs: int
) -> dict[str, float]:
    """Time ``runs`` executions of the passing smoke test."""
    durations: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        executor.run_once(SMOKE_PROJECT, pytest_args=["test_pass.py"], strategy=strategy)
        durations.append((time.perf_counter() - started) * 1000)
    return {
        "runs": runs,
        "median_ms": round(statistics.median(durations), 1),
        "mean_ms": round(statistics.fmean(durations), 1),
        "p95_ms": round(sorted(durations)[int(runs * 0.95) - 1], 1),
        "projected_full_eval_minutes": round(
            statistics.median(durations) * FULL_EVALUATION_RUNS / 1000 / 60, 1
        ),
    }


def check_execution_cost(traced: SandboxExecutor, untraced: SandboxExecutor) -> Check:
    """Price SPAWN and FORK, with and without per-run tracing."""
    data = {
        "spawn_traced": _benchmark(traced, Strategy.SPAWN, BENCHMARK_RUNS),
        "spawn_untraced": _benchmark(untraced, Strategy.SPAWN, BENCHMARK_RUNS),
        "fork_untraced": _benchmark(untraced, Strategy.FORK, BENCHMARK_RUNS),
    }
    spawn = data["spawn_untraced"]["median_ms"]
    fork = data["fork_untraced"]["median_ms"]
    data["fork_speedup"] = round(spawn / fork, 2) if fork else None
    data["tracing_overhead_ms"] = round(
        data["spawn_traced"]["median_ms"] - spawn, 1
    )
    return Check(
        name="execution cost",
        passed=True,  # informational: this check reports, it does not judge
        detail=(
            f"spawn {spawn} ms/run "
            f"(~{data['spawn_untraced']['projected_full_eval_minutes']} min per "
            f"evaluation), fork {fork} ms/run, "
            f"tracing adds {data['tracing_overhead_ms']} ms/run"
        ),
        data=data,
    )


def check_nondeterminism_fidelity(executor: SandboxExecutor) -> Check:
    """SPAWN must reproduce hash-order flakiness; FORK must be shown not to.

    This is the check that protects the methodology. A strategy that runs the
    corpus faster by collapsing the very nondeterminism we are measuring would
    report a flake rate of zero for a test that flakes constantly.
    """
    results: dict[str, dict[str, Any]] = {}
    for strategy in (Strategy.SPAWN, Strategy.FORK):
        counts: Counter[str] = Counter()
        for _ in range(FIDELITY_RUNS):
            result = executor.run_once(
                SMOKE_PROJECT, pytest_args=["test_hashorder.py"], strategy=strategy
            )
            counts[result.outcome.value] += 1
        failures = counts.get("fail", 0)
        results[strategy.value] = {
            "runs": FIDELITY_RUNS,
            "outcomes": dict(counts),
            "flake_rate": round(failures / FIDELITY_RUNS, 3),
            "varied": 0 < failures < FIDELITY_RUNS,
            "expected_to_vary": strategy_preserves_hash_order(strategy),
        }

    problems: list[str] = []
    if not results["spawn"]["varied"]:
        problems.append(
            "SPAWN produced a uniform outcome -- inter-process nondeterminism "
            "is being destroyed somewhere in the executor"
        )
    if results["fork"]["varied"]:
        problems.append(
            "FORK varied unexpectedly -- the documented hash-seed limitation "
            "does not match observed behaviour; re-check the claim"
        )

    return Check(
        name="nondeterminism fidelity",
        passed=not problems,
        detail=(
            "; ".join(problems)
            or (
                f"spawn flake rate {results['spawn']['flake_rate']} (varies), "
                f"fork {results['fork']['flake_rate']} (uniform, as documented)"
            )
        ),
        data=results,
    )


def check_concurrency_headroom(executor: SandboxExecutor) -> Check:
    """Measure whether running batches in parallel is fast *and* faithful.

    pytest's ~465 ms of import and collection is irreducible, so parallelism is
    the only remaining lever big enough to make 500-run verification
    affordable. But it is methodologically risky: CPU contention changes
    timing, and timing is what several corpus cases flake on.

    So this check measures both things at once -- the throughput gain, and
    whether the observed flake rate of a *timing-independent* flaky test
    (hash order) moves. Agreement here is necessary, not sufficient: Phase 1
    must re-check it against the race-condition cases, whose flake rates are
    expected to be contention-sensitive.
    """
    from concurrent.futures import ThreadPoolExecutor

    executor.stage(SMOKE_PROJECT)  # pre-stage so the race is not measured

    def one_run() -> bool:
        result = executor.run_once(SMOKE_PROJECT, pytest_args=["test_hashorder.py"])
        return result.failed

    measurements: dict[str, dict[str, Any]] = {}
    for workers in (1, CONCURRENCY_UNDER_TEST):
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            failures = sum(pool.map(lambda _: one_run(), range(CONCURRENCY_RUNS)))
        elapsed = time.perf_counter() - started
        measurements[f"workers_{workers}"] = {
            "runs": CONCURRENCY_RUNS,
            "wall_s": round(elapsed, 2),
            "ms_per_run": round(elapsed * 1000 / CONCURRENCY_RUNS, 1),
            "flake_rate": round(failures / CONCURRENCY_RUNS, 3),
            "projected_full_eval_minutes": round(
                elapsed / CONCURRENCY_RUNS * FULL_EVALUATION_RUNS / 60, 1
            ),
        }

    serial = measurements["workers_1"]
    parallel = measurements[f"workers_{CONCURRENCY_UNDER_TEST}"]
    measurements["speedup"] = round(serial["ms_per_run"] / parallel["ms_per_run"], 2)
    drift = abs(serial["flake_rate"] - parallel["flake_rate"])
    measurements["flake_rate_drift"] = round(drift, 3)

    # A hash-order flake rate is a binomial proportion; at n=60 per arm, a gap
    # beyond ~0.15 is more than sampling noise and warrants investigation.
    faithful = drift <= 0.15
    return Check(
        name="concurrency headroom",
        passed=faithful,
        detail=(
            f"{CONCURRENCY_UNDER_TEST} workers: {parallel['ms_per_run']} ms/run "
            f"({measurements['speedup']}x, ~{parallel['projected_full_eval_minutes']} "
            f"min per evaluation), flake rate drift {measurements['flake_rate_drift']}"
            + ("" if faithful else " -- EXCEEDS NOISE, parallelism is distorting")
        ),
        data=measurements,
    )


def main() -> int:
    """Run every gate check and report."""
    assert_sandboxed()

    trace_dir = REPO_ROOT / "traces"
    results_dir = REPO_ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=trace_dir, run_id=f"phase0-gate-{stamp}")
    traced = SandboxExecutor(tracer, trace_each_run=True)
    untraced = SandboxExecutor(tracer, trace_each_run=False)

    checks: list[Check] = [
        check_tracer_schema(trace_dir, stamp),
        check_trace_write_failure(trace_dir, stamp),
        check_executor_outcomes(untraced),
        check_execution_cost(traced, untraced),
        check_nondeterminism_fidelity(untraced),
        check_concurrency_headroom(untraced),
    ]

    trace_bytes = tracer.path.stat().st_size if tracer.path.exists() else 0
    payload = {
        "phase": 0,
        "python": sys.version.split()[0],
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "detail": c.detail,
                "data": c.data,
            }
            for c in checks
        ],
        "trace_file_bytes": trace_bytes,
    }
    (results_dir / "phase0_gate.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    width = max(len(c.name) for c in checks)
    print("\nPhase 0 gate\n" + "=" * (width + 60))
    for check in checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"  [{mark}] {check.name.ljust(width)}  {check.detail}")
    print(f"\n  trajectory file: {trace_bytes:,} bytes")
    print(f"  full results:    results/phase0_gate.json\n")

    failed = [c.name for c in checks if not c.passed]
    if failed:
        print(f"GATE FAILED: {', '.join(failed)}\n")
        return 1
    print("GATE PASSED\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
