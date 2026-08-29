"""The fair baseline: one LLM call per case, then measure what it produced.

The baseline gets the same model, the same view of the project, the same
taxonomy and the same rules about what counts as a fix as the agent does. It
gets one call, and it never sees a test run.

That absence is the whole experiment. The agent's advantage is not a better
prompt or a bigger model -- it is that it can execute the test hundreds of
times and reason about what came back. Anything else that differed between the
arms would confound the result, which is why the shared material lives in
`src/llm/prompts.py` with exactly one definition.

The patch is applied to a throwaway copy under /scratch and measured there.
Nothing is written to `corpus/`, which is mounted read-only in any case.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.harness.runner import BatchReport, TestRunner
from src.llm.client import GeminiClient, LLMError
from src.llm.prompts import (
    FIX_RULES,
    PATCH_SCHEMA,
    render_project,
    taxonomy_block,
)
from src.sandbox.executor import SandboxExecutor

__all__ = ["BaselineResult", "PatchApplicationError", "apply_patch", "run_baseline_case"]

SYSTEM_PROMPT = """You are an experienced Python engineer fixing a flaky test.

A flaky test passes most of the time and fails intermittently with no code
change. Your job is to find the source of nondeterminism and remove it."""


class PatchApplicationError(RuntimeError):
    """Raised when a model-authored patch could not be applied safely."""


@dataclass
class BaselineResult:
    """What the baseline produced for one case, and how well it worked."""

    case: str
    root_cause_class: str | None
    root_cause_explanation: str
    confidence: str
    files_changed: list[str]
    patch: dict[str, Any]
    prompt_tokens: int
    output_tokens: int
    latency_ms: int
    applied: bool
    apply_error: str | None = None
    report: BatchReport | None = None
    expected_class: str | None = None
    baseline_flake_rate: float | None = None

    @property
    def cause_identified(self) -> bool:
        """Whether the classification matches the case's recorded root cause."""
        return (
            self.root_cause_class is not None
            and self.expected_class is not None
            and self.root_cause_class == self.expected_class
        )

    @property
    def residual_flake_rate(self) -> float | None:
        """Failures per run after the fix, or None if it never ran."""
        return self.report.flake_rate if self.report else None

    @property
    def fixed(self) -> bool:
        """Whether the fix achieved zero failures over the measured runs."""
        return bool(self.report and self.report.failures == 0 and self.report.is_sound)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the results file."""
        return {
            "case": self.case,
            "root_cause_class": self.root_cause_class,
            "expected_class": self.expected_class,
            "cause_identified": self.cause_identified,
            "root_cause_explanation": self.root_cause_explanation,
            "confidence": self.confidence,
            "files_changed": self.files_changed,
            "applied": self.applied,
            "apply_error": self.apply_error,
            "baseline_flake_rate": self.baseline_flake_rate,
            "residual_flake_rate": self.residual_flake_rate,
            "fixed": self.fixed,
            "verification": self.report.to_dict() if self.report else None,
            "tokens": {
                "prompt": self.prompt_tokens,
                "output": self.output_tokens,
                "total": self.prompt_tokens + self.output_tokens,
            },
            "latency_ms": self.latency_ms,
            "patch": self.patch,
        }


def build_instruction(case: Path) -> str:
    """Render the one prompt the baseline gets."""
    project = case / "project"
    return f"""This test suite contains a flaky test: it passes most of the time and
fails intermittently, with no code change between runs.

Diagnose the root cause and fix it.

{taxonomy_block()}

{FIX_RULES}

Here is the complete project.

{render_project(project)}

Return the root cause class, an explanation, your confidence, and the complete
new contents of every file you change."""


def apply_patch(source_project: Path, destination: Path, patch: dict[str, Any]) -> list[str]:
    """Copy the project and apply a model-authored patch to the copy.

    Args:
        source_project: The pristine case project. Never modified.
        destination: Where to build the patched copy.
        patch: The model's structured reply.

    Returns:
        The relative paths written.

    Raises:
        PatchApplicationError: If a path escapes the project or is not a file
            the project contains.
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_project, destination)

    written: list[str] = []
    for entry in patch.get("files", []):
        relative = str(entry.get("path", "")).strip().lstrip("/")
        if not relative:
            raise PatchApplicationError("patch entry has no path")
        target = (destination / relative).resolve()
        # Model-authored path: never let it escape the copy.
        if not str(target).startswith(str(destination.resolve())):
            raise PatchApplicationError(f"path escapes the project: {relative!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry.get("new_content", ""), encoding="utf-8")
        written.append(relative)

    if not written:
        raise PatchApplicationError("the patch changed no files")
    return written


def run_baseline_case(
    case: Path,
    client: GeminiClient,
    executor: SandboxExecutor,
    runner: TestRunner,
    runs: int,
    workers: int,
    scratch_root: Path = Path("/scratch/baseline"),
) -> BaselineResult:
    """Run the one-shot baseline against a single case and measure the result.

    Args:
        case: The corpus case directory.
        client: Shared LLM client.
        executor: Sandbox executor.
        runner: Repeat-execution harness -- used to *score* the baseline, never
            offered to it.
        runs: Verification runs after the fix.
        workers: Concurrency for verification.
        scratch_root: Where patched copies are built.

    Returns:
        The measured result.
    """
    metadata = json.loads((case / "metadata.json").read_text(encoding="utf-8"))
    expected = metadata.get("root_cause_class")
    recorded = (metadata.get("baseline") or {}).get("flake_rate")

    response = client.complete(
        agent_name="baseline.one_shot",
        instruction=build_instruction(case),
        system=SYSTEM_PROMPT,
        response_schema=PATCH_SCHEMA,
    )
    try:
        patch = response.json()
    except (ValueError, TypeError) as exc:
        raise LLMError(f"{case.name}: reply was not valid JSON: {exc}") from exc

    result = BaselineResult(
        case=case.name,
        root_cause_class=patch.get("root_cause_class"),
        root_cause_explanation=patch.get("root_cause_explanation", ""),
        confidence=patch.get("confidence", "unknown"),
        files_changed=[f.get("path", "?") for f in patch.get("files", [])],
        patch=patch,
        prompt_tokens=response.prompt_tokens,
        output_tokens=response.billed_output_tokens,
        latency_ms=response.latency_ms,
        applied=False,
        expected_class=expected,
        baseline_flake_rate=recorded,
    )

    destination = scratch_root / case.name
    try:
        result.files_changed = apply_patch(case / "project", destination, patch)
        result.applied = True
    except PatchApplicationError as exc:
        result.apply_error = str(exc)
        return result

    # The patched copy is a different path, so it stages independently of the
    # pristine case and cannot pick up a cached copy of it.
    executor.clear_stage(destination)
    result.report = runner.measure(
        destination,
        runs=runs,
        workers=workers,
        case_name=f"{case.name}@baseline",
        agent_name="baseline.verify",
    )
    return result
