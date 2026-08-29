"""Check whether running batches in parallel distorts the flake rate.

Phase 0 justified 8 workers using a *timing-independent* flaky test (hash
order), which drifted by 0.067 -- inside sampling noise. That was necessary
but not sufficient: the race, port, tempfile, async and publication cases all
flake on timing, and CPU contention changes timing.

This script measures each case serially and at 8 workers and reports the
absolute drift. The agreed policy:

* drift within ~10-15 percentage points -> keep the case at 8 workers;
* drift beyond that -> pin the case class to a lower worker count.

Either way, **baseline and agent must use the same worker count for the same
case**. A concurrency difference between the two arms would invalidate the
comparison the whole project exists to make, so the chosen count is written
into the case's metadata rather than left to a caller's default.

    docker compose run --rm recorder python scripts/concurrency_drift.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.harness.runner import DEFAULT_WORKERS, TestRunner  # noqa: E402
from src.sandbox.executor import SandboxExecutor, assert_sandboxed  # noqa: E402
from src.telemetry.tracer import Tracer  # noqa: E402

CORPUS = REPO_ROOT / "corpus"

#: Cases whose nondeterminism is timing-driven, and so plausibly sensitive to
#: CPU contention. The others (hash order, unseeded RNG, collection order) are
#: timing-independent and were covered by the Phase 0 gate.
TIMING_SENSITIVE = [
    "case_01_race_condition",
    "case_03_port_collision",
    "case_04_clock_dependence",
    "case_07_network_timeout",
    "case_08_tempfile_collision",
    "case_09_float_tolerance",
    "case_10_async_ordering",
    "case_12_masking_trap",
]

#: Drift beyond this many percentage points is treated as distortion rather
#: than sampling noise.
DRIFT_THRESHOLD = 0.15


def main() -> int:
    """Measure each timing-sensitive case serially and in parallel."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=300, help="runs per arm")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--cases", nargs="*", default=None)
    args = parser.parse_args()

    assert_sandboxed()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"drift-{stamp}")
    executor = SandboxExecutor(tracer, trace_each_run=False)
    runner = TestRunner(executor, tracer)

    names = args.cases or TIMING_SENSITIVE
    findings: dict[str, dict] = {}

    for name in names:
        project = CORPUS / name / "project"
        if not project.is_dir():
            print(f"skipping unknown case {name}", file=sys.stderr)
            continue

        serial = runner.measure(
            project,
            runs=args.runs,
            workers=1,
            case_name=name,
            agent_name="corpus.drift.serial",
        )
        parallel = runner.measure(
            project,
            runs=args.runs,
            workers=args.workers,
            case_name=name,
            agent_name="corpus.drift.parallel",
        )
        drift = abs(serial.flake_rate - parallel.flake_rate)
        findings[name] = {
            "serial_flake_rate": round(serial.flake_rate, 4),
            "parallel_flake_rate": round(parallel.flake_rate, 4),
            "drift": round(drift, 4),
            "runs_per_arm": args.runs,
            "parallel_workers": args.workers,
            "within_threshold": drift <= DRIFT_THRESHOLD,
            "recommended_workers": args.workers if drift <= DRIFT_THRESHOLD else 1,
            "serial_wall_s": round(serial.wall_s, 1),
            "parallel_wall_s": round(parallel.wall_s, 1),
        }
        print(
            f"{name:<32} serial {serial.flake_rate:>6.1%}  "
            f"parallel {parallel.flake_rate:>6.1%}  "
            f"drift {drift:>6.1%}  "
            f"{'ok' if drift <= DRIFT_THRESHOLD else 'PIN TO SERIAL'}"
        )

    out = REPO_ROOT / "results" / "concurrency_drift.json"
    out.write_text(
        json.dumps(
            {
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "threshold": DRIFT_THRESHOLD,
                "cases": findings,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")

    distorted = [n for n, f in findings.items() if not f["within_threshold"]]
    if distorted:
        print(f"\nPIN TO SERIAL: {', '.join(distorted)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
