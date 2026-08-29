"""Record a human's approve/reject decision on a pending patch.

Without this, the trajectory's human checkpoint stays open forever. The agent
writes `decision: "pending"` when it hands a patch over, and nothing ever
closes it — so a reviewer reading the trace cannot tell whether a person looked
and said no, or never looked at all. Those are very different, and the record
should distinguish them.

This is the one place in the project where a human decision enters the
trajectory. It runs on the host, not in the sandbox, because that is where the
person is.

    python scripts/record_decision.py case_12_masking_trap --approve
    python scripts/record_decision.py case_12_masking_trap --reject --note "..."

Recording an approval does **not** install the patch. Applying it is a separate
`--apply` flag, so that saying "yes, this looks right" and "yes, write it into
the repository now" stay two deliberate acts rather than one.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.telemetry.tracer import Tracer  # noqa: E402

APPROVAL_ROOT = REPO_ROOT / "results" / "pending_approval"
CORPUS = REPO_ROOT / "corpus"


def apply_patch_files(package: Path, case: str) -> list[str]:
    """Copy the approved patch over the real case. The only write to corpus/."""
    source_root = package / "patched_files"
    target_root = CORPUS / case / "project"
    written: list[str] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_root)
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        written.append(str(relative))
    return written


def update_results(case: str, decision: str, note: str) -> list[str]:
    """Record the decision against the case's record, wherever it lives.

    Quota forced the agent arm across several result files, so a case's record
    is not reliably in ``agent_results.json``. Searching all of them is the
    difference between the decision being recorded and silently going nowhere:
    the first version of this script reported "no matching case found" for the
    only patch awaiting approval.
    """
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    updated: list[str] = []
    for path in sorted((REPO_ROOT / "results").glob("agent_results*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        changed = False
        for result in document.get("results", []):
            if result.get("case") != case:
                continue
            result["human_decision"] = {"decision": decision, "note": note, "at": stamp}
            result["status"] = (
                "APPROVED" if decision == "approved" else "REJECTED_BY_HUMAN"
            )
            changed = True
        if changed:
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            updated.append(path.name)
    return updated


def main() -> int:
    """Close the open human checkpoint for one case."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="case directory name, e.g. case_12_masking_trap")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")
    parser.add_argument("--note", default="", help="why, in your own words")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="also copy the patch over corpus/<case>/project/ (approval only)",
    )
    args = parser.parse_args()

    package = APPROVAL_ROOT / args.case
    if not package.exists():
        print(f"no pending package at {package}", file=sys.stderr)
        return 1

    decision = "approved" if args.approve else "rejected"
    note = args.note or ("approved by human review" if args.approve else "rejected by human review")

    applied: list[str] = []
    if args.apply:
        if not args.approve:
            print("--apply only makes sense with --approve", file=sys.stderr)
            return 1
        applied = apply_patch_files(package, args.case)

    # The trajectory turn. This is what closes the checkpoint the agent opened.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"human-decision-{stamp}")
    with tracer.turn(
        "human.reviewer",
        "human",
        f"Review the patch held in {package.relative_to(REPO_ROOT)} and decide "
        f"whether it may be applied.",
    ) as turn:
        turn.call(
            "record_decision",
            case=args.case,
            decision=decision,
            applied_to_corpus=bool(applied),
            files=applied,
        )
        turn.respond(
            stdout=(
                f"decision={decision}; "
                + (f"applied {len(applied)} file(s) to corpus/" if applied
                   else "patch NOT applied to corpus/")
            ),
            exit_code=0,
        )
        turn.checkpoint(prompted=True, decision=decision, note=note)
        turn.reflect(
            f"A human reviewed the patch and {decision} it. "
            + (
                "The corpus now carries the change."
                if applied
                else "The corpus is unchanged; approval was recorded without applying."
            )
        )

    (package / "DECISION.md").write_text(
        f"""# Human decision — {decision.upper()}

**Recorded:** {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
**Note:** {note}
**Applied to `corpus/`:** {"yes — " + ", ".join(applied) if applied else "no"}

This closes the human checkpoint the agent opened when it produced the patch.
The corresponding trajectory turn is `traces/{tracer.run_id}.jsonl`, where
`human_checkpoint.decision` is `{decision}` rather than `pending`.
""",
        encoding="utf-8",
    )

    updated = update_results(args.case, decision, note)

    print(f"  decision      : {decision}")
    print(f"  checkpoint    : closed in traces/{tracer.run_id}.jsonl")
    print(f"  package       : {(package / 'DECISION.md').relative_to(REPO_ROOT)}")
    print(f"  results record: {', '.join(updated) if updated else 'NO MATCHING CASE FOUND'}")
    if applied:
        print(f"  applied       : {len(applied)} file(s) to corpus/{args.case}/project/")
    elif args.approve:
        print("  applied       : no (re-run with --apply to install the patch)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
