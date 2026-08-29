"""Diagnose why corpus flake rates move between measurements.

Case 01 read 47% (n=100, alone), then 20.6% and 33.4% (n=500, full sweeps).
Case 12 read 38%, then 6.2%, then 3.2%. The code did not change between the
n=500 sweeps, so "sample size" cannot be the whole story and neither can
"we edited the case".

Three explanations are on the table, and they make different predictions:

**Sampling noise.** If a batch of N runs is N independent Bernoulli trials,
the standard deviation of the measured rate is sqrt(p(1-p)/N) -- 2.0% at
N=500 for p=0.3. Repeating the measurement should scatter within roughly
that. A 20.6% -> 33.4% move is six of those standard deviations, so if this
is the whole story the repeats will be tight and the earlier numbers were a
fluke.

**Time-varying machine state** (thermal, host background load, Docker VM).
Then runs inside one batch are *correlated* rather than independent: the whole
batch is measured under whichever state the machine is in. Between-batch
spread would far exceed the binomial prediction -- overdispersion -- and the
rate would also drift *within* a batch, which the per-chunk column tests.

**Position/ordering effects.** Measuring a case first, cold, differs from
measuring it after eleven other cases have loaded the machine. The
position test at the end measures the same case before and after a filler
sweep, inside one container invocation.

Overdispersion is the discriminating statistic. If observed spread is close
to binomial, the earlier numbers were noise and a larger N fixes everything.
If it is several times binomial, then N does *not* fix it, because the runs
in a batch are not independent samples -- and the protocol has to change
rather than the sample size.

    docker compose run --rm flakehunter python scripts/diagnose_drift.py
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sandbox.executor import SandboxExecutor, assert_sandboxed  # noqa: E402
from src.telemetry.tracer import Tracer  # noqa: E402

CORPUS = REPO_ROOT / "corpus"

#: Cases probed. 01 and 12 are the two that moved; 06 is a control whose
#: nondeterminism comes from the RNG rather than from thread timing, so it
#: should *not* show machine-state sensitivity. 03 is a second timing case.
DEFAULT_CASES = [
    "case_01_race_condition",
    "case_12_masking_trap",
    "case_03_port_collision",
    "case_06_unseeded_randomness",
]

#: Cases used as filler load in the position test.
FILLER = [
    "case_05_hash_iteration_order",
    "case_09_float_tolerance",
    "case_10_async_ordering",
    "case_11_cache_leak",
]

#: Chunks each batch is split into for the within-batch trend column.
CHUNKS = 5


def run_batch(
    executor: SandboxExecutor, project: Path, runs: int, workers: int
) -> list[bool]:
    """Run a case ``runs`` times and return per-run failure flags, in order.

    ``ThreadPoolExecutor.map`` yields results in submission order, so index i
    is the i-th run started. That is what makes the within-batch trend
    meaningful.
    """
    executor.stage(project)

    def one(_: int) -> bool:
        return executor.run_once(project, pytest_args=["--tb=line"]).failed

    if workers <= 1:
        return [one(i) for i in range(runs)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, range(runs)))


def binomial_sd(rate: float, runs: int) -> float:
    """Standard deviation the rate would have if runs were independent."""
    return math.sqrt(max(rate * (1.0 - rate), 0.0) / runs)


def chunk_rates(outcomes: list[bool], chunks: int) -> list[float]:
    """Failure rate within each successive slice of a batch."""
    size = len(outcomes) // chunks
    return [
        sum(outcomes[i * size : (i + 1) * size]) / size for i in range(chunks)
    ]


def analyse(case: str, batches: list[list[bool]]) -> dict:
    """Summarise repeatability and overdispersion for one case."""
    rates = [sum(batch) / len(batch) for batch in batches]
    runs = len(batches[0])
    mean = statistics.fmean(rates)
    observed_sd = statistics.stdev(rates) if len(rates) > 1 else 0.0
    expected_sd = binomial_sd(mean, runs)
    overdispersion = observed_sd / expected_sd if expected_sd else float("inf")

    all_chunks = [chunk_rates(batch, CHUNKS) for batch in batches]
    # Average each chunk position across batches: a monotonic trend here is a
    # within-batch warm-up or thermal effect rather than between-batch drift.
    by_position = [
        statistics.fmean([chunks[i] for chunks in all_chunks])
        for i in range(CHUNKS)
    ]

    return {
        "case": case,
        "runs_per_batch": runs,
        "batches": len(batches),
        "rates": [round(r, 4) for r in rates],
        "mean": round(mean, 4),
        "min": round(min(rates), 4),
        "max": round(max(rates), 4),
        "spread": round(max(rates) - min(rates), 4),
        "observed_sd": round(observed_sd, 4),
        "binomial_sd": round(expected_sd, 4),
        "overdispersion": round(overdispersion, 2),
        "chunk_rates_by_position": [round(c, 4) for c in by_position],
    }


def print_analysis(result: dict) -> None:
    """Print one case's repeatability analysis."""
    print(f"\n  {result['case']}")
    print(
        f"    {result['batches']} batches x {result['runs_per_batch']} runs: "
        + ", ".join(f"{r:.1%}" for r in result["rates"])
    )
    print(
        f"    mean {result['mean']:.1%}  spread {result['spread']:.1%}  "
        f"observed sd {result['observed_sd']:.2%}  "
        f"binomial sd {result['binomial_sd']:.2%}  "
        f"OVERDISPERSION {result['overdispersion']}x"
    )
    print(
        "    within-batch by position: "
        + " -> ".join(f"{c:.1%}" for c in result["chunk_rates_by_position"])
    )


