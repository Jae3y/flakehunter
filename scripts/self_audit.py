"""Check the unattended run against its own compliance checklist.

Written to be run, not asserted. Each check either passes with the evidence it
used or fails with what is missing, so the session log can quote results rather
than claims.

    python scripts/self_audit.py

Runs on the host as well as in the container -- it inspects files and git, and
never executes a case.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: A number that looks like a real measurement rather than prose.
MEASUREMENT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|ms|s\b|pts|/\d+)")

#: Sections every decision entry must carry.
DECISION_SECTIONS = ("**Tension.**", "**Chosen.**", "**Why.**", "**Revisit if.**")


@dataclass(slots=True)
class Audit:
    """One checklist item."""

    name: str
    passed: bool
    evidence: str

    def render(self) -> str:
        """Format for printing."""
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name}\n       {self.evidence}"


def audit_changelog() -> Audit:
    """Every changelog entry must carry real measured numbers."""
    path = REPO_ROOT / "docs" / "CHANGELOG.md"
    if not path.exists():
        return Audit("changelog entries with measurements", False, "CHANGELOG.md missing")

    text = path.read_text(encoding="utf-8")
    entries = re.split(r"^## (?=\d{3} )", text, flags=re.MULTILINE)[1:]
    if not entries:
        return Audit("changelog entries with measurements", False, "no numbered entries")

    bare: list[str] = []
    counts: list[str] = []
    for entry in entries:
        title = entry.splitlines()[0].strip()
        found = MEASUREMENT.findall(entry)
        counts.append(f"{title.split(' — ')[0]}: {len(found)} measurements")
        if len(found) < 3:
            bare.append(title)

    return Audit(
        "changelog entries with measurements",
        not bare,
        "; ".join(counts) + (f" | THIN: {bare}" if bare else ""),
    )


def audit_decisions() -> Audit:
    """Every decision must state tension, choice, reasoning and a revisit trigger."""
    path = REPO_ROOT / "DECISIONS.md"
    if not path.exists():
        return Audit("decisions logged with reasoning", False, "DECISIONS.md missing")

    text = path.read_text(encoding="utf-8")
    entries = re.split(r"^## (?=D-\d{3})", text, flags=re.MULTILINE)[1:]
    if not entries:
        return Audit("decisions logged with reasoning", False, "no D-NNN entries")

    incomplete: list[str] = []
    for entry in entries:
        ident = entry.split()[0]
        missing = [s for s in DECISION_SECTIONS if s not in entry]
        if missing:
            incomplete.append(f"{ident} missing {missing}")

    return Audit(
        "decisions logged with reasoning",
        not incomplete,
        f"{len(entries)} entries, all four sections present"
        if not incomplete
        else "; ".join(incomplete),
    )


def audit_case_outcomes() -> Audit:
    """Every finished case is PENDING with a package, or UNRESOLVED with hypotheses."""
    path = REPO_ROOT / "results" / "agent_results.json"
    if not path.exists():
        return Audit(
            "every case PENDING or UNRESOLVED",
            False,
            "results/agent_results.json missing -- the agent arm did not run",
        )

    document = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    tally: dict[str, int] = {}
    for result in document.get("results", []):
        status = result.get("status", "?")
        tally[status] = tally.get(status, 0) + 1
        case = result.get("case", "?")

        if status == "PENDING":
            approval = result.get("approval_dir")
            if not approval or not Path(approval).exists():
                problems.append(f"{case}: PENDING but no approval package")
            elif not (Path(approval) / "ROOT_CAUSE.md").exists():
                problems.append(f"{case}: approval package lacks ROOT_CAUSE.md")
        elif status == "UNRESOLVED":
            if not result.get("hypotheses"):
                problems.append(f"{case}: UNRESOLVED with no hypotheses recorded")
            if not result.get("stuck_reason"):
                problems.append(f"{case}: UNRESOLVED with no reason recorded")
        elif status not in ("NO_FLAKE", "ERROR"):
            problems.append(f"{case}: unexpected status {status!r}")

    summary = ", ".join(f"{count} {status}" for status, count in sorted(tally.items()))
    return Audit(
        "every case PENDING or UNRESOLVED",
        not problems,
        summary + (f" | PROBLEMS: {problems}" if problems else ""),
    )


def audit_corpus_untouched() -> Audit:
    """No patch may have been applied to the corpus outside the sandbox."""
    # Only the *code* under test matters here. `metadata.json` is written by
    # scripts/measure_corpus.py every time a baseline is measured -- that is
    # its documented job, done through the `recorder` service -- so flagging it
    # would make the audit fail for anyone who simply followed the reproduction
    # guide. Found exactly that way, in a clean-clone test of REPRODUCTION.md.
    try:
        changed = subprocess.run(
            ["git", "status", "--porcelain", "--", "corpus/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return Audit("corpus unmodified by any patch", False, f"git unavailable: {exc}")

    code_changes, measurement_changes = [], []
    for line in changed.splitlines():
        path = line[3:].strip().strip('"')
        (measurement_changes if path.endswith("metadata.json") else code_changes).append(path)
    dirty = "; ".join(code_changes)

    approval = REPO_ROOT / "results" / "pending_approval"
    packages = sorted(approval.glob("case_*")) if approval.exists() else []

    # A package carrying a rejection banner is a record of what the agent
    # produced, not a patch awaiting approval. Counting the two together would
    # overstate how much is ready for a human to apply.
    live, rejected = [], []
    for package in packages:
        banner = package / "REVALIDATION.md"
        if banner.exists() and "REJECTED" in banner.read_text(encoding="utf-8"):
            rejected.append(package.name)
        else:
            live.append(package.name)

    detail = f"no corpus code modified; {len(live)} awaiting approval: {live}"
    if rejected:
        detail += f"; {len(rejected)} rejected on re-validation (do not apply): {rejected}"
    if measurement_changes:
        detail += (
            f"; {len(measurement_changes)} metadata.json file(s) carry re-measured "
            "baselines, which is expected"
        )
    if dirty:
        detail = "CORPUS CODE MODIFIED: " + dirty

    return Audit("corpus unmodified by any patch", not dirty, detail)


def main() -> int:
    """Run every audit and print the results."""
    audits = [
        audit_changelog(),
        audit_decisions(),
        audit_case_outcomes(),
        audit_corpus_untouched(),
    ]
    print("=" * 78)
    print("SELF-AUDIT")
    print("=" * 78 + "\n")
    for audit in audits:
        print(audit.render() + "\n")

    failed = [a.name for a in audits if not a.passed]
    print("-" * 78)
    print(
        f"{len(audits) - len(failed)}/{len(audits)} passed"
        + (f" | FAILED: {failed}" if failed else "")
    )
    (REPO_ROOT / "results" / "self_audit.json").write_text(
        json.dumps(
            [{"name": a.name, "passed": a.passed, "evidence": a.evidence} for a in audits],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
