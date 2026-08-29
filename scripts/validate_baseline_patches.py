"""Run the anti-cheat validator over the baseline arm's patches.

The baseline is never *stopped* by the validator -- it gets one call and no
feedback loop, which is the point of it being the baseline. But running the
validator over what it produced answers a question the results table needs:
how many of the baseline's fixes were legitimate, as opposed to merely
green?

Structural checks only by default. Those need no execution, so this is cheap
and can run while other measurements are in flight. Pass ``--stress`` to add
the behavioural check, which re-verifies each patch under CPU oversubscription
and costs real CPU.

    docker compose run --rm flakehunter python scripts/validate_baseline_patches.py
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

from src.baseline.one_shot import PatchApplicationError, apply_patch  # noqa: E402
from src.harness.protocol import runs_for, workers_for  # noqa: E402
from src.harness.runner import TestRunner  # noqa: E402
from src.harness.validator import FixValidator  # noqa: E402
from src.sandbox.executor import SandboxExecutor, assert_sandboxed  # noqa: E402
from src.telemetry.tracer import Tracer  # noqa: E402

CORPUS = REPO_ROOT / "corpus"
SCRATCH = Path("/scratch/baseline-validation")


def main() -> int:
    """Validate every baseline patch and summarise."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stress", action="store_true")
    args = parser.parse_args()

    assert_sandboxed()
    results_path = REPO_ROOT / "results" / "baseline_results.json"
    if not results_path.exists():
        print("no baseline results to validate", file=sys.stderr)
        return 1

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"validate-baseline-{stamp}")
    executor = SandboxExecutor(tracer, trace_each_run=False)
    runner = TestRunner(executor, tracer)

    document = json.loads(results_path.read_text(encoding="utf-8"))
    findings = []

    print("=" * 78)
    print(f"VALIDATING BASELINE PATCHES ({'with' if args.stress else 'without'} stress)")
    print("=" * 78)

    for entry in document.get("results", []):
        case_name = entry["case"]
        if "error" in entry or not entry.get("patch"):
            print(f"\n  {case_name}: no patch to validate")
            continue

        case = CORPUS / case_name
        metadata = json.loads((case / "metadata.json").read_text(encoding="utf-8"))
        validator = FixValidator(
            executor,
            runner,
            tracer,
            protected_paths=metadata.get("protected_paths", ["conftest.py"]),
        )

        patched = SCRATCH / case_name
        try:
            changed = apply_patch(case / "project", patched, entry["patch"])
        except PatchApplicationError as exc:
            print(f"\n  {case_name}: patch will not apply -- {exc}")
            findings.append({"case": case_name, "passed": False, "rejections": [str(exc)]})
            continue

        executor.clear_stage(patched)
        verdict = validator.validate(
            case_name,
            case / "project",
            patched,
            changed,
            stress_runs=runs_for(case_name, "stress"),
            workers=workers_for(case_name),
            run_stress=args.stress,
        )
        findings.append(
            {
                "case": case_name,
                "passed": verdict.passed,
                "rejections": verdict.rejections,
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail}
                    for c in verdict.checks
                ],
            }
        )
        mark = "ACCEPT" if verdict.passed else "REJECT"
        print(f"\n  [{mark}] {case_name}  files={changed}")
        for rejection in verdict.rejections:
            print(f"        {rejection}")

    accepted = sum(1 for f in findings if f["passed"])
    print("\n" + "-" * 78)
    print(f"  validator accepted {accepted}/{len(findings)} baseline patches")

    out = REPO_ROOT / "results" / "baseline_validation.json"
    out.write_text(
        json.dumps(
            {
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "stress_included": args.stress,
                "accepted": accepted,
                "total": len(findings),
                "findings": findings,
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
