"""Unit tests for the anti-cheat validator.

The validator is what stops a green suite being mistaken for a fixed bug, so
its checks need to be exercised directly rather than only through a live agent
run that may or may not happen to produce each kind of cheat.

The structural checks are tested here in full. They need no execution, so a
patch is built on disk and validated with the stress pass switched off. The
behavioural check is covered separately by `scripts/demo_masking_fix.py`, which
measures real masking fixes against real workloads.

The case that motivates all of this: case 07's *correct* fix is a retry, and
case 12's *masking* fix is also a retry. Nothing here rejects a patch for
containing a retry, and the tests below pin that down.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.harness.runner import TestRunner
from src.harness.validator import FixValidator
from src.sandbox.executor import SandboxExecutor
from src.telemetry.tracer import Tracer

ORIGINAL_SOURCE = '''"""The code under test."""


def compute(values):
    """Return the total."""
    total = 0
    for value in values:
        total += value
    return total
'''

ORIGINAL_TEST = '''"""Tests."""

from app.thing import compute


def test_total_is_exact():
    assert compute([0.1, 0.2]) == 0.30000000000000004
    assert compute([1, 2]) == 3


def test_empty_is_zero():
    assert compute([]) == 0
'''

CONFTEST = '''"""Test infrastructure that supplies the bug's conditions."""


def pytest_collection_modifyitems(session, config, items):
    items.sort(key=lambda item: hash(item.name))
'''


@pytest.fixture()
def workspace(tmp_path: Path):
    """A pristine project and a validator wired to a real sandbox."""
    original = tmp_path / "original"
    (original / "app").mkdir(parents=True)
    (original / "app" / "thing.py").write_text(ORIGINAL_SOURCE, encoding="utf-8")
    (original / "test_thing.py").write_text(ORIGINAL_TEST, encoding="utf-8")
    (original / "conftest.py").write_text(CONFTEST, encoding="utf-8")

    tracer = Tracer(trace_dir=tmp_path / "traces", run_id="validator-test")
    executor = SandboxExecutor(
        tracer, scratch_root=tmp_path / "scratch", trace_each_run=False
    )
    validator = FixValidator(executor, TestRunner(executor, tracer), tracer)
    return original, validator, tmp_path


def patched_copy(original: Path, tmp_path: Path, edits: dict[str, str]) -> Path:
    """Copy the project and apply file rewrites."""
    patched = tmp_path / "patched"
    if patched.exists():
        shutil.rmtree(patched)
    shutil.copytree(original, patched)
    for relative, content in edits.items():
        (patched / relative).write_text(content, encoding="utf-8")
    return patched


def check_named(verdict, name: str):
    """Pull one named check out of a verdict."""
    return next(c for c in verdict.checks if c.name == name)


class TestAcceptsLegitimateFixes:
    """A real fix must not be rejected for looking like a cheat."""

    def test_a_source_only_fix_passes(self, workspace) -> None:
        original, validator, tmp_path = workspace
        fixed = ORIGINAL_SOURCE.replace(
            "    total = 0\n    for value in values:\n        total += value\n    return total",
            "    import math\n\n    return math.fsum(values)",
        )
        patched = patched_copy(original, tmp_path, {"app/thing.py": fixed})

        verdict = validator.validate(
            "case", original, patched, ["app/thing.py"], run_stress=False
        )

        assert verdict.passed, verdict.rejections

    def test_a_retry_in_the_source_is_not_rejected(self, workspace) -> None:
        """case 07's correct fix is a retry. Syntax must not condemn it."""
        original, validator, tmp_path = workspace
        with_retry = ORIGINAL_SOURCE.replace(
            '    """Return the total."""',
            '''    """Return the total, retrying a flaky backend."""
    import time

    for attempt in range(3):
        try:
            return _fetch(values)
        except OSError:
            time.sleep(0.01 * 2**attempt)
    raise OSError("gave up")


