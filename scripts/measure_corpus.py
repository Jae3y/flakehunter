"""Measure the true baseline flake rate of every corpus case.

Run inside the container:

    docker compose run --rm flakehunter python scripts/measure_corpus.py --runs 500

The measured rate is written back into each case's ``metadata.json`` under
``baseline``, together with the run count, worker count and failure signatures
that produced it. Recording the conditions alongside the number matters: a
flake rate measured at 8 workers is not necessarily the same number as one
measured serially, and a rate quoted without its conditions is not evidence.

Use ``--runs 60`` while tuning a case, ``--runs 500`` for the number of record.
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

from src.harness.protocol import workers_for  # noqa: E402
from src.harness.runner import DEFAULT_WORKERS, BatchReport, TestRunner  # noqa: E402
from src.sandbox.executor import SandboxExecutor, assert_sandboxed  # noqa: E402
from src.telemetry.tracer import Tracer  # noqa: E402

CORPUS = REPO_ROOT / "corpus"

#: The smoke case is plumbing, not evaluation, and never appears in results.
EXCLUDED = {"case_00_smoke"}


def discover_cases(only: list[str] | None = None) -> list[Path]:
    """Return the corpus case directories to measure, in numeric order."""
    cases = sorted(
        path
        for path in CORPUS.iterdir()
        if path.is_dir() and path.name.startswith("case_") and path.name not in EXCLUDED
    )
    if only:
        wanted = {name.strip() for name in only}
        cases = [c for c in cases if c.name in wanted or c.name.split("_")[1] in wanted]
    return cases


def load_metadata(case: Path) -> dict:
    """Read a case's metadata, or an empty skeleton if it has none yet."""
    path = case / "metadata.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def baseline_block(report: BatchReport) -> dict:
    """The measurement, with the conditions that produced it."""
    return {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs": report.runs,
        "workers": report.workers,
        "strategy": report.strategy,
        "failures": report.failures,
        "flake_rate": round(report.flake_rate, 4),
        "distinct_signatures": report.distinct_signatures,
        "signatures": dict(report.signatures),
        "wall_s": round(report.wall_s, 2),
        "sound": report.is_sound,
    }


def record_baseline(case: Path, report: BatchReport) -> None:
    """Write the measured baseline into the case's metadata.

    Raises:
        SystemExit: If ``corpus/`` is mounted read-only, with the command that
            does have write access.
    """
    path = case / "metadata.json"
    metadata = load_metadata(case)
    metadata["baseline"] = baseline_block(report)
    try:
        path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"cannot record into {path}: {exc}\n"
            "corpus/ is mounted read-only for the default service so that "
            "agent-authored code cannot alter the cases it is measured "
            "against. Record baselines with the tooling service instead:\n"
            "  docker compose run --rm recorder python scripts/measure_corpus.py "
            "--runs 500\n"
            "or pass --no-record to measure without writing."
        ) from exc


def main() -> int:
    """Measure every selected case and print a summary table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=500, help="runs per case")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="override the locked per-case worker count (not for headline numbers)",
    )
    parser.add_argument(
        "--protocol",
        action="store_true",
        help="use the locked per-case worker counts from src/harness/protocol.py",
    )
    parser.add_argument(
        "--cases", nargs="*", default=None, help="case names or numbers to limit to"
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="print results without writing metadata.json (use while tuning)",
    )
    args = parser.parse_args()

    assert_sandboxed()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"corpus-baseline-{stamp}")
    executor = SandboxExecutor(tracer, trace_each_run=False)
    runner = TestRunner(executor, tracer)

    cases = discover_cases(args.cases)
    if not cases:
        print("no matching cases", file=sys.stderr)
        return 1

    reports: list[tuple[Path, BatchReport]] = []
    for case in cases:
        # The locked protocol pins some cases to fewer workers because their
        # rate is contention-sensitive; see src/harness/protocol.py.
        workers = (
            workers_for(case.name)
            if args.protocol or args.workers is None
            else args.workers
        )
        report = runner.measure(
            case / "project",
            runs=args.runs,
            workers=workers,
            case_name=case.name,
            agent_name="corpus.baseline",
        )
        reports.append((case, report))
        if not args.no_record:
            record_baseline(case, report)
        print(f"{report.summary()}\n{report.signature_table(limit=3)}\n")

    print(f"\n{'case':<38} {'flake':>7} {'fail/runs':>12} {'sigs':>5} {'sound':>6}")
    print("-" * 74)
    total_wall = 0.0
    for case, report in reports:
        total_wall += report.wall_s
        print(
            f"{case.name:<38} {report.flake_rate:>6.1%} "
            f"{f'{report.failures}/{report.runs}':>12} "
            f"{report.distinct_signatures:>5} {str(report.is_sound):>6}"
        )
    print("-" * 74)
    worker_note = (
        "locked per-case protocol"
        if args.workers is None
        else f"{args.workers} workers (override)"
    )
    print(f"{len(reports)} cases, {total_wall:.0f}s total, {worker_note}")

    summary_path = REPO_ROOT / "results" / "corpus_baseline.json"
    summary_path.write_text(
        json.dumps(
            {
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runs_per_case": args.runs,
                "workers": args.workers or "locked per-case protocol",
                "cases": {c.name: baseline_block(r) for c, r in reports},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {summary_path.relative_to(REPO_ROOT)}\n")

    unsound = [c.name for c, r in reports if not r.is_sound]
    if unsound:
        print(f"UNSOUND (error runs present): {', '.join(unsound)}\n", file=sys.stderr)
    never = [c.name for c, r in reports if r.failures == 0]
    if never:
        print(f"NEVER FLAKED: {', '.join(never)}\n", file=sys.stderr)
    always = [c.name for c, r in reports if r.failures == r.runs]
    if always:
        print(f"ALWAYS FAILED (not flaky): {', '.join(always)}\n", file=sys.stderr)
    return 1 if (unsound or never or always) else 0


if __name__ == "__main__":
    raise SystemExit(main())
