"""Check the four competition deliverables explicitly.

Deliberately separate from `self_audit.py`. That one checks this project's
internal process discipline -- changelog measurements, decision reasoning,
corpus cleanliness. This one checks the four things actually being submitted,
so a category can pass there and still be missing here.

    python scripts/deliverables_check.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CORE_MODULES = [
    "telemetry/tracer.py", "sandbox/executor.py", "harness/runner.py",
    "harness/validator.py", "harness/protocol.py", "baseline/one_shot.py",
    "agent/orchestrator.py", "evaluate/compare.py",
]


def main() -> int:
    """Verify each deliverable; return non-zero if anything is missing."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    repro = (REPO_ROOT / "REPRODUCTION.md").read_text(encoding="utf-8")
    changelog = (REPO_ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
    traces_index = REPO_ROOT / "traces" / "README.md"
    entries = len([l for l in changelog.splitlines() if l.startswith("## ")])

    traces = sorted((REPO_ROOT / "traces").glob("*.jsonl"))
    has_checkpoint = any(
        json.loads(line).get("human_checkpoint")
        for f in traces
        for line in f.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("{")
    )

    shots = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_video_shots.py")],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )

    rows: list[tuple[str, str, bool, str]] = [
        ("1 Solution code", "core modules present",
         all((REPO_ROOT / "src" / m).exists() for m in CORE_MODULES),
         f"{sum((REPO_ROOT / 'src' / m).exists() for m in CORE_MODULES)}/{len(CORE_MODULES)}"),
        ("1 Solution code", "Improvement Changelog findable",
         "## Improvement Changelog" in readme, f"README section + {entries} changelog entries"),
        ("1 Solution code", "Primary Failure Mode section",
         "## Primary Failure Mode" in readme, "labelled section"),
        ("1 Solution code", "Hot Take section", "## Hot Take" in readme, "labelled section"),
        ("1 Solution code", "user / bottleneck / why it matters",
         "## The user and the bottleneck" in readme and "**Why it matters.**" in readme, "explicit"),
        ("2 Reproduction", "exact commands", repro.count("```bash") >= 10,
         f"{repro.count('```bash')} command blocks"),
        ("2 Reproduction", "versions pinned", "python:3.11.9-slim-bookworm" in repro, "env table"),
        ("2 Reproduction", "runtime and request cost per phase", "Requests |" in repro, "cost table"),
        ("2 Reproduction", "tested in a clean clone",
         "This guide was tested by following it" in repro, "tested end to end"),
        ("3 Video", "shot list exists", (REPO_ROOT / "docs" / "VIDEO_OUTLINE.md").exists(), "present"),
        ("3 Video", "named artifacts verified", shots.returncode == 0,
         (shots.stdout.strip().splitlines() or ["no output"])[-1]),
        ("3 Video", "THE VIDEO ITSELF", False, "requires human filming"),
        ("4 Trajectories", "captured live", len(traces) >= 10, f"{len(traces)} JSONL files"),
        ("4 Trajectories", "indexed for a reviewer", traces_index.exists(), "traces/README.md"),
        ("4 Trajectories", "shows retries / rejections",
         traces_index.exists() and "rejection" in traces_index.read_text(encoding="utf-8").lower(),
         "validator rejections indexed"),
        ("4 Trajectories", "shows human checkpoints", has_checkpoint, "human_checkpoint records present"),
    ]

    print("=" * 74)
    print("DELIVERABLES CHECK")
    print("=" * 74)
    current = None
    for deliverable, item, ok, evidence in rows:
        if deliverable != current:
            print(f"\n{deliverable}")
            current = deliverable
        print(f"  [{'DONE' if ok else 'GAP '}] {item} — {evidence}")

    gaps = [(d, i) for d, i, ok, _ in rows if not ok]
    print("\n" + "-" * 74)
    print(f"{len(rows) - len(gaps)}/{len(rows)} complete")
    for deliverable, item in gaps:
        print(f"  GAP: {deliverable} — {item}")
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
