"""Author a minimal fix for a confirmed root cause, and re-author on rejection.

The patcher gets more than the baseline does, and the difference is entirely
evidence: the confirmed root cause, the experiments that confirmed it, and --
when a previous attempt was rejected -- exactly why. It returns the same
structured patch the baseline returns, so the two arms' outputs stay directly
comparable.

Rejection feedback is the point of the VALIDATE step existing at all. A
validator that only said "no" would waste the round; one that says "you added a
sleep, the failure came back at 32 workers, the race is still there" gives the
next attempt something to act on. Those rejections are recorded in the
trajectory because they are evidence of the loop working, not of it failing.
"""

from __future__ import annotations

from typing import Any

from src.llm.client import GeminiClient
from src.llm.prompts import FIX_RULES, PATCH_SCHEMA

__all__ = ["author_patch"]

SYSTEM_PROMPT = """You are an experienced Python engineer fixing a flaky test
whose root cause has already been established by experiment.

Write the minimal change that removes the nondeterminism. You have evidence,
not a guess -- trust it, and fix the cause it identifies rather than
re-diagnosing from scratch."""


def _rejection_block(rejections: list[str], attempt: int) -> str:
    """Render prior rejection reasons for the prompt."""
    if not rejections:
        return ""
    lines = "\n".join(f"  - {reason}" for reason in rejections)
    return f"""
A previous attempt at this fix was REJECTED. This is attempt {attempt}.

Reasons it was rejected:
{lines}

Address these directly. In particular, if the fix was rejected for failing
under load, that means the change widened a timing window rather than closing
it -- the nondeterminism is still present and needs removing at its source.
"""


def author_patch(
    client: GeminiClient,
    case_name: str,
    project_source: str,
    root_cause_class: str,
    root_cause_reasoning: str,
    evidence: list[str],
    rejections: list[str] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Write a fix for the confirmed root cause.

    Args:
        client: Shared LLM client.
        case_name: For the trajectory.
        project_source: The rendered project.
        root_cause_class: The confirmed class from the taxonomy.
        root_cause_reasoning: Why that class was concluded.
        evidence: One line per experiment run and what it showed.
        rejections: Reasons a previous attempt was rejected, if any.
        attempt: Which patch attempt this is, from 1.

    Returns:
        The structured patch.
    """
    evidence_lines = "\n".join(f"  - {line}" for line in evidence) or "  (none)"

    instruction = f"""The flaky test in {case_name} has been diagnosed by experiment.

Confirmed root cause class: {root_cause_class}

Why: {root_cause_reasoning}

Experimental evidence:
{evidence_lines}
{_rejection_block(rejections or [], attempt)}
{FIX_RULES}

Write the minimal fix. Return the complete new contents of every file you
change, and set root_cause_class to the confirmed class above.

Here is the complete project.

{project_source}"""

    response = client.complete(
        agent_name="agent.patch",
        instruction=instruction,
        system=SYSTEM_PROMPT,
        response_schema=PATCH_SCHEMA,
    )
    return response.json()
