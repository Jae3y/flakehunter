"""Check that every artifact the video outline names exists and still says so.

Numbers move when anything is re-measured, and a shot list that promises a
figure the file no longer contains wastes a filming session. Run this
immediately before recording.

    python scripts/verify_video_shots.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """Verify each shot's artifact; return non-zero if any is missing."""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, ok, detail))

    results = REPO_ROOT / "results" / "RESULTS.md"
    text = results.read_text(encoding="utf-8") if results.exists() else ""
    check("1 claimed/verified/legitimate table", "Claimed 12/12" in text,
          "present" if "Claimed 12/12" in text else "MISSING")

    baseline = json.loads((REPO_ROOT / "results" / "baseline_results.json").read_text(encoding="utf-8"))
    patch = next((r["patch"] for r in baseline["results"]
                  if r["case"] == "case_01_race_condition"), None)
    source = patch["files"][0]["new_content"] if patch else ""
    check("2 case 01 syntax error verbatim", "def __init__( -> None:" in source,
          "found" if "def __init__( -> None:" in source else "MISSING")

    reval = REPO_ROOT / "results" / "revalidation.json"
    stress = {}
    if reval.exists():
        finding = next((f for f in json.loads(reval.read_text(encoding="utf-8"))["findings"]
                        if f["arm"] == "baseline" and f["case"] == "case_07_network_timeout"), None)
        stress = (finding or {}).get("stress") or {}
    check("3 case 07 stress failures at 32 workers",
          bool(stress) and stress.get("failures", 0) > 0,
          f"{stress.get('failures')}/{stress.get('runs')} at {stress.get('workers')} workers")

    demo = REPO_ROOT / "results" / "case12_masking_demo.json"
    variants = json.loads(demo.read_text(encoding="utf-8")).get("results", {}) if demo.exists() else {}
    check("4 masking demo variants", len(variants) >= 3, f"{sorted(variants)}")

    rejection = (REPO_ROOT / "results" / "archive" / "rejected_patches"
                 / "case_07_attempt1_rejected" / "REVALIDATION.md")
    rtext = rejection.read_text(encoding="utf-8") if rejection.exists() else ""
    check("5 rejection record carries the diff",
          "SERVICE_WORK_S" in rtext and "```diff" in rtext,
          "diff present" if "```diff" in rtext else "MISSING")

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    check("7 README self-flake section",
          "found a flaky test in itself" in readme and "13%" in readme, "present")

    approval = REPO_ROOT / "results" / "pending_approval" / "case_12_masking_trap" / "ROOT_CAUSE.md"
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "corpus/"],
                           cwd=REPO_ROOT, capture_output=True, text=True).stdout
    code_changes = [l for l in dirty.splitlines() if not l.strip().endswith("metadata.json")]
    check("8 approval package + no corpus code modified",
          approval.exists() and not code_changes,
          f"package={approval.exists()} code_changes={len(code_changes)}")

    check("10 trajectory index", (REPO_ROOT / "traces" / "README.md").exists(),
          "traces/README.md")

    outline = (REPO_ROOT / "docs" / "VIDEO_OUTLINE.md").read_text(encoding="utf-8")
    referenced = set(re.findall(r"`([A-Za-z0-9_./-]+\.(?:md|json|py|jsonl))`", outline))
    missing = sorted(p for p in referenced if not (REPO_ROOT / p).exists())
    check("every path named in the outline exists", not missing, f"missing={missing or 'none'}")

    print("=" * 74)
    print("VIDEO SHOT-LIST VERIFICATION")
    print("=" * 74 + "\n")
    for name, ok, detail in checks:
        print(f"  [{'OK  ' if ok else 'GAP '}] {name}: {detail}")
    failed = [n for n, ok, _ in checks if not ok]
    print("\n" + "-" * 74)
    print(f"{len(checks) - len(failed)}/{len(checks)} verified"
          + (f" | GAPS: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
