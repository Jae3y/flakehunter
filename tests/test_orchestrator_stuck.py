"""The stuck-loop detector, exercised against a scripted model.

Five rounds is an arbitrary bound that stops a loop being infinite. Repeating a
hypothesis with no new discriminating evidence is a *signal*: it means the
experiments are not separating the candidates, so more rounds would produce
more of the same. The second is the one that says the agent is stuck rather
than merely slow, and it is the one worth testing.

No API calls. The model is replaced by a scripted stand-in that always names
the same root cause and always designs an experiment that cannot confirm it,
which is exactly the pathology the detector exists to catch. The sandbox,
harness and corpus are real, so the loop under test is the real one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from src.agent.orchestrator import AgentConfig, run_agent_case
from src.harness.runner import TestRunner
from src.sandbox.executor import SandboxExecutor
from src.telemetry.tracer import Tracer

CORPUS = Path(__file__).resolve().parent.parent / "corpus"

#: An RNG-driven case: fast, reliably flaky, and unaffected by the timezone,
#: which is what lets the scripted experiment fail to confirm anything.
CASE = CORPUS / "case_06_unseeded_randomness"


@dataclass(slots=True)
class ScriptedResponse:
    """Stands in for an LLMResponse."""

    payload: dict[str, Any]
    prompt_tokens: int = 10
    completion_tokens: int = 20
    thinking_tokens: int = 0

    @property
    def billed_output_tokens(self) -> int:
        return self.completion_tokens + self.thinking_tokens

    def json(self) -> Any:
        return self.payload


class ScriptedClient:
    """A model that never learns: same hypothesis, every round.

    Mirrors the surface of ``GeminiClient`` that the orchestrator actually
    touches. Every call is still written to a real tracer, so the trajectory
    of a stuck run is inspectable in the same way a live one is.
    """

    def __init__(self, tracer: Tracer, repeated_class: str = "race_condition") -> None:
        self.tracer = tracer
        self.model = "scripted/stuck-model"
        self.repeated_class = repeated_class
        self.total_prompt_tokens = 0
        self.total_output_tokens = 0
        self.calls_by_agent: dict[str, int] = {}

    def complete(self, *, agent_name: str, instruction: str, **_: Any) -> ScriptedResponse:
        """Return the scripted reply for whichever phase is asking."""
        self.calls_by_agent[agent_name] = self.calls_by_agent.get(agent_name, 0) + 1
        payload = self._payload_for(agent_name)

        with self.tracer.turn(agent_name, self.model, instruction[:200]) as turn:
            turn.call("scripted.complete", agent=agent_name)
            turn.respond(stdout=json.dumps(payload)[:400], exit_code=0)
            turn.reflect(f"scripted reply #{self.calls_by_agent[agent_name]}")

        self.total_prompt_tokens += 10
        self.total_output_tokens += 20
        return ScriptedResponse(payload)

    def _payload_for(self, agent_name: str) -> dict[str, Any]:
        if agent_name == "agent.hypothesize":
            # The same top candidate every round. A model that has stopped
            # updating on evidence looks exactly like this.
            return {
                "hypotheses": [
                    {
                        "id": "H1",
                        "root_cause_class": self.repeated_class,
                        "reasoning": "Threads mutate shared state without a lock.",
                        "discriminating_prediction": "Serialising would eliminate it.",
                    }
                ]
            }
        if agent_name == "agent.experiment.design":
            # Pinning the timezone cannot affect an RNG-driven failure, so this
            # experiment can never confirm the hypothesis it claims to target.
            return {
                "manipulation": "pin_timezone",
                "parameter": "UTC",
                "targets_hypothesis": "H1",
                "rationale": "Scripted: deliberately non-discriminating.",
                "predicted_effect": "eliminated",
            }
        raise AssertionError(f"the loop should not reach {agent_name} when stuck")


@pytest.fixture()
def scripted_run(tmp_path: Path):
    """A real executor and harness, with a scripted model."""
    tracer = Tracer(trace_dir=tmp_path / "traces", run_id="stuck-test")
    executor = SandboxExecutor(tracer, scratch_root=tmp_path / "scratch", trace_each_run=False)
    runner = TestRunner(executor, tracer)
    client = ScriptedClient(tracer)
    config = AgentConfig(
        confirm_runs=60,
        experiment_runs=40,
        verify_runs=40,
        stress_runs=40,
        workers=8,
        max_rounds=5,
        scratch_root=tmp_path / "agent",
    )
    return client, executor, runner, config, tracer, tmp_path


def test_repeated_hypothesis_stops_the_loop(scripted_run) -> None:
    """A hypothesis repeated with no new evidence ends the case as UNRESOLVED."""
    client, executor, runner, config, tracer, tmp_path = scripted_run

    outcome = run_agent_case(
        CASE, client, executor, runner, config, tmp_path / "approval", tracer.run_id
    )

    assert outcome.status == "UNRESOLVED"
    assert outcome.stuck_reason
    assert "race_condition" in outcome.stuck_reason
    assert "no new discriminating evidence" in outcome.stuck_reason


def test_it_stops_before_the_round_cap(scripted_run) -> None:
    """The signal fires on its own, not by exhausting the arbitrary bound.

    If the detector only ever tripped at ``max_rounds`` it would be
    indistinguishable from the round cap and would tell nobody anything.
    """
    client, executor, runner, config, tracer, tmp_path = scripted_run

    outcome = run_agent_case(
        CASE, client, executor, runner, config, tmp_path / "approval", tracer.run_id
    )

    assert outcome.rounds < config.max_rounds
    assert "exhausted" not in (outcome.stuck_reason or "")


def test_the_attempted_hypotheses_are_recorded(scripted_run) -> None:
    """An unresolved case must carry what it tried, or it is not a report."""
    client, executor, runner, config, tracer, tmp_path = scripted_run

    outcome = run_agent_case(
        CASE, client, executor, runner, config, tmp_path / "approval", tracer.run_id
    )

    assert outcome.hypotheses, "no hypotheses recorded"
    assert outcome.experiments, "no experiments recorded"
    proposed = {
        item["root_cause_class"]
        for entry in outcome.hypotheses
        for item in entry["items"]
    }
    assert proposed == {"race_condition"}
    for experiment in outcome.experiments:
        assert experiment["matches_prediction"] is False


def test_no_patch_is_produced_when_stuck(scripted_run) -> None:
    """Nothing unverified may reach the approval directory."""
    client, executor, runner, config, tracer, tmp_path = scripted_run

    outcome = run_agent_case(
        CASE, client, executor, runner, config, tmp_path / "approval", tracer.run_id
    )

    assert outcome.patch is None
    assert outcome.approval_dir is None
    assert outcome.verify_report is None
    assert "agent.patch" not in client.calls_by_agent


def test_the_stuck_run_leaves_a_readable_trajectory(scripted_run) -> None:
    """A stuck run is evidence, so its turns must still be on disk."""
    client, executor, runner, config, tracer, tmp_path = scripted_run

    run_agent_case(
        CASE, client, executor, runner, config, tmp_path / "approval", tracer.run_id
    )

    records = [
        json.loads(line)
        for line in tracer.path.read_text(encoding="utf-8").strip().splitlines()
    ]
    agents = {record["agent_name"] for record in records}
    assert "agent.hypothesize" in agents
    assert "agent.experiment.design" in agents
    assert [r["turn_id"] for r in records] == list(range(len(records)))
