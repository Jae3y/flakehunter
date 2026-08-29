"""Anti-cheat validation: did this patch remove the bug, or hide it?

A fix that makes the test pass by weakening the test is not a fix. Neither is
one that widens a timing window until the failure stops being observable.

The hard part is that **syntax cannot decide this**. Case 07's correct fix is a
retry with backoff -- its root cause class is literally
``network_timeout_no_retry``, so the bug *is* the absence of a retry. Case 12's
masking fix is also a retry. A rule that rejects retries would fail the one
case where a retry is right; a rule that permits them would pass the case where
it is cheating. Measured, not asserted: both of case 12's masking fixes reached
0/300 failures at the corpus workload and would sail through a 500-run
verification.

So validation has two layers.

**Structural checks** decide what syntax genuinely can. Deleting an assertion,
skipping a test, marking it xfail, loosening an exact comparison, editing the
conftest that supplies the bug's conditions, or changing nothing outside the
test file -- these are wrong regardless of intent, and are cheap to detect.

**A behavioural check** decides the rest. The insight is that a masking fix
and a real fix differ in *where their headroom runs out*. A sleep survives
until the operation it is hiding takes longer than the sleep. A retry survives
until the operation outlasts the whole retry budget. A fix that removed the
race has no budget to exhaust. So the patched code is re-verified under CPU
oversubscription -- many more concurrent runs than cores -- which stretches
every timing window without editing a single line of the case. A real fix
stays at zero. A mask comes back.

That amplifier is deliberately generic: it needs no per-case knowledge, so it
cannot be tuned to the twelve cases in the corpus and quietly stop generalising.
"""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from src.harness.runner import BatchReport, TestRunner
from src.sandbox.executor import SandboxExecutor
from src.telemetry.tracer import Tracer

__all__ = [
    "CheckResult",
    "FixValidator",
    "ValidationVerdict",
]

#: Markers that disable a test rather than fix it.
DISABLING_MARKERS = ("skip", "skipif", "xfail", "flaky", "repeat", "retry", "rerun")

#: Calls that loosen a comparison rather than make the code correct.
LOOSENING_CALLS = ("approx", "almost_equal", "assertAlmostEqual")

#: Multiplier on the normal worker count for the stress re-verification.
#: Four times the core count is enough oversubscription to stretch a timing
#: window well past a typical sleep or retry budget.
STRESS_WORKER_MULTIPLIER = 4


@dataclass(slots=True)
class CheckResult:
    """One validation check and what it found."""

    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name}: {self.detail}"


@dataclass(slots=True)
class ValidationVerdict:
    """The outcome of validating one patch."""

    checks: list[CheckResult] = field(default_factory=list)
    stress_report: BatchReport | None = None

    @property
    def passed(self) -> bool:
        """Whether every check passed."""
        return all(check.passed for check in self.checks)

    @property
    def rejections(self) -> list[str]:
        """Reasons this patch was rejected, for feeding back to the patcher."""
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]

    def to_dict(self) -> dict:
        """Serialise for results and trajectory."""
        return {
            "passed": self.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "rejections": self.rejections,
            "stress": self.stress_report.to_dict() if self.stress_report else None,
        }


def _is_test_file(relative: Path) -> bool:
    """Whether a project-relative path is a test module."""
    return relative.name.startswith("test_") or relative.name == "conftest.py"


def _assert_count(tree: ast.AST) -> dict[str, int]:
    """Assertions per top-level test function."""
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts[node.name] = sum(
                1 for child in ast.walk(node) if isinstance(child, ast.Assert)
            )
    return counts


def _trivially_true(node: ast.Assert) -> bool:
    """Whether an assertion can never fail."""
    test = node.test
    if isinstance(test, ast.Constant):
        return bool(test.value)
    # `assert x == x` and friends.
    if isinstance(test, ast.Compare) and len(test.comparators) == 1:
        return ast.dump(test.left) == ast.dump(test.comparators[0])
    return False