def _fetch(values):''',
        )
        patched = patched_copy(original, tmp_path, {"app/thing.py": with_retry})

        verdict = validator.validate(
            "case", original, patched, ["app/thing.py"], run_stress=False
        )

        assert verdict.passed, verdict.rejections

    def test_a_source_fix_alongside_a_test_update_passes(self, workspace) -> None:
        """Changing a test is allowed; changing *only* the test is not."""
        original, validator, tmp_path = workspace
        fixed_source = ORIGINAL_SOURCE.replace("total = 0", "total = 0.0")
        extra_test = ORIGINAL_TEST + '''

def test_new_coverage():
    assert compute([5]) == 5
'''
        patched = patched_copy(
            original,
            tmp_path,
            {"app/thing.py": fixed_source, "test_thing.py": extra_test},
        )

        verdict = validator.validate(
            "case", original, patched, ["app/thing.py", "test_thing.py"], run_stress=False
        )

        assert verdict.passed, verdict.rejections


class TestRejectsCheats:
    """Each check must actually fire on the cheat it exists for."""

    def test_a_test_only_change_is_rejected(self, workspace) -> None:
        original, validator, tmp_path = workspace
        patched = patched_copy(original, tmp_path, {"test_thing.py": ORIGINAL_TEST})

        verdict = validator.validate(
            "case", original, patched, ["test_thing.py"], run_stress=False
        )

        assert not verdict.passed
        assert not check_named(verdict, "modifies_source").passed

    def test_deleting_an_assertion_is_rejected(self, workspace) -> None:
        original, validator, tmp_path = workspace
        weakened = ORIGINAL_TEST.replace("    assert compute([1, 2]) == 3\n", "")
        patched = patched_copy(
            original,
            tmp_path,
            {"test_thing.py": weakened, "app/thing.py": ORIGINAL_SOURCE + "\n"},
        )

        verdict = validator.validate(
            "case", original, patched, ["test_thing.py", "app/thing.py"], run_stress=False
        )

        assert not verdict.passed
        assert not check_named(verdict, "assertions_intact").passed

    def test_deleting_a_whole_test_is_rejected(self, workspace) -> None:
        original, validator, tmp_path = workspace
        without = ORIGINAL_TEST.split("def test_empty_is_zero")[0]
        patched = patched_copy(
            original,
            tmp_path,
            {"test_thing.py": without, "app/thing.py": ORIGINAL_SOURCE + "\n"},
        )

        verdict = validator.validate(
            "case", original, patched, ["test_thing.py", "app/thing.py"], run_stress=False
        )

        assert not check_named(verdict, "assertions_intact").passed

    def test_a_trivially_true_assertion_is_rejected(self, workspace) -> None:
        original, validator, tmp_path = workspace
        trivial = ORIGINAL_TEST.replace(
            "assert compute([1, 2]) == 3", "assert True"
        )
        patched = patched_copy(
            original,
            tmp_path,
            {"test_thing.py": trivial, "app/thing.py": ORIGINAL_SOURCE + "\n"},
        )

        verdict = validator.validate(
            "case", original, patched, ["test_thing.py", "app/thing.py"], run_stress=False
        )

        assert not check_named(verdict, "assertions_intact").passed

    @pytest.mark.parametrize("marker", ["skip", "xfail", "flaky"])
    def test_disabling_the_test_is_rejected(self, workspace, marker: str) -> None:
        original, validator, tmp_path = workspace
        disabled = ORIGINAL_TEST.replace(
            "def test_total_is_exact():",
            f"@pytest.mark.{marker}\ndef test_total_is_exact():",
        ).replace('"""Tests."""', '"""Tests."""\n\nimport pytest')
        patched = patched_copy(
            original,
            tmp_path,
            {"test_thing.py": disabled, "app/thing.py": ORIGINAL_SOURCE + "\n"},
        )

        verdict = validator.validate(
            "case", original, patched, ["test_thing.py", "app/thing.py"], run_stress=False
        )

        assert not check_named(verdict, "test_not_disabled").passed

    def test_loosening_an_exact_comparison_is_rejected(self, workspace) -> None:
        """Swapping `==` for `approx` weakens the test rather than fixing it."""
        original, validator, tmp_path = workspace
        loosened = ORIGINAL_TEST.replace(
            "assert compute([0.1, 0.2]) == 0.30000000000000004",
            "assert compute([0.1, 0.2]) == pytest.approx(0.3)",
        ).replace('"""Tests."""', '"""Tests."""\n\nimport pytest')
        patched = patched_copy(
            original,
            tmp_path,
            {"test_thing.py": loosened, "app/thing.py": ORIGINAL_SOURCE + "\n"},
        )

        verdict = validator.validate(
            "case", original, patched, ["test_thing.py", "app/thing.py"], run_stress=False
        )

        assert not check_named(verdict, "comparison_not_loosened").passed

    def test_editing_conftest_is_rejected(self, workspace) -> None:
        """conftest supplies the bug's conditions; neutering it hides the bug."""
        original, validator, tmp_path = workspace
        neutered = CONFTEST.replace(
            "items.sort(key=lambda item: hash(item.name))", "return"
        )
        patched = patched_copy(
            original,
            tmp_path,
            {"conftest.py": neutered, "app/thing.py": ORIGINAL_SOURCE + "\n"},
        )

        verdict = validator.validate(
            "case", original, patched, ["conftest.py", "app/thing.py"], run_stress=False
        )

        assert not verdict.passed
        assert not check_named(verdict, "protected_paths").passed

    def test_a_patch_that_is_only_a_sleep_is_rejected(self, workspace) -> None:
        original, validator, tmp_path = workspace
        # The whole patch is one added line, and that line is a sleep: the
        # canonical "wait longer and hope" non-fix.
        slept = ORIGINAL_SOURCE.replace(
            "    total = 0", "    time.sleep(0.05)" + chr(92) + "n    total = 0"
        )
        patched = patched_copy(original, tmp_path, {"app/thing.py": slept})

        verdict = validator.validate(
            "case", original, patched, ["app/thing.py"], run_stress=False
        )

        assert not check_named(verdict, "not_only_sleep").passed


class TestVerdictReporting:
    """Rejections must be usable as feedback, not just a boolean."""

    def test_rejections_name_the_failing_checks(self, workspace) -> None:
        original, validator, tmp_path = workspace
        patched = patched_copy(original, tmp_path, {"test_thing.py": ORIGINAL_TEST})

        verdict = validator.validate(
            "case", original, patched, ["test_thing.py"], run_stress=False
        )

        assert verdict.rejections
        assert any("modifies_source" in reason for reason in verdict.rejections)
        assert all(": " in reason for reason in verdict.rejections)

    def test_the_verdict_serialises_for_the_trajectory(self, workspace) -> None:
        original, validator, tmp_path = workspace
        patched = patched_copy(original, tmp_path, {"test_thing.py": ORIGINAL_TEST})

        payload = validator.validate(
            "case", original, patched, ["test_thing.py"], run_stress=False
        ).to_dict()

        assert payload["passed"] is False
        assert payload["checks"]
        assert all({"name", "passed", "detail"} <= set(c) for c in payload["checks"])

    def test_structural_failure_skips_the_stress_run(self, workspace) -> None:
        """No point proving a patch that deleted an assertion survives load."""
        original, validator, tmp_path = workspace
        patched = patched_copy(original, tmp_path, {"test_thing.py": ORIGINAL_TEST})

        verdict = validator.validate(
            "case", original, patched, ["test_thing.py"], run_stress=True
        )

        assert verdict.stress_report is None
        assert not any(c.name == "survives_stress" for c in verdict.checks)
