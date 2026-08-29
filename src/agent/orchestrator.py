"""The agent loop: confirm, hypothesise, experiment, patch, validate, verify.

    CONFIRM      measure the flake rate and collect failure signatures
    HYPOTHESIZE  ranked candidates, each with a discriminating prediction
    EXPERIMENT   run the cheapest manipulation that separates the top two
    OBSERVE      compare observed against predicted; confirm or eliminate
    PATCH        author a minimal fix for the confirmed cause
    VALIDATE     anti-cheat, structural and behavioural; reject with reasons
    VERIFY       run the patched test 500 times; any failure reopens the loop
    APPROVE      package the patch and its evidence for a human
    REPORT       record the outcome

Nothing here writes to ``corpus/``. Patches are applied to throwaway copies
under ``/scratch``, and a fix that survives verification is written to
``results/pending_approval/`` for a person to review. That is the whole of the
human checkpoint in an unattended run: the agent may produce a patch and prove
it, but it may not install one.

Two stopping rules, and they mean different things. Five rounds is an
arbitrary bound to keep a loop finite. Repeating a hypothesis with no new
discriminating evidence is a real signal -- it means the experiments are not
separating anything, so more rounds would only produce more of the same. The
second is the one that indicates the agent is stuck rather than slow.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.agent.experiments import (
    EXPERIMENT_SCHEMA,
    MANIPULATIONS,
    Experiment,
    ExperimentOutcome,
    discover_node_ids,
    run_experiment,
)
from src.agent.checkpoint import CaseCheckpoint, load_checkpoint, save_checkpoint
from src.agent.hypotheses import Hypothesis, propose_round
from src.agent.patcher import author_patch
from src.baseline.one_shot import PatchApplicationError, apply_patch
from src.harness.runner import BatchReport, TestRunner
from src.harness.validator import FixValidator, ValidationVerdict
from src.llm.client import GeminiClient, LLMError
from src.llm.prompts import render_project
from src.sandbox.executor import SandboxExecutor

__all__ = ["AgentConfig", "CaseOutcome", "run_agent_case"]

EXPERIMENT_SYSTEM = """You design experiments that separate competing
explanations for a flaky test.

Choose the manipulation whose outcome differs most between the top hypotheses.
A manipulation that all of them predict the same result for is wasted, however
cheap."""


@dataclass(slots=True)
class AgentConfig:
    """Run parameters for one case."""

    confirm_runs: int = 200
    experiment_runs: int = 150
    verify_runs: int = 500
    stress_runs: int = 200
    workers: int = 8
    max_rounds: int = 5
    max_patch_attempts: int = 3
    scratch_root: Path = Path("/scratch/agent")
    #: Where per-case checkpoints live. Overridden in tests so runs stay
    #: isolated -- a leaked checkpoint would make a test resume from another.
    checkpoint_root: Path | None = None


@dataclass
class CaseOutcome:
    """Everything the agent did on one case, and where it ended up."""

    case: str
    status: str = "UNRESOLVED"
    expected_class: str | None = None
    concluded_class: str | None = None
    rounds: int = 0
    confirm_report: BatchReport | None = None
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    experiments: list[dict[str, Any]] = field(default_factory=list)
    validations: list[dict[str, Any]] = field(default_factory=list)
    patch: dict[str, Any] | None = None
    verify_report: BatchReport | None = None
    stuck_reason: str | None = None
    error: str | None = None
    wall_s: float = 0.0
    prompt_tokens: int = 0
    output_tokens: int = 0
    approval_dir: str | None = None
    resumed_from: str = "no checkpoint"

    @property
    def cause_identified(self) -> bool:
        """Whether the concluded class matches the case's recorded root cause."""
        return bool(
            self.concluded_class
            and self.expected_class
            and self.concluded_class == self.expected_class
        )

    @property
    def residual_flake_rate(self) -> float | None:
        """Failures per run after the fix, or None if never verified."""
        return self.verify_report.flake_rate if self.verify_report else None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the results file."""
        return {
            "case": self.case,
            "status": self.status,
            "expected_class": self.expected_class,
            "concluded_class": self.concluded_class,
            "cause_identified": self.cause_identified,
            "rounds": self.rounds,
            "confirm": self.confirm_report.to_dict() if self.confirm_report else None,
            "hypotheses": self.hypotheses,
            "experiments": self.experiments,
            "validations": self.validations,
            "patch": self.patch,
            "verification": self.verify_report.to_dict() if self.verify_report else None,
            "residual_flake_rate": self.residual_flake_rate,
            "stuck_reason": self.stuck_reason,
            "error": self.error,
            "wall_s": round(self.wall_s, 1),
            "tokens": {
                "prompt": self.prompt_tokens,
                "output": self.output_tokens,
                "total": self.prompt_tokens + self.output_tokens,
            },
            "approval_dir": self.approval_dir,
            "resumed_from": self.resumed_from,
        }


def design_experiment(
    client: GeminiClient,
    case_name: str,
    hypotheses: list[Hypothesis],
    history: list[str],
    node_ids: list[str] | None = None,
) -> Experiment:
    """Ask for the manipulation that best separates the top hypotheses.

    ``node_ids`` are the case's real tests. Supplying them stops the model
    inventing a plausible name for ``isolate_test``; an invented one makes
    pytest collect nothing, which is not evidence about anything.
    """
    catalogue = "\n".join(f"  {name}: {text}" for name, text in MANIPULATIONS.items())
    candidates = "\n".join(
        f"  {h.id} [{h.root_cause_class}]: {h.reasoning}\n"
        f"      predicts: {h.discriminating_prediction}"
        for h in hypotheses
    )
    prior = "\n".join(f"  - {line}" for line in history) or "  (none yet)"
    tests = "\n".join(f"  {n}" for n in (node_ids or [])) or "  (unknown)"

    instruction = f"""Case: {case_name}

