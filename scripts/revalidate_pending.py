"""Re-run the validator over every patch already awaiting approval.

The validator got stricter partway through the evaluation: a live run produced
a patch that set a test fixture's timing constant to zero, deleting the
condition that produced the flakiness, and passed every check of the day
including the stress re-verification — with the window gone, there was nothing
left to stretch.

Patches accepted under the older, weaker validator are therefore not
trustworthy just because they are sitting in `results/pending_approval/`. This
re-checks all of them against the current rules and marks any that no longer
pass, so a human reviewing that directory is not handed a mask labelled as a
verified fix.

    docker compose run --rm flakehunter python scripts/revalidate_pending.py
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
APPROVAL = REPO_ROOT / "results" / "pending_approval"
SCRATCH = Path("/scratch/revalidate")


def main() -> int:
    """Re-validate each pending patch; annotate any that now fail."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stress", action="store_true", help="include the stress run")
    args = parser.parse_args()

    assert_sandboxed()
    if not APPROVAL.exists():
        print("nothing pending")
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"revalidate-{stamp}")
    executor = SandboxExecutor(tracer, trace_each_run=False)
    runner = TestRunner(executor, tracer)

    print("=" * 78)
    print("RE-VALIDATING PENDING PATCHES against the current validator")
    print("=" * 78)

    findings = []
    for package in sorted(APPROVAL.glob("case_*")):
        patch_file = package / "patch.json"
        if not patch_file.exists():
            print(f"\n  {package.name}: no patch.json")
            continue

        patch = json.loads(patch_file.read_text(encoding="utf-8"))
        case = CORPUS / package.name
        metadata = json.loads((case / "metadata.json").read_text(encoding="utf-8"))
        validator = FixValidator(
            executor,
            runner,
            tracer,
            protected_paths=metadata.get("protected_paths", ["conftest.py"]),
        )

        patched = SCRATCH / package.name
        try:
            changed = apply_patch(case / "project", patched, patch)
        except PatchApplicationError as exc:
            print(f"\n  {package.name}: will not apply -- {exc}")
            continue

        executor.clear_stage(patched)
        verdict = validator.validate(
            package.name,
            case / "project",
            patched,
            changed,
            stress_runs=runs_for(package.name, "stress"),
            workers=workers_for(package.name),
            run_stress=args.stress,
        )

        mark = "STILL VALID" if verdict.passed else "NOW REJECTED"
        print(f"\n  [{mark}] {package.name}  files={changed}")
        for check in verdict.checks:
            flag = "ok  " if check.passed else "FAIL"
            print(f"        [{flag}] {check.name}: {check.detail[:100]}")

        findings.append(
            {"case": package.name, "passed": verdict.passed, **verdict.to_dict()}
        )

        # A package that no longer validates must say so where a reviewer will
        # see it, not only in a results file they may not open.
        banner = package / "REVALIDATION.md"
        if verdict.passed:
            banner.write_text(
                f"# Re-validated {stamp}\n\nStill passes every current check.\n",
                encoding="utf-8",
            )
        else:
            reasons = "\n".join(f"- {r}" for r in verdict.rejections)
            banner.write_text(
                f"""# REJECTED on re-validation — {stamp}

**Do not apply this patch.** It was accepted by an earlier, weaker version of
the validator and fails the current one:

{reasons}

The patch and its original evidence are left in place as a record of what the
agent produced and why it was initially accepted.
""",
                encoding="utf-8",
            )

    rejected = [f["case"] for f in findings if not f["passed"]]
    print("\n" + "-" * 78)
    print(f"  {len(findings) - len(rejected)}/{len(findings)} still valid")
    if rejected:
        print(f"  NOW REJECTED: {', '.join(rejected)}")

    out = REPO_ROOT / "results" / "revalidation.json"
    out.write_text(
        json.dumps(
            {
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "stress_included": args.stress,
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
