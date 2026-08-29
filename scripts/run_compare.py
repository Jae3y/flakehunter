"""Emit the baseline-vs-agent results table.

    docker compose run --rm flakehunter python scripts/run_compare.py

Reads every agent results file, because API quota forced the agent arm across
more than one model. Rows carry which model produced them, and rows the agent
never reached are shown as placeholders rather than dropped.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluate.compare import (  # noqa: E402
    build_rows,
    render_claimed_vs_verified,
    render_summary,
    render_table,
)

#: Cases deliberately left out of both arms, so neither is credited or blamed.
EXCLUDED = {"case_01_race_condition"}


def main() -> int:
    """Build the table from whatever results exist and write it out."""
    results = REPO_ROOT / "results"
    agent_files = sorted(results.glob("agent_results*.json"))
    rows, meta = build_rows(
        REPO_ROOT / "corpus",
        results / "baseline_results.json",
        agent_files,
        excluded=EXCLUDED,
    )

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    document = f"""# Results

Generated {stamp}.

Primary metric: **residual flake rate** — failures per 500 runs after the fix,
target zero. Cost is in tokens, not dollars (`DECISIONS.md` D-007).

## Results table

{render_table(rows)}

`(unsound)` marks a verification in which runs errored rather than ran, so its
zero means nothing. Agent columns show `-` where the case was never attempted.

{render_summary(rows, meta)}

## The comparison that matters

{render_claimed_vs_verified(rows)}
"""
    (results / "RESULTS.md").write_text(document, encoding="utf-8")
    print(document)
    print("wrote results/RESULTS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
