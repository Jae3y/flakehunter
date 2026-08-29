"""Experiments that discriminate between candidate root causes.

An experiment is a cheap manipulation that a hypothesis makes a *different*
prediction about than its rivals. Pinning the hash seed should eliminate a
set-iteration flake and do nothing to a thread race; running the tests one at
a time should eliminate an order dependency and leave a clock dependence
untouched. Running the manipulation and comparing what happened to what was
predicted is what turns a guess into a diagnosis.

The vocabulary is a closed set rather than free-form. Three reasons:

* **Safety.** The model chooses from named manipulations with typed
  parameters, so nothing model-authored reaches a shell, an arbitrary
  environment variable, or a path outside the case.
* **Interpretability.** A trajectory that says ``pin_hash_seed(0)`` is
  evidence. One that says "ran some code" is not.
* **Honesty about coverage.** A closed vocabulary makes it visible when a case
  has no discriminating experiment available, instead of letting the agent
  invent one that does not actually discriminate.

None of these manipulations edit the case. They change how it is *run*, which
is what keeps the observed flake rate attributable to the code rather than to
the patch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.harness.runner import BatchReport, TestRunner

__all__ = [
    "EXPERIMENT_SCHEMA",
    "Experiment",
    "ExperimentOutcome",
    "MANIPULATIONS",
    "run_experiment",
]

#: The closed vocabulary. Each entry documents what turning that knob means,
#: which is the text the model is given when choosing.
MANIPULATIONS: dict[str, str] = {
    "pin_hash_seed": (
        "Fix PYTHONHASHSEED to a constant. Eliminates flakiness caused by "
        "set or dict iteration order, and by anything keyed on identity or "
        "string hashing. Leaves thread races and clock dependence untouched."
    ),
    "pin_timezone": (
        "Fix TZ to a constant. Eliminates timezone-dependent flakiness. Does "
        "not affect flakiness driven by the passage of time."
    ),
    "serialize_execution": (
        "Run the batch one at a time instead of concurrently. Removes CPU "
        "contention between runs. Usually reduces timing-race flakiness "
        "sharply; leaves hash order, RNG and order dependence unchanged."
    ),
    "amplify_contention": (
        "Oversubscribe the CPU. Widens every timing window. Increases "
        "timing-race flakiness; leaves hash order, RNG and order dependence "
        "unchanged."
    ),
    "isolate_test": (
        "Run a single test on its own. If the failure disappears, the test "
        "depends on state left behind by another test."
    ),
    "force_test_order": (
        "Run named tests in a fixed order. Distinguishes an order dependency "
        "from other causes, and identifies which ordering is the bad one."
    ),
    "repeat_baseline": (
        "Re-run unchanged, as a control. Establishes the comparison point and "
        "confirms the flake rate is stable enough to reason about."
    ),
}

#: Environment variables an experiment may set. Nothing else is permitted.
ALLOWED_ENV = {"PYTHONHASHSEED", "TZ"}

#: Schema the model fills in when designing an experiment.
EXPERIMENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "manipulation": {"type": "string", "enum": list(MANIPULATIONS)},
        "parameter": {
            "type": "string",
            "description": (
                "The manipulation's argument. Hash seed value for "
                "pin_hash_seed, timezone name for pin_timezone, oversubscribe "
                "factor for amplify_contention, test node id for isolate_test, "
                "comma-separated node ids for force_test_order. Empty otherwise."
            ),
        },
        "targets_hypothesis": {
            "type": "string",
            "description": "The id of the hypothesis this experiment tests.",
        },
        "rationale": {
            "type": "string",
            "description": "Why this manipulation separates the top hypotheses.",
        },
        "predicted_effect": {
            "type": "string",
            "enum": ["eliminated", "reduced", "unchanged", "increased"],
            "description": (
                "What happens to the flake rate IF the targeted hypothesis is "
                "correct. Choose a manipulation whose prediction differs from "
                "what the rival hypotheses would predict."
            ),
        },
    },
    "required": [
        "manipulation",
        "parameter",
        "targets_hypothesis",
        "rationale",
        "predicted_effect",
    ],
}


def discover_node_ids(project: Path) -> list[str]:
    """List the test node ids a case actually defines.

    Given to the model when it designs an experiment. Without this it invents
    plausible-looking names -- a live run produced
    ``isolate_test(test_network_timeout)`` against a case whose only test is
    ``test_status_is_fetched_from_a_healthy_service`` -- and an invented node id
    makes pytest collect nothing, which used to read as "the failure was
    eliminated".
    """
    node_ids: list[str] = []
    for path in sorted(project.rglob("test_*.py")):
        relative = path.relative_to(project)
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("def test_"):
                name = stripped[len("def ") :].split("(", 1)[0]
                node_ids.append(f"{relative}::{name}")
    return node_ids


@dataclass(slots=True)
class Experiment:
    """A designed manipulation, before it has been run."""

    manipulation: str
    parameter: str
    targets_hypothesis: str
    rationale: str
    predicted_effect: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Experiment":
        """Build from the model's structured reply."""
        return cls(
            manipulation=str(payload.get("manipulation", "repeat_baseline")),
            parameter=str(payload.get("parameter", "")).strip(),
            targets_hypothesis=str(payload.get("targets_hypothesis", "")),
            rationale=str(payload.get("rationale", "")),
            predicted_effect=str(payload.get("predicted_effect", "unchanged")),
        )

    def describe(self) -> str:
        """One-line human-readable description."""
        argument = f"({self.parameter})" if self.parameter else "()"
        return f"{self.manipulation}{argument} -> expects {self.predicted_effect}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise for results and trajectory."""
        return {
            "manipulation": self.manipulation,
            "parameter": self.parameter,
            "targets_hypothesis": self.targets_hypothesis,
            "rationale": self.rationale,
            "predicted_effect": self.predicted_effect,
        }


