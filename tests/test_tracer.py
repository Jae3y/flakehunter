"""Unit tests for the trajectory tracer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.telemetry.tracer import (
    GAP_AGENT_NAME,
    MAX_CONSECUTIVE_GAPS,
    PRICING_USD_PER_MTOK,
    RECORD_FIELDS,
    Tracer,
    TokenUsage,
    TraceWriteError,
    validate_record,
)


class Unserialisable:
    """A payload json.dumps cannot render, even through ``default=str``."""

    def __repr__(self) -> str:
        raise RuntimeError("cannot repr")


def _write_unpersistable(tracer: Tracer) -> None:
    """Emit one turn whose record cannot be serialised."""
    with tracer.turn("agent", "m", "unserialisable payload") as turn:
        turn.call("bad", payload=Unserialisable())


def _records(tracer: Tracer) -> list[dict]:
    """Read back every record written by ``tracer``."""
    text = tracer.path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines()] if text else []


def test_record_carries_exactly_the_required_fields(tmp_path: Path) -> None:
    tracer = Tracer(trace_dir=tmp_path)
    with tracer.turn("agent", "claude-opus-5", "do the thing") as turn:
        turn.call("run_once", project="case_00").respond(stdout="ok", exit_code=0)
        turn.reflect("looks fine")

    (record,) = _records(tracer)
    assert tuple(record) == RECORD_FIELDS
    assert validate_record(record) == []


def test_optional_objects_are_null_not_absent(tmp_path: Path) -> None:
    """A turn with no tool call must still carry the key, set to null."""
    tracer = Tracer(trace_dir=tmp_path)
    with tracer.turn("agent", "claude-opus-5", "think only"):
        pass

    (record,) = _records(tracer)
    assert record["tool_call"] is None
    assert record["tool_response"] is None
    assert record["human_checkpoint"] is None
    assert validate_record(record) == []


def test_turn_ids_are_monotonic_within_a_run(tmp_path: Path) -> None:
    tracer = Tracer(trace_dir=tmp_path)
    for index in range(5):
        with tracer.turn("agent", "m", f"turn {index}"):
            pass

    assert [r["turn_id"] for r in _records(tracer)] == [0, 1, 2, 3, 4]


def test_a_raising_turn_is_still_written(tmp_path: Path) -> None:
    """The most interesting turn must never be the missing one."""
    tracer = Tracer(trace_dir=tmp_path)
    with pytest.raises(ValueError):
        with tracer.turn("agent", "m", "will explode") as turn:
            turn.call("explode")
            raise ValueError("boom")

    (record,) = _records(tracer)
    assert "ValueError" in record["reflection"]
    assert "boom" in record["reflection"]
    assert validate_record(record) == []


def test_human_checkpoint_round_trips(tmp_path: Path) -> None:
    tracer = Tracer(trace_dir=tmp_path)
    with tracer.turn("orchestrator", "m", "seek approval") as turn:
        turn.checkpoint(prompted=True, decision="rejected", note="masks the symptom")

    (record,) = _records(tracer)
    assert record["human_checkpoint"] == {
        "prompted": True,
        "decision": "rejected",
        "note": "masks the symptom",
    }


def test_unknown_model_reports_unknown_cost_not_zero() -> None:
    """Reporting 0.00 for an unpriced model would understate spend silently."""
    usage = TokenUsage.for_model("not-a-real-model", prompt=1000, completion=500)
    assert usage.cost_usd is None


def test_known_model_cost_is_derived_from_the_pricing_table() -> None:
    PRICING_USD_PER_MTOK["test-model"] = (3.0, 15.0)
    try:
        usage = TokenUsage.for_model("test-model", prompt=1_000_000, completion=100_000)
        assert usage.cost_usd == pytest.approx(3.0 + 1.5)
    finally:
        del PRICING_USD_PER_MTOK["test-model"]


def test_a_single_unpersistable_record_becomes_a_gap_not_a_crash(
    tmp_path: Path,
) -> None:
    """One blip must not kill a 500-run verification loop."""
    tracer = Tracer(trace_dir=tmp_path)
    _write_unpersistable(tracer)

    (record,) = _records(tracer)
    assert record["agent_name"] == GAP_AGENT_NAME
    assert "[GAP]" in record["reflection"]
    assert tracer.consecutive_gaps == 1
    assert tracer.total_gaps == 1


def test_gap_markers_are_schema_valid(tmp_path: Path) -> None:
    """A malformed marker would make the whole trajectory unparseable."""
    tracer = Tracer(trace_dir=tmp_path)
    _write_unpersistable(tracer)

    (record,) = _records(tracer)
    assert validate_record(record) == []
    assert tuple(record) == RECORD_FIELDS


def test_gap_marker_keeps_the_turn_id_sequence_contiguous(tmp_path: Path) -> None:
    """A hole in the sequence would read as a turn that never happened."""
    tracer = Tracer(trace_dir=tmp_path)
    with tracer.turn("agent", "m", "first"):
        pass
    _write_unpersistable(tracer)
    with tracer.turn("agent", "m", "third"):
        pass

    assert [r["turn_id"] for r in _records(tracer)] == [0, 1, 2]


def test_a_successful_write_resets_the_gap_streak(tmp_path: Path) -> None:
    """Scattered blips must not accumulate into a spurious halt."""
    tracer = Tracer(trace_dir=tmp_path)
    for _ in range(MAX_CONSECUTIVE_GAPS * 2):
        _write_unpersistable(tracer)
        assert tracer.consecutive_gaps == 1
        with tracer.turn("agent", "m", "healthy"):
            pass
        assert tracer.consecutive_gaps == 0

    assert tracer.total_gaps == MAX_CONSECUTIVE_GAPS * 2


def test_consecutive_gaps_escalate_to_a_hard_failure(tmp_path: Path) -> None:
    """A volume that is genuinely broken must stop the run."""
    tracer = Tracer(trace_dir=tmp_path)
    for _ in range(MAX_CONSECUTIVE_GAPS - 1):
        _write_unpersistable(tracer)

    with pytest.raises(TraceWriteError, match="consecutive"):
        _write_unpersistable(tracer)

    assert tracer.consecutive_gaps == MAX_CONSECUTIVE_GAPS
    # The escalating record is still recorded before the raise.
    assert len(_records(tracer)) == MAX_CONSECUTIVE_GAPS


def test_transient_write_failure_is_retried(tmp_path: Path, monkeypatch) -> None:
    """Two failures then success must yield a real record, not a gap."""
    tracer = Tracer(trace_dir=tmp_path)
    real_append = tracer._append_line
    attempts = {"n": 0}

    def flaky_append(line: str) -> None:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise OSError("transient")
        real_append(line)

    monkeypatch.setattr(tracer, "_append_line", flaky_append)
    with tracer.turn("agent", "m", "survives a blip") as turn:
        turn.reflect("ok")

    (record,) = _records(tracer)
    assert record["agent_name"] == "agent"
    assert attempts["n"] == 3
    assert tracer.total_gaps == 0


def test_validate_record_reports_every_problem_at_once() -> None:
    problems = validate_record({"turn_id": "not-an-int", "run_id": "r"})
    assert any("missing fields" in p for p in problems)
    assert any("turn_id" in p for p in problems)
