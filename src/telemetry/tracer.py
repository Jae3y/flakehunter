"""JSONL trajectory capture for FlakeHunter.

Every LLM call and every tool execution in this project routes through this
module. A turn that is not traced is a bug, not an optimisation, so the public
API is a context manager: the record is written when the block exits, including
when it exits by exception. There is no way to "forget" to close a turn.

One file per run, at ``traces/<run_id>.jsonl``, one JSON object per line.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

__all__ = [
    "GAP_AGENT_NAME",
    "MAX_CONSECUTIVE_GAPS",
    "RECORD_FIELDS",
    "ToolCall",
    "ToolResponse",
    "HumanCheckpoint",
    "TokenUsage",
    "Turn",
    "Tracer",
    "TraceWriteError",
    "validate_record",
]

#: The exact field set required of every trajectory record, in order.
RECORD_FIELDS: tuple[str, ...] = (
    "turn_id",
    "run_id",
    "timestamp",
    "agent_name",
    "model",
    "instruction",
    "tool_call",
    "tool_response",
    "reflection",
    "human_checkpoint",
    "tokens",
)

#: Cost per million tokens, keyed by exact model identifier.
#:
#: Deliberately empty at Phase 0: there are no LLM calls yet, and inventing
#: rates would put fabricated numbers into the cost column of the results
#: table. Populated at Phase 2 from published pricing. Until a model appears
#: here, ``cost_usd`` is ``None`` -- unknown, not zero.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {}

#: Attempts made to persist one record before giving up on it.
MAX_WRITE_ATTEMPTS = 3

#: Base delay between write attempts; doubled each retry.
WRITE_RETRY_BACKOFF_S = 0.05

#: Consecutive gap markers tolerated before the run is halted. One blip is
#: noise; three in a row is a broken volume, and continuing would produce a
#: trajectory made mostly of holes.
MAX_CONSECUTIVE_GAPS = 3

#: ``agent_name`` used by synthetic gap markers, so they are greppable and
#: countable without adding a field the schema does not allow.
GAP_AGENT_NAME = "telemetry.gap"


class TraceWriteError(RuntimeError):
    """Raised when the trajectory can no longer be persisted at all."""


@dataclass(slots=True)
class ToolCall:
    """The invocation half of a tool use."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResponse:
    """The result half of a tool use.

    Mirrors the shape of a subprocess result because most FlakeHunter tools
    are process executions; non-process tools report ``exit_code=0`` and put
    their payload in ``stdout``.
    """

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int


@dataclass(slots=True)
class HumanCheckpoint:
    """A point where the run paused for a qualified human."""

    prompted: bool
    decision: str | None = None
    note: str | None = None


@dataclass(slots=True)
class TokenUsage:
    """Token spend for a single turn.

    ``cost_usd`` is ``None`` when the model has no entry in
    :data:`PRICING_USD_PER_MTOK`, which is honest about not knowing rather
    than reporting a misleading zero.
    """

    prompt: int = 0
    completion: int = 0
    cost_usd: float | None = None

    @classmethod
    def for_model(cls, model: str, prompt: int, completion: int) -> "TokenUsage":
        """Build usage with cost derived from the pricing table."""
        rates = PRICING_USD_PER_MTOK.get(model)
        if rates is None:
            return cls(prompt=prompt, completion=completion, cost_usd=None)
        prompt_rate, completion_rate = rates
        cost = (prompt * prompt_rate + completion * completion_rate) / 1_000_000
        return cls(prompt=prompt, completion=completion, cost_usd=round(cost, 6))


