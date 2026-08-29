"""Re-run the current validator over every patch that was previously accepted.

The validator got stricter partway through the evaluation. A live run produced
a patch that set a test fixture's timing constant to zero -- deleting the
condition that produced the flakiness -- and passed every check of the day
including the stress re-verification. With the window gone there was nothing
left to stretch.

That means acceptance under the older rules is not evidence of anything. Any
patch accepted before `test_conditions_unchanged` existed could carry the same
blind spot, and nobody had looked. This re-checks all of them.

Two sources are covered:

* ``results/pending_approval/`` — patches the agent accepted.
* ``results/baseline_results.json`` — patches the baseline produced. The
  baseline is never *stopped* by the validator (one call, no feedback, by
  design), but its patches were never checked at all, and "verified at zero
  failures" is a different claim from "legitimate".

Rejections carry the complete diff, so a reviewer can disagree with the verdict
rather than take it on trust.

    docker compose run --rm flakehunter python scripts/revalidate_pending.py --stress
"""

from __future__ import annotations

import argparse
import difflib
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

NEWLINE = "\n"


def is_test_path(relative: str) -> bool:
    """Whether a project-relative path is test infrastructure."""
    name = Path(relative).name
    return name.startswith("test_") or name == "conftest.py"


def render_diff(case_project: Path, patch: dict) -> str:
    """Unified diff of a patch against the pristine case.

    A rejection record that only names the failing check is an assertion. One
    that shows every changed line lets a reviewer check it.
    """
    blocks: list[str] = []
    for entry in patch.get("files", []):
        relative = str(entry.get("path", "")).lstrip("/")
        source = case_project / relative
        before = (
            source.read_text(encoding="utf-8").splitlines() if source.exists() else []
        )
        after = entry.get("new_content", "").splitlines()
        diff = list(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                lineterm="",
            )
        )
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        kind = "TEST FILE" if is_test_path(relative) else "SOURCE UNDER TEST"
        header = f"### `{relative}` - {kind} ({added} added, {removed} removed)"
        body = "```diff" + NEWLINE + NEWLINE.join(diff) + NEWLINE + "```"
        blocks.append(header + NEWLINE + NEWLINE + body)
    return (NEWLINE + NEWLINE).join(blocks)


def write_banner(package: Path, stamp: str, verdict, case_project: Path, patch: dict, changed: list[str]) -> None:
    """Record the re-validation verdict where a reviewer will see it."""
    banner = package / "REVALIDATION.md"
    if verdict.passed:
        banner.write_text(
            f"# Re-validated {stamp}{NEWLINE}{NEWLINE}"
            f"Passes every current check, including `test_conditions_unchanged`."
            f"{NEWLINE}",
            encoding="utf-8",
        )
        return

    reasons = NEWLINE.join(f"- {r}" for r in verdict.rejections)
    source_files = [f for f in changed if not is_test_path(f)]
    test_files = [f for f in changed if is_test_path(f)]
    banner.write_text(
        f"""# REJECTED on re-validation - {stamp}

**Do not apply this patch.** It was accepted by an earlier, weaker version of
the validator and fails the current one:

{reasons}

## What actually changed

- **Source under test:** {source_files or "none"}
- **Test files:** {test_files or "none"}

Whether real source logic was changed alongside the test edit matters for how
this is characterised, so the complete diff follows rather than a summary.

{render_diff(case_project, patch)}

The patch and its original evidence are left in place as a record of what was
produced and why it was initially accepted.
""",
        encoding="utf-8",
    )


def collect_targets() -> list[tuple[str, str, dict]]:
    """Every previously-accepted patch: (source, case_name, patch)."""
    targets: list[tuple[str, str, dict]] = []

    for package in sorted(APPROVAL.glob("case_*")) if APPROVAL.exists() else []:
        patch_file = package / "patch.json"
        if patch_file.exists():
            targets.append(
                ("agent", package.name, json.loads(patch_file.read_text(encoding="utf-8")))
            )

    baseline_path = REPO_ROOT / "results" / "baseline_results.json"
    if baseline_path.exists():
        document = json.loads(baseline_path.read_text(encoding="utf-8"))
        for entry in document.get("results", []):
            if entry.get("patch"):
                targets.append(("baseline", entry["case"], entry["patch"]))
    return targets


def main() -> int:
    """Re-validate every previously-accepted patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stress", action="store_true", help="include the stress run")
    parser.add_argument(
        "--arm",
        choices=["agent", "baseline", "both"],
        default="both",
        help="which arm's patches to re-check",
    )
    args = parser.parse_args()

    assert_sandboxed()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"revalidate-{stamp}")
    executor = SandboxExecutor(tracer, trace_each_run=False)
    runner = TestRunner(executor, tracer)

    targets = [t for t in collect_targets() if args.arm in ("both", t[0])]
    print("=" * 78)
    print(f"RE-VALIDATING {len(targets)} PREVIOUSLY-ACCEPTED PATCHES")
    print(f"stress={'on' if args.stress else 'off'}")
    print("=" * 78)

    findings = []
    for arm, case_name, patch in targets:
        case = CORPUS / case_name
        metadata = json.loads((case / "metadata.json").read_text(encoding="utf-8"))
        validator = FixValidator(
            executor,
            runner,
            tracer,
            protected_paths=metadata.get("protected_paths", ["conftest.py"]),
        )

        patched = SCRATCH / f"{arm}-{case_name}"
        try:
            changed = apply_patch(case / "project", patched, patch)
        except PatchApplicationError as exc:
            print(f"{NEWLINE}  [{arm}] {case_name}: will not apply -- {exc}")
            continue

        executor.clear_stage(patched)
        verdict = validator.validate(
            f"{arm}:{case_name}",
            case / "project",
            patched,
            changed,
            stress_runs=runs_for(case_name, "stress"),
            workers=workers_for(case_name),
            run_stress=args.stress,
        )

        mark = "VALID " if verdict.passed else "REJECT"
        touched_tests = [f for f in changed if is_test_path(f)]
        print(f"{NEWLINE}  [{mark}] {arm:<8} {case_name}")
        print(f"           files={changed}")
        if touched_tests:
            print(f"           touches test infrastructure: {touched_tests}")
        for check in verdict.checks:
            if not check.passed:
                print(f"           FAIL {check.name}: {check.detail[:110]}")

        findings.append(
            {
                "arm": arm,
                "case": case_name,
                "passed": verdict.passed,
                "files_changed": changed,
                "touched_test_files": touched_tests,
                **verdict.to_dict(),
            }
        )
        if arm == "agent":
            write_banner(APPROVAL / case_name, stamp, verdict, case / "project", patch, changed)

    print(NEWLINE + "=" * 78)
    for arm in ("agent", "baseline"):
        rows = [f for f in findings if f["arm"] == arm]
        if not rows:
            continue
        rejected = [f["case"] for f in rows if not f["passed"]]
        print(f"  {arm}: {len(rows) - len(rejected)}/{len(rows)} still valid")
        if rejected:
            print(f"    REJECTED: {', '.join(rejected)}")

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
        + NEWLINE,
        encoding="utf-8",
    )
    print(f"{NEWLINE}  wrote {out.relative_to(REPO_ROOT)}{NEWLINE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
