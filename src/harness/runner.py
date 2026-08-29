"""Run a test many times and report what actually happened.

This is the component the baseline does not get. One LLM call can read a flaky
test and guess; only repeated execution can say *how often* it fails and *in
how many distinct ways* -- and that distinction is the whole thesis of the
project.

Two things are produced per batch:

**A flake rate.** Failures over runs, with the outcome breakdown kept
separate so a misconfigured measurement (``ERROR``) can never masquerade as a
clean one (``PASS``).

**Failure signatures.** The distinct ways the test failed. One signature means
one bug; three means either three bugs or a signature grouping that is too
loose. This is the evidence that drives HYPOTHESIZE, so its quality matters
more than the flake rate's third decimal place.

### On tracing granularity

The agent never calls :meth:`SandboxExecutor.run_once`; it asks for "run this
500 times and tell me the failure rate". That batch is the tool, so the batch
is what gets a trajectory turn. Emitting 500 turns per measurement would be
affordable on disk (722 bytes each, measured at Phase 0) but would bury the
instructions, reflections and human checkpoints that make a trajectory
readable. Per-run tracing stays available via the executor's
``trace_each_run`` flag for debugging.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from src.sandbox.executor import (
    ExecutionResult,
    Outcome,
    SandboxExecutor,
    Strategy,
)
from src.telemetry.tracer import Tracer

__all__ = [
    "DEFAULT_WORKERS",
    "BatchReport",
    "TestRunner",
    "extract_signature",
    "normalise_message",
]

#: Default worker count. Phase 0 measured 5.7-5.8x throughput at 8 workers on
#: 16 cores with a hash-order flake-rate drift of 0.0-0.067 -- inside binomial
#: noise. Timing-sensitive case classes are expected to need less; see
#: ``docs/CHANGELOG.md`` for the policy.
DEFAULT_WORKERS = 8

#: Pytest's ``--tb=line`` emits ``<path>:<line>: <detail>``.
_TRACEBACK_LINE = re.compile(r"^(?P<path>\S+):(?P<line>\d+):\s*(?P<detail>.+)$")

#: ``detail`` takes two shapes: ``ExcType: message`` when the assertion carried
#: a message or the failure was an exception, and a bare rewritten assert
#: expression (``assert 390666 == (8 * 50000)``) when it did not.
_EXC_DETAIL = re.compile(r"^(?P<exc>[A-Za-z_][\w.]*)(?::\s*(?P<msg>.*))?$", re.DOTALL)

#: Volatile substrings that differ between runs without indicating a different
#: bug: scratch workdirs, object addresses, pids and thread ids.
_VOLATILE = (
    (re.compile(r"/scratch/[^\s'\"]+"), "<workdir>"),
    (re.compile(r"0x[0-9a-fA-F]{6,}"), "<addr>"),
    (re.compile(r"\b(?:pid|PID)[= ]\d+"), "pid=<pid>"),
    (re.compile(r"\bThread-\d+"), "Thread-<n>"),
)

#: Observed values inside a failure message. These are what *differs* between
#: two runs of the same bug, so they are collapsed; see
#: :func:`normalise_message` for the measurement that justified this.
_OBSERVED_VALUES = (
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<str>"),
    (re.compile(r"\[[^\[\]]*\]"), "<seq>"),
    (re.compile(r"\b\d+\.\d+\b"), "<f>"),
    (re.compile(r"\b\d+\b"), "<n>"),
)


def normalise_message(message: str) -> str:
    """Reduce a failure message to something groupable across runs.

    This is the knob that decides whether failure signatures are useful.

    Too aggressive -- normalising every number, say -- and two genuinely
    different bugs collapse into one signature, so the agent forms a single
    hypothesis for two root causes and its fix verifies clean on only one of
    them. Too loose, and 500 runs of one bug produce 500 "distinct"
    signatures, which tells the agent nothing at all.

    Phase 1 measurement settled where to draw the line. Keeping observed values
    verbatim produced one signature *per run* for the race case
    (``assert 390666 == (8 * 50000)``, a different total every time) and ten
    signatures for a single bug in the sharding case (``an idle shard:
    [3, 3, 0]``). That is the too-loose failure mode: the agent sees hundreds
    of "distinct" failures and learns nothing about how many bugs it faces.

    So observed values -- numbers, quoted strings, bracketed sequences -- are
    collapsed to placeholders, while the exception type, the failing location
    and the *structure* of the assertion are all preserved. Two genuinely
    different bugs would have to share a file, a line, an exception type and an
    assertion shape to be merged, and would then differ only in a literal
    value.

    Args:
        message: The detail from a pytest one-line traceback.

    Returns:
        The message with volatile and observed substrings replaced.
    """
    normalised = message.strip()
    for pattern, placeholder in _VOLATILE:
        normalised = pattern.sub(placeholder, normalised)
    for pattern, placeholder in _OBSERVED_VALUES:
        normalised = pattern.sub(placeholder, normalised)
    return normalised[:200]


def extract_signature(result: ExecutionResult) -> str:
    """Summarise *how* a run failed, stably enough to group across runs.

    Args:
        result: A completed run.

    Returns:
        A short signature string. Passing runs return ``"pass"``.
    """
    if result.outcome is Outcome.PASS:
        return "pass"
    if result.outcome is Outcome.TIMEOUT:
        return "timeout: exceeded wall clock"
    if result.outcome is Outcome.ERROR:
        return f"error: pytest exit {result.exit_code}"

    # Walk backwards: the last matching traceback line is the failing frame.
    for line in reversed((result.stdout + "\n" + result.stderr).splitlines()):
        match = _TRACEBACK_LINE.match(line.strip())
        if not match:
            continue
        location = f"{Path(match['path']).name}:{match['line']}"
        detail = match["detail"].strip()

        exc_match = _EXC_DETAIL.fullmatch(detail)
        if exc_match:
            exception = exc_match["exc"]
            message = normalise_message(exc_match["msg"] or "")
        else:
            # A rewritten assert expression, with no exception name of its own.
            exception = "AssertionError"
            message = normalise_message(detail)

        return f"{exception} at {location}" + (f": {message}" if message else "")
    return "fail: unparsed pytest output"


@dataclass(slots=True)
class BatchReport:
    """The result of running one test many times."""

    case: str
    runs: int
    failures: int
    errors: int
    outcomes: Counter[str]
    signatures: Counter[str]
    wall_s: float
    workers: int
    strategy: str
    env_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def flake_rate(self) -> float:
        """Failures over runs.

        ``ERROR`` runs are counted in the denominator but not the numerator:
        they mean the measurement is broken, and :attr:`is_sound` is how a
        caller finds out rather than reading a quietly deflated rate.
        """
        return self.failures / self.runs if self.runs else 0.0

    @property
    def is_sound(self) -> bool:
        """Whether this measurement can be trusted at all."""
        return self.errors == 0

    @property
    def distinct_signatures(self) -> int:
        """How many different ways the test failed."""
        return len([s for s in self.signatures if s != "pass"])

    @classmethod
    def from_dict(cls, payload: dict) -> "BatchReport":
        """Rebuild a report from its serialised form.

        Used when resuming a case from a checkpoint: a CONFIRM measurement
        costs no API requests but does cost minutes of CPU, so it is worth
        restoring rather than repeating.
        """
        return cls(
            case=payload["case"],
            runs=payload["runs"],
            failures=payload["failures"],
            errors=payload.get("errors", 0),
            outcomes=Counter(payload.get("outcomes", {})),
            signatures=Counter(payload.get("signatures", {})),
            wall_s=payload.get("wall_s", 0.0),
            workers=payload.get("workers", 1),
            strategy=payload.get("strategy", "spawn"),
            env_overrides=payload.get("env_overrides", {}),
        )

    def summary(self) -> str:
        """One-line human-readable summary."""
        parts = [
            f"{self.case}: {self.failures}/{self.runs} failed "
            f"({self.flake_rate:.1%}) in {self.wall_s:.1f}s "
            f"at {self.workers} worker(s)"
        ]
        if not self.is_sound:
            parts.append(f" [UNSOUND: {self.errors} error runs]")
        return "".join(parts)

    def signature_table(self, limit: int = 5) -> str:
        """The top failure signatures, most frequent first."""
        rows = [(s, n) for s, n in self.signatures.most_common() if s != "pass"]
        if not rows:
            return "  (no failures)"
        lines = [f"  {n:>4}x  {sig}" for sig, n in rows[:limit]]
        if len(rows) > limit:
            lines.append(f"  ... and {len(rows) - limit} more distinct signature(s)")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        """Serialise for ``results/`` and for the trajectory."""
        return {
            "case": self.case,
            "runs": self.runs,
            "failures": self.failures,
            "errors": self.errors,
            "flake_rate": round(self.flake_rate, 4),
            "is_sound": self.is_sound,
            "outcomes": dict(self.outcomes),
            "signatures": dict(self.signatures),
            "distinct_signatures": self.distinct_signatures,
            "wall_s": round(self.wall_s, 2),
            "workers": self.workers,
            "strategy": self.strategy,
            "env_overrides": self.env_overrides,
        }


class TestRunner:
    """Runs a case's test many times and reports the aggregate."""

    def __init__(self, executor: SandboxExecutor, tracer: Tracer) -> None:
        self.executor = executor
        self.tracer = tracer

    def measure(
        self,
        project_dir: str | Path,
        runs: int,
        pytest_args: Sequence[str] = (),
        env_overrides: Mapping[str, str] | None = None,
        workers: int = DEFAULT_WORKERS,
        strategy: Strategy = Strategy.SPAWN,
        case_name: str | None = None,
        agent_name: str = "harness.runner",
    ) -> BatchReport:
        """Run the case's tests ``runs`` times and summarise the outcomes.

        Args:
            project_dir: The case's ``project/`` directory.
            runs: How many times to execute. 500 for baseline and final
                verification; fewer is appropriate for CONFIRM and EXPERIMENT.
            pytest_args: Extra pytest arguments, e.g. a single node id.
            env_overrides: Environment pinned for every run in the batch. This
                is how EXPERIMENT tests a hypothesis -- fix ``PYTHONHASHSEED``,
                pin ``TZ`` -- without editing any source.
            workers: Concurrent runs. Baseline and agent measurements of the
                same case must always use the same value.
            strategy: Execution strategy. ``FORK`` is unsafe for cases whose
                nondeterminism is inter-process; see
                ``strategy_preserves_hash_order``.
            case_name: Label for reports. Defaults to the directory name.
            agent_name: Which phase requested this batch, for the trajectory.

        Returns:
            The aggregated report.
        """
        project = Path(project_dir)
        name = case_name or project.parent.name
        # Pay the bind-mount copy once for the whole batch, not once per run.
        self.executor.stage(project)

        args = ["--tb=line", *pytest_args]
        started = time.perf_counter()

        def one_run(_: int) -> ExecutionResult:
            return self.executor.run_once(
                project,
                pytest_args=args,
                env_overrides=env_overrides,
                strategy=strategy,
            )

        if workers <= 1:
            results = [one_run(i) for i in range(runs)]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(one_run, range(runs)))
        wall_s = time.perf_counter() - started

        outcomes: Counter[str] = Counter(r.outcome.value for r in results)
        signatures: Counter[str] = Counter(extract_signature(r) for r in results)
        report = BatchReport(
            case=name,
            runs=runs,
            failures=sum(1 for r in results if r.failed),
            errors=outcomes.get(Outcome.ERROR.value, 0),
            outcomes=outcomes,
            signatures=signatures,
            wall_s=wall_s,
            workers=workers,
            strategy=strategy.value,
            env_overrides=dict(env_overrides or {}),
        )
        self._trace(report, args, agent_name)
        return report

    def _trace(
        self, report: BatchReport, args: Sequence[str], agent_name: str
    ) -> None:
        """Emit one trajectory turn for the whole batch."""
        with self.tracer.turn(
            agent_name=agent_name,
            model="n/a",
            instruction=(
                f"Run {report.case} {report.runs} times and report the "
                "empirical failure rate and the distinct failure signatures."
            ),
        ) as turn:
            turn.call(
                "run_batch",
                case=report.case,
                runs=report.runs,
                pytest_args=list(args),
                env_overrides=report.env_overrides,
                workers=report.workers,
                strategy=report.strategy,
            )
            turn.respond(
                stdout=report.summary() + "\n" + report.signature_table(),
                exit_code=0 if report.is_sound else 1,
                duration_ms=int(report.wall_s * 1000),
            )
            turn.reflect(
                f"flake_rate={report.flake_rate:.4f} over {report.runs} runs; "
                f"{report.distinct_signatures} distinct failure signature(s); "
                f"sound={report.is_sound}"
            )