class Turn:
    """A single mutable trajectory record, in flight.

    Instances are handed out by :meth:`Tracer.turn` and serialised when that
    context manager exits. Setters return ``self`` so a turn can be annotated
    in one expression where that reads better.
    """

    __slots__ = (
        "turn_id",
        "run_id",
        "agent_name",
        "model",
        "instruction",
        "tool_call",
        "tool_response",
        "reflection",
        "human_checkpoint",
        "tokens",
        "_started_ns",
    )

    def __init__(
        self,
        turn_id: int,
        run_id: str,
        agent_name: str,
        model: str,
        instruction: str,
    ) -> None:
        self.turn_id = turn_id
        self.run_id = run_id
        self.agent_name = agent_name
        self.model = model
        self.instruction = instruction
        self.tool_call: ToolCall | None = None
        self.tool_response: ToolResponse | None = None
        self.reflection: str = ""
        self.human_checkpoint: HumanCheckpoint | None = None
        self.tokens = TokenUsage()
        self._started_ns = time.perf_counter_ns()

    @property
    def elapsed_ms(self) -> int:
        """Milliseconds since this turn was opened."""
        return (time.perf_counter_ns() - self._started_ns) // 1_000_000

    def call(self, name: str, **arguments: Any) -> "Turn":
        """Record the tool invocation for this turn."""
        self.tool_call = ToolCall(name=name, arguments=arguments)
        return self

    def respond(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        duration_ms: int | None = None,
    ) -> "Turn":
        """Record the tool result for this turn."""
        self.tool_response = ToolResponse(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=self.elapsed_ms if duration_ms is None else duration_ms,
        )
        return self

    def reflect(self, reflection: str) -> "Turn":
        """Record the reasoning about the tool result.

        This is the field that makes a trajectory legible: it is the evidence
        that the tool response actually shaped the next step.
        """
        self.reflection = reflection
        return self

    def checkpoint(
        self,
        *,
        prompted: bool,
        decision: str | None = None,
        note: str | None = None,
    ) -> "Turn":
        """Record that this turn paused for human review."""
        self.human_checkpoint = HumanCheckpoint(
            prompted=prompted, decision=decision, note=note
        )
        return self

    def spend(self, prompt: int, completion: int) -> "Turn":
        """Record token spend, deriving cost from this turn's model."""
        self.tokens = TokenUsage.for_model(self.model, prompt, completion)
        return self

    def to_record(self) -> dict[str, Any]:
        """Serialise to the exact schema required of a trajectory record."""
        return {
            "turn_id": self.turn_id,
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_name": self.agent_name,
            "model": self.model,
            "instruction": self.instruction,
            "tool_call": asdict(self.tool_call) if self.tool_call else None,
            "tool_response": asdict(self.tool_response) if self.tool_response else None,
            "reflection": self.reflection,
            "human_checkpoint": (
                asdict(self.human_checkpoint) if self.human_checkpoint else None
            ),
            "tokens": asdict(self.tokens),
        }


