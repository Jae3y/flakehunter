"""Emit the baseline-vs-agent results table.

    docker compose run --rm flakehunter python scripts/run_compare.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluate.compare import build_rows, render_summary, render_table


def main() -> int:
    """Build the table from whatever results exist and write it out."""
    results = REPO_ROOT / "results"
    rows, meta = build_rows(
        REPO_ROOT / "corpus",
        results / "baseline_results.json",
        results / "agent_results.json",
    )
    table = render_table(rows)
    summary = render_summary(rows, meta)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    document = f"# Results\n\nGenerated {stamp}.\n\n{table}\n{summary}\n"
    (results / "RESULTS.md").write_text(document, encoding="utf-8")
    print(document)
    print("wrote results/RESULTS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
