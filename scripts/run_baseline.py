"""Run the one-shot baseline across the corpus and record what it achieved.

    docker compose run --rm flakehunter python scripts/run_baseline.py
    docker compose run --rm flakehunter python scripts/run_baseline.py --dry-run

``--dry-run`` makes the LLM call and applies the patch to a scratch copy but
skips the 500-run verification. Useful for checking the prompt and the patch
plumbing without spending twenty minutes of CPU.
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

from src.baseline.one_shot import run_baseline_case  # noqa: E402
from src.harness.protocol import runs_for, workers_for  # noqa: E402
from src.harness.runner import TestRunner  # noqa: E402
from src.llm.client import GeminiClient, LLMError  # noqa: E402
from src.sandbox.executor import SandboxExecutor, assert_sandboxed  # noqa: E402
from src.telemetry.tracer import Tracer  # noqa: E402

CORPUS = REPO_ROOT / "corpus"
EXCLUDED = {"case_00_smoke"}


def discover_cases(only: list[str] | None) -> list[Path]:
    """Corpus cases in numeric order, smoke case excluded."""
    cases = sorted(
        path
        for path in CORPUS.iterdir()
        if path.is_dir() and path.name.startswith("case_") and path.name not in EXCLUDED
    )
    if only:
        wanted = {name.strip() for name in only}
        cases = [c for c in cases if c.name in wanted or c.name.split("_")[1] in wanted]
    return cases


def main() -> int:
    """Run the baseline over every selected case."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help="override the locked verification run count",
    )
    parser.add_argument("--cases", nargs="*", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    assert_sandboxed()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"baseline-{stamp}")
    client = GeminiClient(tracer)
    executor = SandboxExecutor(tracer, trace_each_run=False)
    runner = TestRunner(executor, tracer)

    cases = discover_cases(args.cases)
    print("=" * 78)
    print(f"BASELINE: one LLM call per case, model={client.model}")
    print("=" * 78)

    results = []
    started = time.perf_counter()
    for case in cases:
        try:
            # Both arms read the same protocol module, so they cannot be
            # measured under different settings.
            workers = workers_for(case.name)
            verify_runs = args.runs or runs_for(case.name, "verify")
            result = run_baseline_case(
                case,
                client,
                executor,
                runner,
                runs=0 if args.dry_run else verify_runs,
                workers=workers,
            )
        except LLMError as exc:
            print(f"\n  {case.name}: LLM CALL FAILED -- {exc}")
            results.append({"case": case.name, "error": str(exc)})
            continue

        mark = "OK " if result.cause_identified else "MISS"
        print(f"\n  {case.name}")
        print(
            f"    class    : {result.root_cause_class} "
            f"(expected {result.expected_class}) [{mark}] conf={result.confidence}"
        )
        print(f"    files    : {', '.join(result.files_changed) or '(none)'}")
        if not result.applied:
            print(f"    APPLY FAILED: {result.apply_error}")
        elif result.report is not None:
            print(
                f"    residual : {result.report.failures}/{result.report.runs} "
                f"= {result.report.flake_rate:.1%} "
                f"(was {result.baseline_flake_rate:.1%})"
                if result.baseline_flake_rate is not None
                else ""
            )
        print(
            f"    tokens   : {result.prompt_tokens} in / {result.output_tokens} out"
        )
        results.append(result.to_dict())

        # Written after every case, so an interrupted run still leaves results.
        (REPO_ROOT / "results" / "baseline_partial.json").write_text(
            json.dumps(
                {
                    "note": "partial; written after each case",
                    "model": client.model,
                    "usage": client.usage_summary(),
                    "results": results,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    elapsed = time.perf_counter() - started
    usage = client.usage_summary()

    if not args.dry_run:
        print("\n" + "=" * 78)
        print(f"{'case':<34}{'cause?':>8}{'residual':>12}{'fixed?':>9}")
        print("-" * 78)
        for entry in results:
            if "error" in entry:
                print(f"{entry['case']:<34}{'ERR':>8}{'-':>12}{'-':>9}")
                continue
            residual = entry["residual_flake_rate"]
            print(
                f"{entry['case']:<34}"
                f"{('yes' if entry['cause_identified'] else 'no'):>8}"
                f"{(f'{residual:.1%}' if residual is not None else 'n/a'):>12}"
                f"{('yes' if entry['fixed'] else 'no'):>9}"
            )
        print("-" * 78)
        identified = sum(1 for e in results if e.get("cause_identified"))
        fixed = sum(1 for e in results if e.get("fixed"))
        print(f"  cause identified: {identified}/{len(results)}")
        print(f"  fixed (0 failures): {fixed}/{len(results)}")

    print(f"\n  wall clock: {elapsed / 60:.1f} min")
    print(f"  tokens    : {usage}")

    out = REPO_ROOT / "results" / (
        "baseline_dry_run.json" if args.dry_run else "baseline_results.json"
    )
    out.write_text(
        json.dumps(
            {
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "model": client.model,
                "runs_per_case": 0 if args.dry_run else (args.runs or "locked per-case protocol"),
                "workers": "locked per-case protocol",
                "wall_clock_s": round(elapsed, 1),
                "usage": usage,
                "results": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {out.relative_to(REPO_ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
