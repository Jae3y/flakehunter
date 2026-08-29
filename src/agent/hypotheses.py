"""Ranked root-cause hypotheses, each with a discriminating prediction.

A hypothesis is only useful if it predicts something a rival hypothesis does
not. "This is a race condition" is a guess; "this is a race condition, so
running the batch serially will eliminate it while pinning the hash seed will
not" is a hypothesis, because running the experiment can be wrong about it.

So the model is required to supply, for each candidate, an experiment from the
closed vocabulary in `experiments.py` and what that experiment would show *if
this candidate is the cause*. The orchestrator then runs the experiment and
compares.

Evidence from earlier rounds is fed back in, both to stop the model
re-proposing an eliminated candidate and to make the repetition visible when it
does -- that repetition is the stuck-loop signal the run is capped on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.harness.runner import BatchReport
from src.llm.client import GeminiClient
from src.llm.prompts import taxonomy_block

__all__ = [
    "HYPOTHESIS_SCHEMA",
    "ROUND_SCHEMA",
    "Hypothesis",
    "propose_hypotheses",
    "propose_round",
]

SYSTEM_PROMPT = """You are diagnosing a flaky test by experiment.

You cannot see the test run. You are given the source, the measured failure
rate, and the distinct ways it failed. Propose ranked candidate root causes,
each paired with an experiment that would distinguish it from the others.