@dataclass(slots=True)
class ExperimentOutcome:
    """What an experiment actually produced, against what it predicted."""

    experiment: Experiment
    baseline_rate: float
    observed_rate: float
    report: BatchReport
    actual_effect: str
    matches_prediction: bool
    unsupported: bool = False
    note: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """One-line summary for the trajectory reflection."""
        if self.actual_effect == "invalid":
            return (
                f"{self.experiment.describe()}; INVALID -- {self.note} "
                f"=> no evidence for or against "
                f"{self.experiment.targets_hypothesis}"
            )
        verdict = "CONFIRMS" if self.matches_prediction else "ELIMINATES"
        return (
            f"{self.experiment.describe()}; observed {self.actual_effect} "
            f"({self.baseline_rate:.1%} -> {self.observed_rate:.1%}) "
            f"=> {verdict} hypothesis {self.experiment.targets_hypothesis}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for results and trajectory."""
        return {
            "experiment": self.experiment.to_dict(),
            "baseline_rate": round(self.baseline_rate, 4),
            "observed_rate": round(self.observed_rate, 4),
            "actual_effect": self.actual_effect,
            "matches_prediction": self.matches_prediction,
            "unsupported": self.unsupported,
            "note": self.note,
            "verification": self.report.to_dict(),
        }


def classify_effect(baseline: float, observed: float) -> str:
    """Describe how the flake rate moved.

    The bands are deliberately coarse. A flake rate measured over a few hundred
    runs carries real sampling error, and timing-sensitive cases carry machine
    -state variation on top of that, so fine distinctions here would be
    reading noise as signal.
    """
    if observed <= 0.005:
        return "eliminated"
    if baseline <= 0.0:
        return "increased" if observed > 0.005 else "unchanged"
    ratio = observed / baseline
    if ratio <= 0.4:
        return "reduced"
    if ratio >= 1.75:
        return "increased"
    return "unchanged"


def _resolve(experiment: Experiment, workers: int) -> tuple[dict[str, str], list[str], int, str]:
    """Turn a manipulation into env overrides, pytest args and a worker count.

    Returns:
        ``(env, pytest_args, workers, note)``. ``note`` is non-empty when the
        manipulation could not be honoured as written.
    """
    env: dict[str, str] = {}
    args: list[str] = []
    note = ""

    match experiment.manipulation:
        case "pin_hash_seed":
            value = experiment.parameter or "0"
            env["PYTHONHASHSEED"] = value if value.isdigit() else "0"
        case "pin_timezone":
            env["TZ"] = experiment.parameter or "UTC"
        case "serialize_execution":
            workers = 1
        case "amplify_contention":
            try:
                factor = max(2, min(int(experiment.parameter or "4"), 8))
            except ValueError:
                factor = 4
            workers = workers * factor
        case "isolate_test":
            if experiment.parameter:
                args = [experiment.parameter]
            else:
                note = "isolate_test needs a test node id; ran the full suite"
        case "force_test_order":
            ids = [p.strip() for p in experiment.parameter.split(",") if p.strip()]
            if ids:
                args = ids
            else:
                note = "force_test_order needs node ids; ran the full suite"
        case "repeat_baseline":
            pass
        case _:
            note = f"unknown manipulation {experiment.manipulation!r}; ran unchanged"

    unsupported = {k: v for k, v in env.items() if k not in ALLOWED_ENV}
    if unsupported:  # pragma: no cover - defensive; the schema constrains this
        for key in unsupported:
            env.pop(key)
        note = f"dropped disallowed env {sorted(unsupported)}"
    return env, args, workers, note


def run_experiment(
    experiment: Experiment,
    project: Path,
    runner: TestRunner,
    baseline_rate: float,
    runs: int,
    workers: int,
    case_name: str,
) -> ExperimentOutcome:
    """Execute one experiment in the sandbox and judge it against its prediction.

    Args:
        experiment: The designed manipulation.
        project: The pristine case project. Never modified.
        runner: Repeat-execution harness.
        baseline_rate: The rate the manipulation is compared against.
        runs: Runs in this experiment. Fewer than a verification -- an
            experiment only has to separate hypotheses, not prove a fix.
        workers: The case's normal worker count.
        case_name: For the trajectory.

    Returns:
        The outcome, including whether it matched the prediction.
    """
    env, args, effective_workers, note = _resolve(experiment, workers)
    report = runner.measure(
        project,
        runs=runs,
        pytest_args=args,
        env_overrides=env or None,
        workers=effective_workers,
        case_name=f"{case_name}@{experiment.manipulation}",
        agent_name="agent.experiment",
    )
    observed = report.flake_rate

    # An unsound batch is not evidence. If the manipulation stopped the test
    # from running at all -- a node id that does not exist, a usage error --
    # every run reports ERROR, no run reports FAIL, and the flake rate reads
    # 0.0%. Read naively that looks like "eliminated", which is the single
    # most dangerous misreading available to this loop: it manufactures
    # support for whichever hypothesis the experiment happened to target.
    #
    # This is the same trap as counting an ERROR run as a passing one during
    # verification, and it gets the same answer: errors are a broken
    # measurement, never a result.
    if not report.is_sound:
        return ExperimentOutcome(
            experiment=experiment,
            baseline_rate=baseline_rate,
            observed_rate=observed,
            report=report,
            actual_effect="invalid",
            matches_prediction=False,
            unsupported=True,
            note=(
                f"{report.errors}/{report.runs} runs errored, so the test never "
                f"executed: this manipulation produced no evidence either way. "
                f"Check the parameter -- a node id must match a real test."
            ),
            extras={"env": env, "pytest_args": args, "workers": effective_workers},
        )

    actual = classify_effect(baseline_rate, observed)

    # "reduced" partially supports a prediction of "eliminated": the signal
    # moved the predicted way without fully vanishing. Treating that as a
    # refutation would discard real evidence over a threshold.
    predicted = experiment.predicted_effect
    matches = actual == predicted or (
        predicted == "eliminated" and actual == "reduced"
    )

    return ExperimentOutcome(
        experiment=experiment,
        baseline_rate=baseline_rate,
        observed_rate=observed,
        report=report,
        actual_effect=actual,
        matches_prediction=matches,
        unsupported=bool(note),
        note=note,
        extras={"env": env, "pytest_args": args, "workers": effective_workers},
    )