The tests in this case, by exact node id. For isolate_test or
force_test_order the parameter MUST be one of these verbatim -- an id that
does not exist makes pytest collect nothing, which proves nothing:
{tests}

Competing hypotheses:
{candidates}

Experiments already run:
{prior}

Available manipulations:
{catalogue}

Choose one manipulation that would separate these hypotheses, say which
hypothesis it targets, and state what the flake rate would do IF that
hypothesis is correct. Do not repeat an experiment already run unless its
result was ambiguous."""

    response = client.complete(
        agent_name="agent.experiment.design",
        instruction=instruction,
        system=EXPERIMENT_SYSTEM,
        response_schema=EXPERIMENT_SCHEMA,
    )
    return Experiment.from_dict(response.json())


def write_approval_package(
    outcome: CaseOutcome,
    case: Path,
    patched: Path,
    approval_root: Path,
    trace_run_id: str,
) -> Path:
    """Write a patch and its evidence for a human to review.

    This is the human checkpoint made concrete. The patch exists, it is proven,
    and it is *not installed* -- a person decides that.
    """
    target = approval_root / case.name
    if target.exists():
        shutil.rmtree(target)
    (target / "patched_files").mkdir(parents=True, exist_ok=True)

    patch = outcome.patch or {}
    for entry in patch.get("files", []):
        relative = str(entry.get("path", "")).lstrip("/")
        destination = target / "patched_files" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(entry.get("new_content", ""), encoding="utf-8")

    verification = outcome.verify_report
    evidence_lines = [
        f"- {e['experiment']['manipulation']}"
        f"({e['experiment']['parameter']}): "
        f"{e['baseline_rate']:.1%} -> {e['observed_rate']:.1%} "
        f"({e['actual_effect']}), "
        f"{'confirms' if e['matches_prediction'] else 'eliminates'} "
        f"{e['experiment']['targets_hypothesis']}"
        for e in outcome.experiments
    ]

    writeup = f"""# {case.name} — root cause and evidence

**Status: PENDING HUMAN APPROVAL.** This patch has not been applied to the
repository. It was produced and verified inside the sandbox.

## Root cause

**Class:** `{outcome.concluded_class}` (recorded class: `{outcome.expected_class}`)

{patch.get("root_cause_explanation", "(none recorded)")}

## How it was established

Confirmed flake rate before any change:
{outcome.confirm_report.summary() if outcome.confirm_report else "(not measured)"}

{outcome.confirm_report.signature_table() if outcome.confirm_report else ""}

Experiments run ({len(outcome.experiments)} across {outcome.rounds} round(s)):

{chr(10).join(evidence_lines) or "- (none)"}

## The fix

Files changed: {", ".join(f"`{f.get('path')}`" for f in patch.get("files", [])) or "(none)"}

{chr(10).join(f"### `{f.get('path')}`{chr(10)}{chr(10)}{f.get('rationale', '')}" for f in patch.get("files", []))}

## Verification

{verification.summary() if verification else "(not verified)"}

Residual flake rate: **{outcome.residual_flake_rate:.2%}** over
{verification.runs if verification else 0} runs at
{verification.workers if verification else 0} worker(s).

## Anti-cheat validation

{chr(10).join(f"- {c['name']}: {'PASS' if c['passed'] else 'FAIL'} — {c['detail']}" for v in outcome.validations for c in v["checks"])}

## Trajectory

Full turn-by-turn record: `traces/{trace_run_id}.jsonl`

## To apply

