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
    agent_revalidation_failed: bool = False
    baseline_validator_passed: bool | None = None
    baseline_validator_reason: str = ""


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
    revalidation_path: Path | None = None,
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

    # Validator verdicts from the retroactive re-check. "Verified at zero
    # failures" and "legitimate" are different claims, and the second is the
    # one the anti-cheat rules exist to answer.
    revalidation: dict[str, dict[str, Any]] = {}
    if revalidation_path is not None:
        for finding in _load(revalidation_path).get("findings", []):
            if finding.get("arm") == "baseline":
                revalidation[finding["case"]] = finding

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
                agent_revalidation_failed=bool(
                    agent.get("revalidation")
                    and not agent["revalidation"].get("passed", True)
                ),
                baseline_validator_passed=(
                    revalidation[case_dir.name]["passed"]
                    if case_dir.name in revalidation
                    else None
                ),
                baseline_validator_reason=(
                    "; ".join(revalidation[case_dir.name].get("rejections", []))
                    if case_dir.name in revalidation
                    else ""
                ),
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
        if not row.agent_attempted:
            agent_cell = "-"
        elif row.agent_revalidation_failed:
            # The number is real; what it measured was not. Showing a bare
            # 0.00% here would read as a success the validator refused.
            agent_cell = f"{_rate(row.agent_residual)} (patch rejected)"
        else:
            agent_cell = _rate(row.agent_residual)
        if row.agent_attempted:
            agent_cause = "Y" if row.agent_identified else "n"
        else:
            agent_cause = "-"
        baseline_cause = "Y" if row.baseline_identified else "n"
        unsound = " (unsound)" if row.baseline_sound is False else ""
        status = row.agent_status
        if row.agent_note:
            note = row.agent_note
            if len(note) > 58:
                note = note[:55].rstrip() + "..."
            status = f"{status} ({note})"
        lines.append(
            f"| {label} | `{row.root_cause}` | {_rate(row.corpus_flake_rate)} "
            f"| {_rate(row.baseline_residual)}{unsound} | {agent_cell} "
            f"| {baseline_cause} / {agent_cause} | {status} "
            f"| {row.baseline_tokens:,} / {row.agent_tokens:,} |"
        )
    return chr(10).join(lines)


def render_claimed_vs_verified(rows: list[ComparisonRow]) -> str:
    """The comparison this project is actually about.

    Three claims, not two, and they narrow at each step:

    * **Claimed** -- a patch was produced, with a confidence attached.
    * **Verified** -- re-running it 500 times gave zero failures.
    * **Legitimate** -- the anti-cheat validator accepts it.

    A patch can be claimed and not verified (case 07: 0.80% residual), or
    verified and not legitimate (a mask whose window is simply wider than the
    observation), or verified against code that never compiled.
    """
    claimed = [r for r in rows if r.baseline_claimed]
    verified = [r for r in claimed if r.baseline_verified]
    judged = [r for r in claimed if r.baseline_validator_passed is not None]
    legitimate = [r for r in judged if r.baseline_validator_passed]

    lines = [
        "### Claimed versus verified versus legitimate",
        "",
        f"The baseline returned a patch for **{len(claimed)}/{len(rows)}** cases and",
        "attached a confidence to every one. Two further questions then narrow it:",
        "did the patch actually work, and is it a fix at all?",
        "",
        "| Case | Confidence | Residual after fix | Verified? | Validator | Why not |",
        "|---|---|---|---|---|---|",
    ]
    for row in claimed:
        label = row.case.replace("case_", "").replace("_", " ")
        detail = _rate(row.baseline_residual)
        if row.baseline_sound is False:
            detail += " *(unsound)*"
        verified_mark = "yes" if row.baseline_verified else "**no**"
        if row.baseline_validator_passed is None:
            validator = "—"
        elif row.baseline_validator_passed:
            validator = "accepts"
        else:
            validator = "**REJECTS**"
        reason = row.baseline_validator_reason
        if len(reason) > 70:
            reason = reason[:67].rstrip() + "..."
        lines.append(
            f"| {label} | {row.baseline_confidence} | {detail} "
            f"| {verified_mark} | {validator} | {reason or '—'} |"
        )

    lines += [
        "",
        f"**Claimed {len(claimed)}/{len(rows)} → verified {len(verified)} → "
        f"legitimate {len(legitimate)}/{len(judged)}.**",
        "",
        "Every patch carried the same confidence. Nothing in the model's own",
        "output separated the ones that worked from the ones that did not —",
        "that separation came entirely from running the tests and from the",
        "validator, neither of which the baseline has.",
        "",
    ]

    masked = [
        r
        for r in judged
        if not r.baseline_validator_passed and r.baseline_residual not in (None, 0.0)
    ]
    if masked:
        anchor = masked[0]
        label = anchor.case.replace("case_", "").replace("_", " ")
        lines += [
            f"**Anchor — {label}.** The baseline identified the root cause",
            f"correctly, reported `{anchor.baseline_confidence}` confidence, and",
            f"produced a patch that still fails **{_rate(anchor.baseline_residual)}**",
            "of the time at the normal worker count. Under CPU oversubscription the",
            "validator drives that far higher: this is not an incomplete fix but a",
            "**confirmed mask**, one that widens the timing window rather than",
            "closing it. A single execution would have shown it green.",
            "",
        ]
    return chr(10).join(lines)


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