def _decorator_names(node: ast.AST) -> Iterable[str]:
    """Every dotted name appearing in a function's decorators."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    names: list[str] = []
    for decorator in node.decorator_list:
        for sub in ast.walk(decorator):
            if isinstance(sub, ast.Name):
                names.append(sub.id)
            elif isinstance(sub, ast.Attribute):
                names.append(sub.attr)
    return names


def _call_names(tree: ast.AST) -> list[str]:
    """Every called function name in a module."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return names


class FixValidator:
    """Decides whether a patch is a legitimate fix.

    Args:
        executor: Sandbox executor, used for the stress re-verification.
        runner: Repeat-execution harness.
        tracer: Trajectory writer; every verdict is recorded.
        protected_paths: Files a fix must not touch, from case metadata.
    """

    def __init__(
        self,
        executor: SandboxExecutor,
        runner: TestRunner,
        tracer: Tracer,
        protected_paths: Iterable[str] = ("conftest.py",),
    ) -> None:
        self.executor = executor
        self.runner = runner
        self.tracer = tracer
        self.protected = {str(p) for p in protected_paths}

    # -- structural layer ---------------------------------------------------

    def check_structure(
        self, original: Path, patched: Path, changed: Iterable[str]
    ) -> list[CheckResult]:
        """Run every check that can be decided from the source alone."""
        changed_paths = [Path(c) for c in changed]
        checks: list[CheckResult] = []

        touched_protected = [
            str(p) for p in changed_paths if p.name in self.protected
        ]
        checks.append(
            CheckResult(
                "protected_paths",
                not touched_protected,
                f"modified protected test infrastructure: {touched_protected}"
                if touched_protected
                else "no protected file touched",
            )
        )

        source_changes = [str(p) for p in changed_paths if not _is_test_file(p)]
        checks.append(
            CheckResult(
                "modifies_source",
                bool(source_changes),
                f"source modified: {source_changes}"
                if source_changes
                else "only test files changed; a fix must change the code under test",
            )
        )

        checks.extend(self._check_tests_intact(original, patched, changed_paths))
        checks.append(self._check_not_only_sleep(original, patched, changed_paths))
        return checks

    def _check_tests_intact(
        self, original: Path, patched: Path, changed: list[Path]
    ) -> list[CheckResult]:
        """Assertions must survive, undisabled and unloosened."""
        weakened: list[str] = []
        disabled: list[str] = []
        loosened: list[str] = []

        for relative in changed:
            if not _is_test_file(relative):
                continue
            before_path, after_path = original / relative, patched / relative
            if not before_path.exists() or not after_path.exists():
                weakened.append(f"{relative} was created or deleted")
                continue
            try:
                before = ast.parse(before_path.read_text(encoding="utf-8"))
                after = ast.parse(after_path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                weakened.append(f"{relative} does not parse: {exc}")
                continue

            before_counts, after_counts = _assert_count(before), _assert_count(after)
            for name, count in before_counts.items():
                if name not in after_counts:
                    weakened.append(f"{relative}::{name} was removed")
                elif after_counts[name] < count:
                    weakened.append(
                        f"{relative}::{name} lost assertions "
                        f"({count} -> {after_counts[name]})"
                    )

            for node in ast.walk(after):
                if isinstance(node, ast.Assert) and _trivially_true(node):
                    weakened.append(f"{relative}: an assertion is trivially true")
                markers = [
                    d for d in _decorator_names(node) if d in DISABLING_MARKERS
                ]
                if markers:
                    disabled.append(f"{relative}::{getattr(node, 'name', '?')} @{markers}")

            new_calls = set(_call_names(after)) - set(_call_names(before))
            loosening = sorted(new_calls & set(LOOSENING_CALLS))
            if loosening:
                loosened.append(f"{relative}: introduced {loosening}")

        return [
            CheckResult(
                "assertions_intact",
                not weakened,
                "; ".join(weakened) if weakened else "no assertion removed or weakened",
            ),
            CheckResult(
                "test_not_disabled",
                not disabled,
                "; ".join(disabled) if disabled else "no skip/xfail/retry marker added",
            ),
            CheckResult(
                "comparison_not_loosened",
                not loosened,
                "; ".join(loosened) if loosened else "no comparison loosened",
            ),
        ]

    def _check_not_only_sleep(
        self, original: Path, patched: Path, changed: list[Path]
    ) -> CheckResult:
        """A bare sleep must not be the only substantive change.

        This is narrow on purpose. It catches the patch whose entire content is
        "wait longer"; it does not try to judge a sleep that appears alongside
        real changes, because that judgement belongs to the stress check.
        """
        added: list[str] = []
        for relative in changed:
            before_path, after_path = original / relative, patched / relative
            before = (
                before_path.read_text(encoding="utf-8").splitlines()
                if before_path.exists()
                else []
            )
            after = (
                after_path.read_text(encoding="utf-8").splitlines()
                if after_path.exists()
                else []
            )
            for line in difflib.unified_diff(before, after, n=0):
                if not line.startswith("+") or line.startswith("+++"):
                    continue
                body = line[1:].strip()
                if not body or body.startswith("#"):
                    continue
                added.append(body)

        if not added:
            return CheckResult("not_only_sleep", False, "the patch added nothing")
        sleeps = [line for line in added if "sleep(" in line]
        only_sleep = bool(sleeps) and len(sleeps) == len(added)
        return CheckResult(
            "not_only_sleep",
            not only_sleep,
            f"the only added lines are sleeps: {sleeps}"
            if only_sleep
            else f"{len(added)} substantive line(s) added, {len(sleeps)} sleep(s)",
        )

    # -- behavioural layer --------------------------------------------------

    def check_behaviour(
        self,
        patched: Path,
        case_name: str,
        runs: int,
        workers: int,
    ) -> tuple[CheckResult, BatchReport]:
        """Re-verify under CPU oversubscription to exhaust any hidden headroom.

        A masking fix buys a fixed amount of headroom -- a sleep's duration, a
        retry budget. Oversubscribing the CPU stretches the operation it is
        hiding until that headroom runs out. A fix that removed the race has no
        headroom to exhaust and stays at zero.

        Args:
            patched: The patched project copy.
            case_name: For the trajectory.
            runs: Runs in the stress batch.
            workers: The normal worker count; the stress count is a multiple.

        Returns:
            The check and the batch report behind it.
        """
        stress_workers = max(workers * STRESS_WORKER_MULTIPLIER, 8)
        report = self.runner.measure(
            patched,
            runs=runs,
            workers=stress_workers,
            case_name=f"{case_name}@stress",
            agent_name="harness.validator.stress",
        )
        clean = report.failures == 0 and report.is_sound
        detail = (
            f"{report.failures}/{report.runs} failures at {stress_workers} workers "
            f"({stress_workers // max(workers, 1)}x oversubscription)"
        )
        if not clean:
            detail += " -- the failure returns under load, so the fix widened the window rather than closing it"
        return (
            CheckResult("survives_stress", clean, detail),
            report,
        )

    # -- entry point --------------------------------------------------------

    def validate(
        self,
        case_name: str,
        original: Path,
        patched: Path,
        changed: Iterable[str],
        stress_runs: int = 200,
        workers: int = 8,
        run_stress: bool = True,
    ) -> ValidationVerdict:
        """Validate one patch and record the verdict to the trajectory.

        Structural checks run first and short-circuit the stress check: there
        is no point spending CPU proving that a patch which deleted an
        assertion also survives load.
        """
        verdict = ValidationVerdict()
        verdict.checks.extend(self.check_structure(original, patched, changed))

        if verdict.passed and run_stress:
            check, report = self.check_behaviour(
                patched, case_name, stress_runs, workers
            )
            verdict.checks.append(check)
            verdict.stress_report = report

        with self.tracer.turn(
            "harness.validator",
            "n/a",
            f"Validate the patch for {case_name}: is the nondeterminism gone, "
            "or merely hidden?",
        ) as turn:
            turn.call("validate", case=case_name, files=list(changed))
            turn.respond(
                stdout="\n".join(str(c) for c in verdict.checks),
                exit_code=0 if verdict.passed else 1,
            )
            turn.reflect(
                "patch accepted"
                if verdict.passed
                else "patch rejected: " + "; ".join(verdict.rejections)
            )
        return verdict