def probe_machine_speed(iterations: int = 4_000_000) -> float:
    """Time a fixed CPU-bound loop, single-threaded, with the workers idle.

    This measures the machine's *capability* rather than any case's behaviour.
    If this number grows over a session while the code under test is
    unchanged, the host is getting slower -- frequency scaling under sustained
    load, or competing work on the host -- and any timing-sensitive flake rate
    measured against it is measuring the machine as much as the bug.
    """
    started = time.perf_counter()
    total = 0
    for index in range(iterations):
        total += index % 7
    elapsed = time.perf_counter() - started
    assert total > 0  # keep the loop from being optimised away
    return elapsed


def correlation(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation, or 0.0 when a series is constant."""
    if len(xs) < 2:
        return 0.0
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    var_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    return cov / (var_x * var_y) if var_x and var_y else 0.0


def run_interleaved(
    executor: SandboxExecutor, runs: int, workers: int, cycles: int
) -> dict:
    """Track machine speed and two cases' rates together, over one session.

    The two cases are chosen to separate the hypotheses. Case 01 is a thread
    race: its rate should move with how fast the machine is running. Case 06
    draws from the RNG and has no timing component at all, so it is the
    control -- if the machine is slowing and only case 01 responds, the cause
    is machine speed rather than anything about the harness.
    """
    timing_case = CORPUS / "case_01_race_condition" / "project"
    control_case = CORPUS / "case_06_unseeded_randomness" / "project"

    print("\n" + "=" * 78)
    print("INTERLEAVED PROBE: machine speed vs a timing case vs a control")
    print("=" * 78)
    print(f"\n  {cycles} cycles of: probe, case_01 ({runs} runs), case_06 ({runs} runs)")
    print(f"\n  {'cycle':>6}{'probe_s':>10}{'case_01':>10}{'case_06':>10}")
    print("  " + "-" * 34)

    probes: list[float] = []
    timing_rates: list[float] = []
    control_rates: list[float] = []

    for cycle in range(cycles):
        probe = probe_machine_speed()
        timing = run_batch(executor, timing_case, runs, workers)
        control = run_batch(executor, control_case, runs, workers)
        timing_rate = sum(timing) / len(timing)
        control_rate = sum(control) / len(control)
        probes.append(probe)
        timing_rates.append(timing_rate)
        control_rates.append(control_rate)
        print(
            f"  {cycle:>6}{probe:>10.3f}{timing_rate:>9.1%}{control_rate:>10.1%}"
        )

    probe_drift = (probes[-1] / probes[0] - 1) if probes[0] else 0.0
    result = {
        "cycles": cycles,
        "runs_per_batch": runs,
        "probe_seconds": [round(p, 4) for p in probes],
        "case_01_rates": [round(r, 4) for r in timing_rates],
        "case_06_rates": [round(r, 4) for r in control_rates],
        "probe_slowdown": round(probe_drift, 4),
        "case_01_spread": round(max(timing_rates) - min(timing_rates), 4),
        "case_06_spread": round(max(control_rates) - min(control_rates), 4),
        "corr_probe_vs_case_01": round(correlation(probes, timing_rates), 3),
        "corr_probe_vs_case_06": round(correlation(probes, control_rates), 3),
    }

    print("\n  machine slowed by      : " f"{probe_drift:+.1%} from first to last cycle")
    print(f"  case_01 spread         : {result['case_01_spread']:.1%} (timing-sensitive)")
    print(f"  case_06 spread         : {result['case_06_spread']:.1%} (control)")
    print(f"  corr(probe, case_01)   : {result['corr_probe_vs_case_01']:+.3f}")
    print(f"  corr(probe, case_06)   : {result['corr_probe_vs_case_06']:+.3f}")
    return result


def main() -> int:
    """Measure repeatability, within-batch trend, and position effects."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--runs", type=int, default=500)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--cases", nargs="*", default=DEFAULT_CASES)
    parser.add_argument("--mode", choices=["repeat", "interleave"], default="repeat")
    parser.add_argument("--cycles", type=int, default=8)
    args = parser.parse_args()

    if args.mode == "interleave":
        assert_sandboxed()
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"interleave-{stamp}")
        executor = SandboxExecutor(tracer, trace_each_run=False)
        result = run_interleaved(executor, args.runs, args.workers, args.cycles)
        out = REPO_ROOT / "results" / "drift_interleaved.json"
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out.relative_to(REPO_ROOT)}\n")
        return 0

    assert_sandboxed()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"diagnose-{stamp}")
    executor = SandboxExecutor(tracer, trace_each_run=False)

    print("=" * 78)
    print("DRIFT DIAGNOSIS: is a batch of N runs actually N independent trials?")
    print("=" * 78)
    print(
        f"\n  {args.repeats} identical batches of {args.runs} runs at "
        f"{args.workers} workers, back to back, same container."
    )

    results: list[dict] = []
    for case in args.cases:
        project = CORPUS / case / "project"
        batches = [
            run_batch(executor, project, args.runs, args.workers)
            for _ in range(args.repeats)
        ]
        result = analyse(case, batches)
        results.append(result)
        print_analysis(result)

    # --- position test: same case, before and after a filler sweep ----------
    print("\n" + "=" * 78)
    print("POSITION TEST: same case, measured cold then after a filler sweep")
    print("=" * 78)

    probe = args.cases[0]
    probe_project = CORPUS / probe / "project"
    before = run_batch(executor, probe_project, args.runs, args.workers)
    for filler in FILLER:
        run_batch(executor, CORPUS / filler / "project", args.runs, args.workers)
    after = run_batch(executor, probe_project, args.runs, args.workers)

    before_rate = sum(before) / len(before)
    after_rate = sum(after) / len(after)
    position = {
        "case": probe,
        "before_filler": round(before_rate, 4),
        "after_filler": round(after_rate, 4),
        "shift": round(after_rate - before_rate, 4),
    }
    print(
        f"\n  {probe}: before {before_rate:.1%}  after {after_rate:.1%}  "
        f"shift {after_rate - before_rate:+.1%}"
    )

    # --- verdict -------------------------------------------------------------
    print("\n" + "=" * 78)
    print(f"{'case':<32}{'mean':>8}{'spread':>9}{'overdisp':>10}  verdict")
    print("-" * 78)
    for result in results:
        if result["overdispersion"] <= 1.5:
            verdict = "binomial - N fixes it"
        elif result["overdispersion"] <= 3.0:
            verdict = "mildly overdispersed"
        else:
            verdict = "NOT independent trials"
        print(
            f"{result['case']:<32}{result['mean']:>7.1%}{result['spread']:>9.1%}"
            f"{result['overdispersion']:>9.1f}x  {verdict}"
        )
    print("-" * 78)

    out = REPO_ROOT / "results" / "drift_diagnosis.json"
    out.write_text(
        json.dumps(
            {
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "repeats": args.repeats,
                "runs_per_batch": args.runs,
                "workers": args.workers,
                "cases": results,
                "position_test": position,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out.relative_to(REPO_ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
