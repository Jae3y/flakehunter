"""Unit tests for the sandbox executor.

These require the sandbox marker, so they run inside the container:

    docker compose run --rm flakehunter python -m pytest tests -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.sandbox.executor import (
    Outcome,
    ResourceLimits,
    SandboxExecutor,
    Strategy,
    classify,
    strategy_preserves_hash_order,
)
from src.telemetry.tracer import Tracer

SMOKE_PROJECT = Path(__file__).resolve().parent.parent / "corpus" / "case_00_smoke" / "project"


@pytest.fixture()
def executor(tmp_path: Path) -> SandboxExecutor:
    """An executor with a short timeout and per-run tracing disabled."""
    return SandboxExecutor(
        tracer=Tracer(trace_dir=tmp_path / "traces"),
        limits=ResourceLimits(wall_clock_s=5.0, cpu_seconds=5),
        scratch_root=tmp_path / "scratch",
        trace_each_run=False,
    )


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "expected"),
    [
        (0, False, Outcome.PASS),
        (1, False, Outcome.FAIL),
        (-9, False, Outcome.FAIL),  # killed by a signal we did not send
        (None, True, Outcome.TIMEOUT),
        (2, False, Outcome.ERROR),  # interrupted
        (4, False, Outcome.ERROR),  # usage error
        (5, False, Outcome.ERROR),  # nothing collected -- must not read as PASS
    ],
)
def test_exit_codes_map_to_outcomes(
    exit_code: int | None, timed_out: bool, expected: Outcome
) -> None:
    assert classify(exit_code, timed_out) is expected


def test_passing_test_is_reported_pass(executor: SandboxExecutor) -> None:
    result = executor.run_once(SMOKE_PROJECT, ["test_pass.py"])
    assert result.outcome is Outcome.PASS
    assert not result.failed


def test_failing_test_is_reported_fail_with_output(executor: SandboxExecutor) -> None:
    result = executor.run_once(SMOKE_PROJECT, ["test_fail.py"])
    assert result.outcome is Outcome.FAIL
    assert result.failed
    assert "deliberate failure" in result.stdout


def test_missing_target_is_error_not_pass(executor: SandboxExecutor) -> None:
    """pytest exits 5 when it collects nothing; that is a broken measurement."""
    result = executor.run_once(SMOKE_PROJECT, ["test_does_not_exist.py"])
    assert result.outcome is Outcome.ERROR


def test_hanging_test_times_out_and_is_killed(executor: SandboxExecutor) -> None:
    result = executor.run_once(SMOKE_PROJECT, ["test_hang.py"])
    assert result.outcome is Outcome.TIMEOUT
    assert result.duration_ms < executor.limits.wall_clock_s * 3000


def test_runs_do_not_mutate_the_corpus(executor: SandboxExecutor) -> None:
    """The pristine case must survive execution untouched."""
    before = sorted(p.name for p in SMOKE_PROJECT.iterdir())
    executor.run_once(SMOKE_PROJECT, ["test_pass.py"])
    assert sorted(p.name for p in SMOKE_PROJECT.iterdir()) == before


def test_scratch_workdir_is_removed_after_each_run(executor: SandboxExecutor) -> None:
    """Only the per-case staging area survives a run; workdirs do not."""
    executor.run_once(SMOKE_PROJECT, ["test_pass.py"])
    leftovers = [p.name for p in executor.scratch_root.iterdir() if p.name != "stage"]
    assert leftovers == []


def test_staging_is_reused_across_runs(executor: SandboxExecutor) -> None:
    """The expensive bind-mount copy must happen once, not once per run."""
    first = executor.stage(SMOKE_PROJECT)
    executor.run_once(SMOKE_PROJECT, ["test_pass.py"])
    assert executor.stage(SMOKE_PROJECT) == first


def test_clear_stage_forces_a_fresh_copy(executor: SandboxExecutor) -> None:
    """The patcher relies on this to avoid running against stale source."""
    first = executor.stage(SMOKE_PROJECT)
    executor.clear_stage(SMOKE_PROJECT)
    assert executor.stage(SMOKE_PROJECT) != first


def test_api_credentials_are_not_visible_to_a_test_run(
    executor: SandboxExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent-authored code must never be able to read the orchestrator's key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    env = executor._build_env(None)
    assert "ANTHROPIC_API_KEY" not in env


def test_env_overrides_reach_the_run(executor: SandboxExecutor) -> None:
    """The EXPERIMENT phase pins variables this way; prove the channel works."""
    result = executor.run_once(
        SMOKE_PROJECT,
        ["test_hashorder.py"],
        env_overrides={"PYTHONHASHSEED": "0"},
    )
    assert result.outcome in (Outcome.PASS, Outcome.FAIL)


def test_fork_strategy_executes(executor: SandboxExecutor) -> None:
    result = executor.run_once(SMOKE_PROJECT, ["test_pass.py"], strategy=Strategy.FORK)
    assert result.outcome is Outcome.PASS
    assert result.strategy is Strategy.FORK


def test_only_spawn_claims_hash_order_fidelity() -> None:
    assert strategy_preserves_hash_order(Strategy.SPAWN)
    assert not strategy_preserves_hash_order(Strategy.FORK)