Review `patched_files/`, then copy its contents over
`corpus/{case.name}/project/` if you approve.
"""
    (target / "ROOT_CAUSE.md").write_text(writeup, encoding="utf-8")
    (target / "patch.json").write_text(json.dumps(patch, indent=2), encoding="utf-8")
    (target / "evidence.json").write_text(
        json.dumps(outcome.to_dict(), indent=2), encoding="utf-8"
    )
    return target


def run_agent_case(
    case: Path,
    client: GeminiClient,
    executor: SandboxExecutor,
    runner: TestRunner,
    config: AgentConfig,
    approval_root: Path,
    trace_run_id: str,
) -> CaseOutcome:
    """Run the full loop against one case."""
    started = time.perf_counter()
    metadata = json.loads((case / "metadata.json").read_text(encoding="utf-8"))
    project = case / "project"
    source = render_project(project)

    outcome = CaseOutcome(case=case.name, expected_class=metadata.get("root_cause_class"))
    tokens_before = (client.total_prompt_tokens, client.total_output_tokens)

    validator = FixValidator(
        executor,
        runner,
        client.tracer,
        protected_paths=metadata.get("protected_paths", ["conftest.py"]),
    )

    try:
        # -- resume ----------------------------------------------------------
        # A case cut off by quota has already established things. Re-deriving
        # them costs requests we do not have.
        checkpoint = load_checkpoint(
            case.name, client.model, config.checkpoint_root
        ) or CaseCheckpoint(
            case=case.name, model=client.model
        )
        outcome.resumed_from = checkpoint.describe()

        # -- CONFIRM ---------------------------------------------------------
        # A checkpointed measurement is only reusable if it is at least as
        # large as the one this run would take. A smaller sample -- a stale
        # checkpoint from a cheaper configuration -- would silently weaken the
        # evidence every later step rests on. Caught in practice: a unit test
        # leaked a 60-run CONFIRM into a case whose real budget is 200.
        reusable = (
            checkpoint.confirm is not None
            and checkpoint.confirm.runs >= config.confirm_runs
        )
        if checkpoint.confirm is not None and not reusable:
            outcome.resumed_from += (
                f" (discarded a {checkpoint.confirm.runs}-run CONFIRM: "
                f"smaller than this run's {config.confirm_runs})"
            )
            checkpoint.confirm = None

        if reusable:
            confirm = checkpoint.confirm
        else:
            confirm = runner.measure(
                project,
                runs=config.confirm_runs,
                workers=config.workers,
                case_name=case.name,
                agent_name="agent.confirm",
            )
            checkpoint.confirm = confirm
            save_checkpoint(checkpoint, config.checkpoint_root)
        outcome.confirm_report = confirm
        if confirm.failures == 0:
            outcome.status = "NO_FLAKE"
            outcome.stuck_reason = (
                f"never failed in {confirm.runs} runs; nothing to diagnose"
            )
            return outcome

        history: list[str] = list(checkpoint.history) if checkpoint.llm_state_usable else []
        eliminated: list[str] = (
            list(checkpoint.eliminated) if checkpoint.llm_state_usable else []
        )
        seen_signatures: set[tuple[str, tuple[str, ...]]] = {
            (sig[0], tuple(sig[1])) for sig in checkpoint.seen_signatures
        } if checkpoint.llm_state_usable else set()
        if checkpoint.llm_state_usable:
            outcome.hypotheses = list(checkpoint.hypotheses)
            outcome.experiments = list(checkpoint.experiments)
        already_done = checkpoint.rounds_completed if checkpoint.llm_state_usable else 0

        for round_number in range(already_done + 1, config.max_rounds + 1):
            outcome.rounds = round_number

            # -- HYPOTHESIZE + EXPERIMENT DESIGN, one request ------------------
            hypotheses, experiment_payload = propose_round(
                client,
                case.name,
                source,
                confirm,
                history,
                eliminated,
                round_number,
                MANIPULATIONS,
                discover_node_ids(project),
            )
            if not hypotheses:
                outcome.stuck_reason = "the model proposed no hypotheses"
                break
            outcome.hypotheses.append(
                {"round": round_number, "items": [h.to_dict() for h in hypotheses]}
            )

            # -- stuck detection ---------------------------------------------
            signature = (hypotheses[0].root_cause_class, tuple(sorted(set(eliminated))))
            if signature in seen_signatures:
                outcome.status = "UNRESOLVED"
                outcome.stuck_reason = (
                    f"hypothesis {hypotheses[0].root_cause_class!r} was proposed again "
                    f"in round {round_number} with no new discriminating evidence "
                    f"since it was last proposed; the experiments are not separating "
                    f"the candidates"
                )
                return outcome
            seen_signatures.add(signature)

            # -- EXPERIMENT + OBSERVE ----------------------------------------
            experiment = Experiment.from_dict(experiment_payload)
            result: ExperimentOutcome = run_experiment(
                experiment,
                project,
                runner,
                confirm.flake_rate,
                config.experiment_runs,
                config.workers,
                case.name,
            )
            outcome.experiments.append(result.to_dict())
            history.append(result.summary())

            # Checkpoint before the patch attempts: this round's evidence is
            # established regardless of what happens next.
            checkpoint.rounds_completed = round_number
            checkpoint.hypotheses = outcome.hypotheses
            checkpoint.experiments = outcome.experiments
            checkpoint.history = history
            checkpoint.eliminated = eliminated
            checkpoint.seen_signatures = [
                [cls, list(elim)] for cls, elim in seen_signatures
            ]
            save_checkpoint(checkpoint, config.checkpoint_root)

            targeted = next(
                (h for h in hypotheses if h.id == experiment.targets_hypothesis),
                hypotheses[0],
            )
            if not result.matches_prediction:
                eliminated.append(targeted.root_cause_class)
                continue

            # -- PATCH / VALIDATE --------------------------------------------
            outcome.concluded_class = targeted.root_cause_class
            rejections: list[str] = []
            patched = config.scratch_root / case.name
            verdict: ValidationVerdict | None = None

            for attempt in range(1, config.max_patch_attempts + 1):
                patch = author_patch(
                    client,
                    case.name,
                    source,
                    targeted.root_cause_class,
                    targeted.reasoning,
                    history,
                    rejections,
                    attempt,
                )
                outcome.patch = patch
                try:
                    changed = apply_patch(project, patched, patch)
                except PatchApplicationError as exc:
                    rejections = [f"patch could not be applied: {exc}"]
                    continue
                executor.clear_stage(patched)

                verdict = validator.validate(
                    case.name,
                    project,
                    patched,
                    changed,
                    stress_runs=config.stress_runs,
                    workers=config.workers,
                )
                outcome.validations.append(
                    {"attempt": attempt, "round": round_number, **verdict.to_dict()}
                )
                if verdict.passed:
                    break
                rejections = verdict.rejections

            if verdict is None or not verdict.passed:
                history.append(
                    f"patch attempts for {targeted.root_cause_class} were all "
                    f"rejected: {'; '.join(rejections)}"
                )
                eliminated.append(targeted.root_cause_class)
                continue

            # -- VERIFY ------------------------------------------------------
            executor.clear_stage(patched)
            verify = runner.measure(
                patched,
                runs=config.verify_runs,
                workers=config.workers,
                case_name=f"{case.name}@verify",
                agent_name="agent.verify",
            )
            outcome.verify_report = verify
            if verify.failures == 0 and verify.is_sound:
                outcome.status = "PENDING"
                target = write_approval_package(
                    outcome, case, patched, approval_root, trace_run_id
                )
                outcome.approval_dir = str(target)

                # The APPROVE step is the human checkpoint, so it belongs in
                # the trajectory as one. The run does not decide -- it stops,
                # having produced a patch and the evidence for it, and records
                # that a person is now required. `decision` stays "pending"
                # because nobody has looked yet; writing "approved" here would
                # be the agent approving its own work.
                with client.tracer.turn(
                    "agent.approve",
                    client.model,
                    f"A fix for {case.name} survived validation and a "
                    f"{config.verify_runs}-run verification. Present it for "
                    f"human approval; do not apply it.",
                ) as turn:
                    turn.call(
                        "write_approval_package",
                        case=case.name,
                        destination=str(target),
                        files=[f.get("path") for f in (outcome.patch or {}).get("files", [])],
                        residual_flake_rate=verify.flake_rate,
                        verification_runs=verify.runs,
                    )
                    turn.respond(
                        stdout=(
                            f"patch + root-cause writeup + {verify.runs}-run evidence "
                            f"written to {target}; corpus/ unmodified"
                        ),
                        exit_code=0,
                    )
                    turn.checkpoint(
                        prompted=True,
                        decision="pending",
                        note=(
                            f"awaiting human review of {target.name}; the patch has "
                            f"NOT been applied to the repository"
                        ),
                    )
                    turn.reflect(
                        f"{verify.failures}/{verify.runs} failures after the fix and "
                        f"every validator check passed, so the loop stops here. "
                        f"Installing the patch is a person's decision, not mine."
                    )
                return outcome

            history.append(
                f"the fix for {targeted.root_cause_class} still failed "
                f"{verify.failures}/{verify.runs} times in verification"
            )
            eliminated.append(targeted.root_cause_class)

        if outcome.status == "UNRESOLVED" and not outcome.stuck_reason:
            outcome.stuck_reason = (
                f"exhausted {config.max_rounds} hypothesis rounds without a "
                "fix that survived verification"
            )
    except LLMError as exc:
        outcome.status = "ERROR"
        outcome.error = str(exc)
    finally:
        outcome.wall_s = time.perf_counter() - started
        outcome.prompt_tokens = client.total_prompt_tokens - tokens_before[0]
        outcome.output_tokens = client.total_output_tokens - tokens_before[1]

    return outcome
