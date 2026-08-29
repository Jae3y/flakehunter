"""Baseline versus agent: the results table, and the claimed-vs-verified split.

The primary metric is **residual flake rate** -- failures per 500 runs after the
fix, target zero.

The secondary analysis matters more than it looks. The baseline returns a patch
and a confidence for every case, and it is right most of the time. What it
cannot do is tell which times. Separating *claimed* success from *verified*
success is what makes that visible, and it is the comparison this project is
actually about: not "who fixes more" but "who knows whether they fixed it".

Cost is reported in tokens rather than dollars. No published per-token rates
for these models were available, and a fabricated dollar figure in a results
table is worse than an honest token count -- tokens are what was measured, and
a rate can be applied to them later. See `DECISIONS.md` D-007.

Cases the agent could not run live are carried as explicit placeholders rather
than omitted. A table that silently drops what it could not measure reads as a
complete result, which it is not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ComparisonRow",
    "build_rows",
    "render_claimed_vs_verified",
    "render_summary",
    "render_table",
]

#: Agent statuses that mean "we never got to try", as distinct from "we tried
#: and did not succeed". Conflating them would credit the agent for cases it
#: never attempted, or blame it for an infrastructure limit.
NOT_ATTEMPTED = {"ERROR", "NOT RUN", "EXCLUDED"}


@dataclass(slots=True)
class ComparisonRow:
    """One case, both arms."""

    case: str
    root_cause: str
    corpus_flake_rate: float | None
    baseline_residual: float | None
    baseline_sound: bool | None
    baseline_identified: bool
    baseline_confidence: str
    baseline_produced_patch: bool
    baseline_tokens: int
    agent_status: str
    agent_residual: float | None
    agent_identified: bool
    agent_tokens: int
    agent_rounds: int
    agent_experiments: int
    validator_rejections: int
    agent_note: str

    @property
    def baseline_verified(self) -> bool:
        """Baseline reached zero failures in a *sound* verification."""
        return self.baseline_residual == 0.0 and bool(self.baseline_sound)

    @property
    def baseline_claimed(self) -> bool:
        """Baseline returned a patch, i.e. asserted it had fixed the case."""
        return self.baseline_produced_patch

    @property
    def agent_verified(self) -> bool:
        """Agent reached zero failures and is awaiting approval."""
        return self.agent_residual == 0.0 and self.agent_status == "PENDING"

    @property
    def agent_attempted(self) -> bool:
        """Whether the agent actually got to run this case."""
        return self.agent_status not in NOT_ATTEMPTED


def _rate(value: float | None) -> str:
    """Render a rate, or a dash when it was never measured."""
    return "—" if value is None else f"{value:.2%}"


def _load(path: Path) -> dict[str, Any]:
    """Read a results document, tolerating absence."""
    if not path.exists():
        return {"results": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"results": []}


def build_rows(
    corpus: Path,
    baseline_path: Path,
    agent_paths: list[Path],
    excluded: set[str] | None = None,
) -> tuple[list[ComparisonRow], dict[str, Any]]:
    """Join both arms per case.

    Args:
        corpus: Corpus directory, for recorded baselines and root causes.
        baseline_path: ``results/baseline_results.json``.
        agent_paths: Agent result files. Several exist because quota forced the
            agent arm across more than one model; each row records which.
        excluded: Cases deliberately left out of both arms.

    Returns:
        The rows, and metadata describing both runs.
    """
    baseline_doc = _load(baseline_path)
    baseline_by_case = {r["case"]: r for r in baseline_doc.get("results", [])}

    agent_by_case: dict[str, dict[str, Any]] = {}
    agent_models: dict[str, str] = {}
    for path in agent_paths:
        document = _load(path)
        model = document.get("model", "?")
        for result in document.get("results", []):
            # A later file wins only if it actually attempted the case.
            existing = agent_by_case.get(result["case"])
            if existing is None or (
                existing.get("status") in NOT_ATTEMPTED
                and result.get("status") not in NOT_ATTEMPTED
            ):
                agent_by_case[result["case"]] = result
                agent_models[result["case"]] = model

    excluded = excluded or set()
    rows: list[ComparisonRow] = []
    for case_dir in sorted(corpus.glob("case_*")):
        if case_dir.name == "case_00_smoke":
            continue
        metadata = json.loads((case_dir / "metadata.json").read_text(encoding="utf-8"))
        baseline = baseline_by_case.get(case_dir.name, {})
        agent = agent_by_case.get(case_dir.name, {})
        verification = baseline.get("verification") or {}

        status = agent.get("status")
        if case_dir.name in excluded:
            status = "EXCLUDED"
        elif status is None:
            status = "NOT RUN"

        note = agent.get("stuck_reason") or ""
        if agent.get("error"):
            note = "quota" if "quota" in agent["error"].lower() else agent["error"][:60]
        elif status == "EXCLUDED":
            note = "runtime cost, see DECISIONS D-012"
        elif status == "NOT RUN":
            note = "quota"

        rows.append(
            ComparisonRow(
                case=case_dir.name,
                root_cause=metadata.get("root_cause_class", "?"),
                corpus_flake_rate=(metadata.get("baseline") or {}).get("flake_rate"),
                baseline_residual=baseline.get("residual_flake_rate"),
                baseline_sound=verification.get("is_sound"),
                baseline_identified=bool(baseline.get("cause_identified")),
                baseline_confidence=baseline.get("confidence", "—"),
                baseline_produced_patch=bool(baseline.get("patch")),
                baseline_tokens=(baseline.get("tokens") or {}).get("total", 0),
                agent_status=status,
                agent_residual=agent.get("residual_flake_rate"),
                agent_identified=bool(agent.get("cause_identified")),
                agent_tokens=(agent.get("tokens") or {}).get("total", 0),
                agent_rounds=agent.get("rounds", 0),
                agent_experiments=len(agent.get("experiments", [])),
                validator_rejections=sum(
                    1 for v in agent.get("validations", []) if not v.get("passed", True)
                ),
                agent_note=note,
            )
        )

    meta = {
        "baseline_model": baseline_doc.get("model"),
        "agent_models": agent_models,
        "baseline_usage": baseline_doc.get("usage", {}),
    }
    return rows, meta


def render_table(rows: list[ComparisonRow]) -> str:
    """The main results table."""
    header = (
        "| Case | Root cause | Corpus flake | Baseline after fix "
        "| Agent after fix | Cause? B/A | Agent status | Tokens B/A |"
    )
    lines = [header, "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        label = row.case.replace("case_", "").replace("_", " ")
        agent_cell = _rate(row.agent_residual) if row.agent_attempted else "-"
        if row.agent_attempted:
            agent_cause = "Y" if row.agent_identified else "n"
        else:
            agent_cause = "-"
        baseline_cause = "Y" if row.baseline_identified else "n"
        unsound = " (unsound)" if row.baseline_sound is False else ""
        status = row.agent_status
        if row.agent_note:
            status = f"{status} ({row.agent_note})"
        lines.append(
            f"| {label} | `{row.root_cause}` | {_rate(row.corpus_flake_rate)} "
            f"| {_rate(row.baseline_residual)}{unsound} | {agent_cell} "
            f"| {baseline_cause} / {agent_cause} | {status} "
            f"| {row.baseline_tokens:,} / {row.agent_tokens:,} |"
        )
    return chr(10).join(lines)


def render_claimed_vs_verified(rows: list[ComparisonRow]) -> str:
    """The comparison the project is actually about.

    The baseline asserts a fix for every case and is usually right. Verification
    is what separates the cases where it was right from the ones where it was
    not -- and the baseline, by construction, cannot run it.
    """
    claimed = [r for r in rows if r.baseline_claimed]
    verified = [r for r in claimed if r.baseline_verified]
    unverified = [r for r in claimed if not r.baseline_verified]

    lines = [
        "### Claimed versus verified",
        "",
        f"The baseline returned a patch for **{len(claimed)}/{len(rows)}** cases —",
        "it asserts a fix every time, and reports a confidence with it.",
        f"Re-running each patch 500 times shows **{len(verified)}** of those",
        f"{len(claimed)} actually reached zero failures.",
        "",
        "| Case | Baseline confidence | Residual after its fix | Actually fixed? |",
        "|---|---|---|---|",
    ]
    for row in claimed:
        mark = "yes" if row.baseline_verified else "**no**"
        detail = _rate(row.baseline_residual)
        if row.baseline_sound is False:
            detail += " (unsound — every run errored)"
        lines.append(
            f"| {row.case.replace('case_', '').replace('_', ' ')} "
            f"| {row.baseline_confidence} | {detail} | {mark} |"
        )

    lines += [
        "",
        f"**{len(unverified)} of {len(claimed)} confident fixes were not fixes.**",
        "The baseline had no way to tell which. Every one of them was returned",
        "with the same kind of confidence as the ones that worked, because a",
        "system that never executes the test has nothing to distinguish them by.",
        "",
    ]

    anchor = next(
        (
            r
            for r in unverified
            if r.baseline_residual not in (None, 0.0)
        ),
        None,
    )
    if anchor is not None:
        lines += [
            f"**Anchor case — {anchor.case.replace('case_', '').replace('_', ' ')}.** "
            f"The baseline identified the root cause correctly, reported "
            f"`{anchor.baseline_confidence}` confidence, and produced a patch that "
            f"still fails **{_rate(anchor.baseline_residual)}** of the time. That is "
            "a handful of failures in 500 runs — invisible to one execution, and "
            "exactly the kind of residual flakiness that gets a test re-run rather "
            "than fixed. The agent reached "
            f"{'the same case and declined to declare success' if anchor.agent_status == 'UNRESOLVED' else 'it'}"
            ", which is the correct answer where a false green is the failure mode.",
            "",
        ]
    return "\n".join(lines)


def render_summary(rows: list[ComparisonRow], meta: dict[str, Any]) -> str:
    """Aggregate rows below the table."""
    total = len(rows)
    attempted = [r for r in rows if r.agent_attempted]
    baseline_verified = sum(1 for r in rows if r.baseline_verified)
    agent_verified = sum(1 for r in rows if r.agent_verified)
    baseline_ids = sum(1 for r in rows if r.baseline_identified)
    agent_ids = sum(1 for r in attempted if r.agent_identified)
    rejections = sum(r.validator_rejections for r in rows)

    pending = sum(1 for r in rows if r.agent_status == "PENDING")
    unresolved = sum(1 for r in rows if r.agent_status == "UNRESOLVED")
    blocked = sum(1 for r in rows if r.agent_status in ("ERROR", "NOT RUN"))
    excluded = sum(1 for r in rows if r.agent_status == "EXCLUDED")

    baseline_tokens = sum(r.baseline_tokens for r in rows)
    agent_tokens = sum(r.agent_tokens for r in rows)

    models = ", ".join(sorted(set(meta.get("agent_models", {}).values()))) or "—"

    return f"""
### Aggregates

| Metric | Baseline | Agent |
|---|---|---|
| Cases attempted | {total}/{total} | {len(attempted)}/{total} |
| Residual flake rate zero (verified) | {baseline_verified}/{total} | {agent_verified}/{len(attempted) or 1} of attempted |
| Root cause identified | {baseline_ids}/{total} | {agent_ids}/{len(attempted) or 1} of attempted |
| Total tokens | {baseline_tokens:,} | {agent_tokens:,} |

Agent outcomes: **{pending} PENDING** approval, **{unresolved} UNRESOLVED**,
{blocked} blocked by API quota, {excluded} excluded for runtime.
Patches rejected by the anti-cheat validator and re-authored: **{rejections}**.

Baseline model `{meta.get('baseline_model') or '?'}`; agent model(s) `{models}`.
Where these differ, the two arms are **not** directly comparable on those rows —
see `DECISIONS.md` D-013.
"""
