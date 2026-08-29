"""Baseline versus agent: the results table.

The primary metric is **residual flake rate** -- failures per 500 runs after
the fix, target zero. Everything else is secondary: root-cause classification
accuracy, masking fixes the validator caught, wall clock, and tokens.

Cost is reported in tokens rather than dollars. Published per-token rates for
`gemini-3.6-flash` are not available to this project, and a fabricated dollar
figure in a results table is worse than an honest token count -- tokens are
what was actually measured, and a rate can be applied to them later. See
`DECISIONS.md` D-007.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["ComparisonRow", "build_rows", "render_table", "render_summary"]


@dataclass(slots=True)
class ComparisonRow:
    """One case, both arms."""

    case: str
    root_cause: str
    corpus_flake_rate: float | None
    baseline_residual: float | None
    baseline_identified: bool
    baseline_tokens: int
    agent_status: str
    agent_residual: float | None
    agent_identified: bool
    agent_tokens: int
    agent_rounds: int
    validator_rejections: int

    @property
    def baseline_fixed(self) -> bool:
        """Baseline reached zero failures."""
        return self.baseline_residual == 0.0

    @property
    def agent_fixed(self) -> bool:
        """Agent reached zero failures and is awaiting approval."""
        return self.agent_residual == 0.0 and self.agent_status == "PENDING"


def _rate(value: float | None) -> str:
    """Render a rate, or a dash when it was never measured."""
    return "-" if value is None else f"{value:.2%}"


def build_rows(
    corpus: Path, baseline_path: Path, agent_path: Path
) -> tuple[list[ComparisonRow], dict[str, Any]]:
    """Join the two arms' results per case.

    Args:
        corpus: The corpus directory, for recorded baselines and root causes.
        baseline_path: ``results/baseline_results.json``.
        agent_path: ``results/agent_results.json``.

    Returns:
        The rows, and a metadata dict describing both runs.
    """
    baseline_doc = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.exists()
        else {"results": []}
    )
    agent_doc = (
        json.loads(agent_path.read_text(encoding="utf-8"))
        if agent_path.exists()
        else {"results": []}
    )
    baseline_by_case = {r["case"]: r for r in baseline_doc.get("results", [])}
    agent_by_case = {r["case"]: r for r in agent_doc.get("results", [])}

    rows: list[ComparisonRow] = []
    for case_dir in sorted(corpus.glob("case_*")):
        if case_dir.name == "case_00_smoke":
            continue
        metadata = json.loads((case_dir / "metadata.json").read_text(encoding="utf-8"))
        baseline = baseline_by_case.get(case_dir.name, {})
        agent = agent_by_case.get(case_dir.name, {})

        rejections = sum(
            1
            for validation in agent.get("validations", [])
            if not validation.get("passed", True)
        )
        rows.append(
            ComparisonRow(
                case=case_dir.name,
                root_cause=metadata.get("root_cause_class", "?"),
                corpus_flake_rate=(metadata.get("baseline") or {}).get("flake_rate"),
                baseline_residual=baseline.get("residual_flake_rate"),
                baseline_identified=bool(baseline.get("cause_identified")),
                baseline_tokens=(baseline.get("tokens") or {}).get("total", 0),
                agent_status=agent.get("status", "NOT RUN"),
                agent_residual=agent.get("residual_flake_rate"),
                agent_identified=bool(agent.get("cause_identified")),
                agent_tokens=(agent.get("tokens") or {}).get("total", 0),
                agent_rounds=agent.get("rounds", 0),
                validator_rejections=rejections,
            )
        )

    meta = {
        "baseline_model": baseline_doc.get("model"),
        "agent_model": agent_doc.get("model"),
        "baseline_usage": baseline_doc.get("usage", {}),
        "agent_usage": agent_doc.get("usage", {}),
        "trace_run_id": agent_doc.get("trace_run_id"),
    }
    return rows, meta


def render_table(rows: list[ComparisonRow]) -> str:
    """Render the results table as markdown."""
    header = (
        "| Case | Root cause | Corpus flake | Baseline after fix | "
        "Agent after fix | Cause? (B/A) | Status | Tokens (B/A) |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for row in rows:
        lines.append(
            f"| {row.case.replace('case_', '').replace('_', ' ')} "
            f"| `{row.root_cause}` "
            f"| {_rate(row.corpus_flake_rate)} "
            f"| {_rate(row.baseline_residual)} "
            f"| {_rate(row.agent_residual)} "
            f"| {'yes' if row.baseline_identified else 'no'} / "
            f"{'yes' if row.agent_identified else 'no'} "
            f"| {row.agent_status} "
            f"| {row.baseline_tokens:,} / {row.agent_tokens:,} |"
        )
    return "\n".join(lines)


def render_summary(rows: list[ComparisonRow], meta: dict[str, Any]) -> str:
    """Render the aggregate rows below the table."""
    total = len(rows)
    if not total:
        return "No cases."

    baseline_fixed = sum(1 for r in rows if r.baseline_fixed)
    agent_fixed = sum(1 for r in rows if r.agent_fixed)
    baseline_ids = sum(1 for r in rows if r.baseline_identified)
    agent_ids = sum(1 for r in rows if r.agent_identified)
    rejections = sum(r.validator_rejections for r in rows)
    pending = sum(1 for r in rows if r.agent_status == "PENDING")
    unresolved = sum(1 for r in rows if r.agent_status == "UNRESOLVED")

    measured_baseline = [r.baseline_residual for r in rows if r.baseline_residual is not None]
    measured_agent = [r.agent_residual for r in rows if r.agent_residual is not None]
    mean_baseline = sum(measured_baseline) / len(measured_baseline) if measured_baseline else None
    mean_agent = sum(measured_agent) / len(measured_agent) if measured_agent else None

    baseline_tokens = sum(r.baseline_tokens for r in rows)
    agent_tokens = sum(r.agent_tokens for r in rows)

    return f"""
| Aggregate | Baseline | Agent |
|---|---|---|
| Residual flake rate zero | {baseline_fixed}/{total} | {agent_fixed}/{total} |
| Mean residual flake rate | {_rate(mean_baseline)} | {_rate(mean_agent)} |
| Root cause identified | {baseline_ids}/{total} | {agent_ids}/{total} |
| Total tokens | {baseline_tokens:,} | {agent_tokens:,} |

Agent case outcomes: {pending} PENDING approval, {unresolved} UNRESOLVED.
Patches rejected by the anti-cheat validator and re-authored: {rejections}.

Both arms ran `{meta.get('baseline_model') or '?'}` / `{meta.get('agent_model') or '?'}`.
Trajectory: `traces/{meta.get('trace_run_id') or '?'}.jsonl`.
"""