A good experiment is one whose result differs depending on which candidate is
correct. An experiment that every candidate predicts the same outcome for
tells you nothing, however cheap it is to run."""

HYPOTHESIS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "description": "Candidates, most likely first. At most three.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Short stable id, e.g. H1.",
                    },
                    "root_cause_class": {"type": "string"},
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "What in the source and the failure signature "
                            "points here, in two or three sentences."
                        ),
                    },
                    "discriminating_prediction": {
                        "type": "string",
                        "description": (
                            "What would be observed if this candidate is the "
                            "cause, that would NOT be observed if a rival is."
                        ),
                    },
                },
                "required": [
                    "id",
                    "root_cause_class",
                    "reasoning",
                    "discriminating_prediction",
                ],
            },
        }
    },
    "required": ["hypotheses"],
}


@dataclass(slots=True)
class Hypothesis:
    """One candidate root cause."""

    id: str
    root_cause_class: str
    reasoning: str
    discriminating_prediction: str
    status: str = "proposed"
    evidence: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Hypothesis":
        """Build from the model's structured reply."""
        return cls(
            id=str(payload.get("id", "H?")),
            root_cause_class=str(payload.get("root_cause_class", "unknown")),
            reasoning=str(payload.get("reasoning", "")),
            discriminating_prediction=str(payload.get("discriminating_prediction", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for results and trajectory."""
        return {
            "id": self.id,
            "root_cause_class": self.root_cause_class,
            "reasoning": self.reasoning,
            "discriminating_prediction": self.discriminating_prediction,
            "status": self.status,
            "evidence": self.evidence,
        }

    def describe(self) -> str:
        """One-line description."""
        return f"{self.id} [{self.root_cause_class}] {self.reasoning[:110]}"


def _evidence_block(history: list[str]) -> str:
    """Render prior rounds' evidence for the prompt."""
    if not history:
        return "No experiments have been run yet."
    lines = [f"  {index}. {line}" for index, line in enumerate(history, start=1)]
    return "Evidence gathered so far:\n" + "\n".join(lines)


def propose_hypotheses(
    client: GeminiClient,
    case_name: str,
    project_source: str,
    confirm_report: BatchReport,
    history: list[str],
    eliminated: list[str],
    round_number: int,
) -> list[Hypothesis]:
    """Ask for ranked candidate root causes given the evidence so far.

    Args:
        client: Shared LLM client.
        case_name: For the trajectory.
        project_source: The rendered project.
        confirm_report: The CONFIRM measurement.
        history: One line per experiment already run and what it showed.
        eliminated: Root cause classes already ruled out by experiment.
        round_number: Which hypothesis round this is, from 1.

    Returns:
        Ranked hypotheses, most likely first.
    """
    ruled_out = (
        "Already eliminated by experiment, do not propose again: "
        + ", ".join(sorted(set(eliminated)))
        if eliminated
        else "Nothing has been eliminated yet."
    )

    instruction = f"""A test in {case_name} is flaky.

Measured over {confirm_report.runs} runs at {confirm_report.workers} worker(s):
  failure rate: {confirm_report.flake_rate:.1%} ({confirm_report.failures} failures)
  distinct failure signatures: {confirm_report.distinct_signatures}

{confirm_report.signature_table(limit=5)}

{taxonomy_block()}

{_evidence_block(history)}

{ruled_out}

This is hypothesis round {round_number}. Propose at most three ranked candidate
root causes. For each, give the reasoning from the source and failure
signature, and a prediction that distinguishes it from the others.

Here is the complete project.

{project_source}"""

    response = client.complete(
        agent_name="agent.hypothesize",
        instruction=instruction,
        system=SYSTEM_PROMPT,
        response_schema=HYPOTHESIS_SCHEMA,
    )
    payload = response.json()
    return [Hypothesis.from_dict(item) for item in payload.get("hypotheses", [])][:3]


# --- combined round -------------------------------------------------------
#
# Hypothesis generation and experiment design were two calls. On a 20-request
# daily budget that is a third of a case's cost spent re-stating context the
# model just produced. They are merged into one.
#
# The merge is not purely a saving. The hypothesis schema already requires a
# ``discriminating_prediction`` for each candidate, so the model was being made
# to reason about what would separate them *before* being asked, in a separate
# call, to choose the separator. Asking for both together lets the choice be
# made with the reasoning still in hand rather than reconstructed from a
# summary.

ROUND_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "hypotheses": HYPOTHESIS_SCHEMA["properties"]["hypotheses"],
        "experiment": {
            "type": "object",
            "description": (
                "The single manipulation to run now. Choose the one whose "
                "outcome differs most between the hypotheses above."
            ),
            "properties": {
                "manipulation": {"type": "string"},
                "parameter": {"type": "string"},
                "targets_hypothesis": {"type": "string"},
                "rationale": {"type": "string"},
                "predicted_effect": {
                    "type": "string",
                    "enum": ["eliminated", "reduced", "unchanged", "increased"],
                },
            },
            "required": [
                "manipulation",
                "parameter",
                "targets_hypothesis",
                "rationale",
                "predicted_effect",
            ],
        },
    },
    "required": ["hypotheses", "experiment"],
}


def propose_round(
    client: GeminiClient,
    case_name: str,
    project_source: str,
    confirm_report: BatchReport,
    history: list[str],
    eliminated: list[str],
    round_number: int,
    manipulations: dict[str, str],
    node_ids: list[str],
) -> tuple[list[Hypothesis], dict[str, Any]]:
    """Propose ranked hypotheses *and* the experiment to run, in one request.

    Args:
        client: Shared LLM client.
        case_name: For the trajectory.
        project_source: The rendered project.
        confirm_report: The CONFIRM measurement.
        history: One line per experiment already run and what it showed.
        eliminated: Root cause classes already ruled out.
        round_number: Which round this is, from 1.
        manipulations: The closed experiment vocabulary, name -> description.
        node_ids: The case's real test node ids. Supplying them stops the model
            inventing one, which makes pytest collect nothing and produces an
            experiment that looks like evidence and is not.

    Returns:
        The ranked hypotheses and the raw experiment payload.
    """
    ruled_out = (
        "Already eliminated by experiment, do not propose again: "
        + ", ".join(sorted(set(eliminated)))
        if eliminated
        else "Nothing has been eliminated yet."
    )
    catalogue = "\n".join(f"  {name}: {text}" for name, text in manipulations.items())
    tests = "\n".join(f"  {n}" for n in node_ids) or "  (unknown)"

    instruction = f"""A test in {case_name} is flaky.

Measured over {confirm_report.runs} runs at {confirm_report.workers} worker(s):
  failure rate: {confirm_report.flake_rate:.1%} ({confirm_report.failures} failures)
  distinct failure signatures: {confirm_report.distinct_signatures}

{confirm_report.signature_table(limit=5)}

{taxonomy_block()}

{_evidence_block(history)}

{ruled_out}

This is round {round_number}.

Do two things in one reply.

First, propose at most three ranked candidate root causes, each with the
reasoning from the source and failure signature, and a prediction that
distinguishes it from the others.

Second, choose the ONE manipulation to run now -- the one whose outcome differs
most between those candidates. A manipulation they all predict the same result
for is wasted.

Available manipulations:
{catalogue}

The tests in this case, by exact node id. For isolate_test or force_test_order
the parameter MUST be one of these verbatim; an id that does not exist makes
pytest collect nothing, which proves nothing:
{tests}

Here is the complete project.

{project_source}"""

    response = client.complete(
        agent_name="agent.round",
        instruction=instruction,
        system=SYSTEM_PROMPT,
        response_schema=ROUND_SCHEMA,
    )
    payload = response.json()
    hypotheses = [Hypothesis.from_dict(h) for h in payload.get("hypotheses", [])][:3]
    return hypotheses, payload.get("experiment", {})
