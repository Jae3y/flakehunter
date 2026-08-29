"""Unit tests for the repeat-execution harness.

The failure-signature logic gets the most attention here. It is the evidence
the HYPOTHESIZE step reasons over, so grouping that is too loose (one
signature per run) or too tight (two bugs merged into one) does more damage
than an inaccurate flake rate would.
"""

from __future__ import annotations

from collections import Counter

import pytest

from src.harness.runner import BatchReport, extract_signature, normalise_message
from src.sandbox.executor import ExecutionResult, Outcome, Strategy


def make_result(
    outcome: Outcome = Outcome.FAIL,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = 1,
) -> ExecutionResult:
    """Build an ExecutionResult with the fields signatures care about."""
    return ExecutionResult(
        outcome=outcome,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=10,
        strategy=Strategy.SPAWN,
    )


class TestSignatureExtraction:
    """Both shapes of pytest one-line traceback must parse."""

    def test_exception_with_message(self) -> None:
        result = make_result(
            stdout="/scratch/run-abc/project/test_x.py:22: "
            "AssertionError: the second hit landed in a different bucket"
        )
        assert extract_signature(result) == (
            "AssertionError at test_x.py:22: "
            "the second hit landed in a different bucket"
        )

    def test_bare_rewritten_assert_expression(self) -> None:
        """pytest prints the assertion itself when it carried no message."""
        result = make_result(
            stdout="/scratch/run-abc/project/test_counter.py:31: "
            "assert 390666 == (8 * 50000)"
        )
        assert extract_signature(result) == (
            "AssertionError at test_counter.py:31: assert <n> == (<n> * <n>)"
        )

    def test_exception_with_no_message(self) -> None:
        result = make_result(stdout="/tmp/test_y.py:4: KeyError")
        assert extract_signature(result) == "KeyError at test_y.py:4"

    def test_the_failing_frame_is_the_last_one(self) -> None:
        result = make_result(
            stdout=(
                "/tmp/test_a.py:1: AssertionError: first\n"
                "/tmp/test_b.py:2: ValueError: second\n"
            )
        )
        assert extract_signature(result).startswith("ValueError at test_b.py:2")

    def test_unparseable_output_is_reported_as_such(self) -> None:
        """Never silently return a plausible-looking signature."""
        result = make_result(stdout="something went wrong, somewhere")
        assert extract_signature(result) == "fail: unparsed pytest output"

    @pytest.mark.parametrize(
        ("outcome", "exit_code", "expected"),
        [
            (Outcome.PASS, 0, "pass"),
            (Outcome.TIMEOUT, None, "timeout: exceeded wall clock"),
            (Outcome.ERROR, 5, "error: pytest exit 5"),
        ],
    )
    def test_non_failure_outcomes(
        self, outcome: Outcome, exit_code: int | None, expected: str
    ) -> None:
        assert extract_signature(make_result(outcome, exit_code=exit_code)) == expected


class TestNormalisation:
    """Observed values collapse; structure survives."""

    def test_scratch_workdirs_are_collapsed(self) -> None:
        assert "<workdir>" in normalise_message("failed at /scratch/run-xyz/project/f")

    def test_object_addresses_are_collapsed(self) -> None:
        assert "<addr>" in normalise_message("<Counter object at 0x7f9c1a2b3c4d>")

    def test_observed_numbers_collapse_to_one_signature(self) -> None:
        """Otherwise every run of the race case is its own signature."""
        first = normalise_message("assert 390666 == (8 * 50000)")
        second = normalise_message("assert 391204 == (8 * 50000)")
        assert first == second

    def test_observed_sequences_collapse(self) -> None:
        first = normalise_message("an idle shard: [3, 3, 0]")
        second = normalise_message("an idle shard: [0, 5, 1]")
        assert first == second

    def test_structurally_different_failures_stay_distinct(self) -> None:
        """Collapsing values must not collapse different assertions."""
        assert normalise_message("assert 5 == 6") != normalise_message(
            "assert 5 in [1, 2]"
        )

    def test_message_length_is_capped(self) -> None:
        assert len(normalise_message("word " * 500)) <= 200


class TestBatchReport:
    """The report must not let a broken measurement look like a clean one."""

    def _report(self, **kwargs) -> BatchReport:
        defaults = dict(
            case="case_x",
            runs=100,
            failures=25,
            errors=0,
            outcomes=Counter({"pass": 75, "fail": 25}),
            signatures=Counter({"pass": 75, "AssertionError at t.py:1": 25}),
            wall_s=9.0,
            workers=8,
            strategy="spawn",
        )
        defaults.update(kwargs)
        return BatchReport(**defaults)

    def test_flake_rate(self) -> None:
        assert self._report().flake_rate == 0.25

    def test_error_runs_make_the_measurement_unsound(self) -> None:
        assert self._report().is_sound
        assert not self._report(errors=3).is_sound

    def test_distinct_signatures_excludes_passes(self) -> None:
        assert self._report().distinct_signatures == 1

    def test_zero_runs_does_not_divide_by_zero(self) -> None:
        assert self._report(runs=0, failures=0).flake_rate == 0.0

    def test_summary_flags_unsound_measurements(self) -> None:
        assert "UNSOUND" in self._report(errors=2).summary()
        assert "UNSOUND" not in self._report().summary()