class Tracer:
    """Append-only JSONL trajectory writer for one run.

    Thread-safe: turn ids are allocated and lines appended under a lock, so a
    parallel harness cannot interleave partial records.
    """

    def __init__(
        self,
        trace_dir: str | os.PathLike[str] = "traces",
        run_id: str | None = None,
    ) -> None:
        self.run_id = run_id or str(uuid.uuid4())
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.trace_dir / f"{self.run_id}.jsonl"
        self._lock = threading.Lock()
        self._next_turn_id = 0
        self._consecutive_gaps = 0
        self._total_gaps = 0

    def __repr__(self) -> str:
        return f"<Tracer run_id={self.run_id} path={self.path}>"

    @property
    def consecutive_gaps(self) -> int:
        """Gap markers written since the last record that persisted cleanly."""
        return self._consecutive_gaps

    @property
    def total_gaps(self) -> int:
        """Gap markers written over the life of this run."""
        return self._total_gaps

    @contextmanager
    def turn(
        self,
        agent_name: str,
        model: str,
        instruction: str = "",
    ) -> Iterator[Turn]:
        """Open a traced turn; the record is written when the block exits.

        The record is written even if the block raises, with the exception
        folded into ``reflection``. A crashed turn is the most interesting
        kind of turn and must never be the one that goes missing.
        """
        with self._lock:
            turn_id = self._next_turn_id
            self._next_turn_id += 1

        turn = Turn(turn_id, self.run_id, agent_name, model, instruction)
        try:
            yield turn
        except BaseException as exc:
            note = f"[turn raised {type(exc).__name__}: {exc}]"
            turn.reflect(f"{turn.reflection}\n{note}".strip())
            self._write(turn.to_record())
            raise
        else:
            self._write(turn.to_record())

    def _append_line(self, line: str) -> None:
        """Append one serialised line, fsynced before returning.

        Raises:
            OSError: If the line could not be written to the trajectory file.
        """
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def _write(self, record: Mapping[str, Any]) -> None:
        """Persist one record, retrying transient failures.

        A write that fails is retried with a short exponential backoff. If it
        still fails, the record becomes a gap marker rather than vanishing --
        see :meth:`_record_gap` for why.
        """
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 - see below
            # Deliberately broad. Tool arguments are arbitrary objects, and
            # ``default=str`` runs user ``__repr__`` code that can raise
            # anything at all. Whatever it raises, the right answer is a gap
            # marker -- never an exception escaping the tracer and killing a
            # run that was otherwise fine.
            #
            # No retry: serialisation is deterministic, so the same object
            # fails identically every time and retrying only burns wall clock.
            self._record_gap(record, exc, attempts=1)
            return

        last_exc: OSError | None = None
        for attempt in range(MAX_WRITE_ATTEMPTS):
            try:
                self._append_line(line)
            except OSError as exc:
                last_exc = exc
                if attempt < MAX_WRITE_ATTEMPTS - 1:
                    time.sleep(WRITE_RETRY_BACKOFF_S * (2**attempt))
            else:
                with self._lock:
                    self._consecutive_gaps = 0
                return

        assert last_exc is not None
        self._record_gap(record, last_exc, attempts=MAX_WRITE_ATTEMPTS)

    def _record_gap(
        self,
        record: Mapping[str, Any],
        exc: BaseException,
        attempts: int,
    ) -> None:
        """Write a synthetic marker in place of a record that would not persist.

        The policy this implements: a trajectory with an honest hole in it beats
        both alternatives. Dropping the record silently would leave a gap in the
        ``turn_id`` sequence that a reader would mistake for a turn that never
        happened; halting on the first blip would let a transient disk error
        destroy a 40-minute verification run.

        So the gap is *recorded*. The marker occupies the failed record's
        ``turn_id`` and is itself schema-valid, which keeps the sequence
        contiguous and the file parseable.

        Escalation is the safety valve: three consecutive gaps is no longer a
        blip, and a trajectory made mostly of holes is not evidence of anything.

        Args:
            record: The record that could not be written.
            exc: The failure that prevented writing it.
            attempts: How many write attempts were made before giving up.

        Raises:
            TraceWriteError: After :data:`MAX_CONSECUTIVE_GAPS` gaps in a row.
        """
        with self._lock:
            self._consecutive_gaps += 1
            self._total_gaps += 1
            gaps = self._consecutive_gaps

        turn_id = record.get("turn_id")
        marker = {
            "turn_id": turn_id if isinstance(turn_id, int) else -1,
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_name": GAP_AGENT_NAME,
            "model": "n/a",
            "instruction": "",
            "tool_call": None,
            "tool_response": None,
            "reflection": (
                f"[GAP] turn {turn_id} could not be persisted after "
                f"{attempts} attempt(s): {type(exc).__name__}: {exc}"
            ),
            "human_checkpoint": None,
            "tokens": {"prompt": 0, "completion": 0, "cost_usd": None},
        }
        try:
            self._append_line(json.dumps(marker, ensure_ascii=False))
        except OSError:
            # Even the marker would not land. The counter still reflects the
            # gap, so escalation below remains correct.
            pass

        if gaps >= MAX_CONSECUTIVE_GAPS:
            raise TraceWriteError(
                f"{gaps} consecutive trajectory records could not be persisted "
                f"for run {self.run_id}; last failure: {exc}"
            ) from exc


def validate_record(record: Mapping[str, Any]) -> list[str]:
    """Check one trajectory record against the required schema.

    Returns a list of human-readable problems; an empty list means valid.
    Used by the Phase 0 gate to prove the tracer emits what the competition
    asks for, rather than something that merely looks similar.

    Args:
        record: A decoded JSONL line.

    Returns:
        Every schema problem found, so one call reports all of them.
    """
    problems: list[str] = []

    missing = [f for f in RECORD_FIELDS if f not in record]
    if missing:
        problems.append(f"missing fields: {', '.join(missing)}")
    extra = [k for k in record if k not in RECORD_FIELDS]
    if extra:
        problems.append(f"unexpected fields: {', '.join(extra)}")

    scalar_types: dict[str, type] = {
        "turn_id": int,
        "run_id": str,
        "timestamp": str,
        "agent_name": str,
        "model": str,
        "instruction": str,
        "reflection": str,
        "tokens": dict,
    }
    for name, expected in scalar_types.items():
        if name in record and not isinstance(record[name], expected):
            problems.append(
                f"{name}: expected {expected.__name__}, "
                f"got {type(record[name]).__name__}"
            )

    for name in ("tool_call", "tool_response", "human_checkpoint"):
        value = record.get(name, None)
        if name in record and value is not None and not isinstance(value, dict):
            problems.append(f"{name}: expected object or null")

    timestamp = record.get("timestamp")
    if isinstance(timestamp, str):
        try:
            datetime.fromisoformat(timestamp)
        except ValueError:
            problems.append("timestamp: not ISO 8601")

    tokens = record.get("tokens")
    if isinstance(tokens, dict):
        for key in ("prompt", "completion", "cost_usd"):
            if key not in tokens:
                problems.append(f"tokens.{key}: missing")

    return problems
