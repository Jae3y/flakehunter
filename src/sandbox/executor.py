"""Containerised execution of a single test run.

The container is the isolation boundary: the orchestrator, the agent, and every
execution of agent-authored code all live inside it, and nothing reaches the
host except through the mounted ``results/`` directory after human approval.
This module enforces that boundary in code -- it refuses to execute anything
unless the sandbox marker written by the Dockerfile is present.

Within the container, one test run is one short-lived child process in a fresh
scratch workdir, under a wall-clock timeout and hard resource limits.

Two execution strategies are provided:

``SPAWN``
    A full ``python -m pytest`` subprocess. Correct for every case in the
    corpus, including those whose nondeterminism lives *between* interpreters
    (hash seed, module import order), at roughly 300 ms per run.

``FORK``
    ``os.fork()`` from a parent that has already imported pytest but not the
    code under test. Each child gets a private copy-on-write image, so global
    state still cannot leak between runs, at roughly a tenth of the cost.
    The catch is that a forked child inherits the parent's hash seed, so
    ``PYTHONHASHSEED``-driven flakiness is invisible under this strategy.
    :func:`strategy_preserves_hash_order` states that limitation in code so a
    caller cannot pick the fast path for a case it would silently break.
"""

from __future__ import annotations

import enum
import os
import resource
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from src.telemetry.tracer import Tracer

__all__ = [
    "Outcome",
    "Strategy",
    "ResourceLimits",
    "ExecutionResult",
    "SandboxExecutor",
    "SandboxViolation",
    "assert_sandboxed",
    "strategy_preserves_hash_order",
]

#: Written by the Dockerfile. Its absence means we are not in the sandbox.
SANDBOX_MARKER = Path("/.flakehunter-sandbox")

#: Escape hatch for running the unit tests on a developer machine. Never set
#: this when executing agent-authored code.
UNSANDBOXED_ENV_VAR = "FLAKEHUNTER_ALLOW_UNSANDBOXED"

#: Captured stream ceiling. A pathological test can emit unbounded output;
#: 26k runs of unbounded output would exhaust the tmpfs.
MAX_CAPTURED_BYTES = 64 * 1024


class SandboxViolation(RuntimeError):
    """Raised when execution was attempted outside the sandbox."""


class Outcome(str, enum.Enum):
    """The result of a single test run.

    ``ERROR`` is deliberately distinct from ``FAIL``: a run that never
    collected a test tells us the harness is misconfigured, and folding that
    into the flake rate would let a broken measurement look like a clean one.
    """

    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    ERROR = "error"


class Strategy(str, enum.Enum):
    """How a single run is started."""

    SPAWN = "spawn"
    FORK = "fork"


def strategy_preserves_hash_order(strategy: Strategy) -> bool:
    """Whether ``strategy`` gives each run an independent ``PYTHONHASHSEED``.

    Only :attr:`Strategy.SPAWN` does. A forked child inherits the parent's
    already-initialised hash seed, so set and dict iteration order is frozen
    across every run in the batch -- which would make a hash-order-dependent
    test look either perfectly reliable or perfectly broken.
    """
    return strategy is Strategy.SPAWN


def assert_sandboxed() -> None:
    """Raise unless we are executing inside the FlakeHunter container.

    Raises:
        SandboxViolation: If the marker is absent and the escape hatch is unset.
    """
    if SANDBOX_MARKER.exists():
        return
    if os.environ.get(UNSANDBOXED_ENV_VAR) == "1":
        print(
            f"WARNING: {UNSANDBOXED_ENV_VAR}=1 -- executing outside the sandbox.",
            file=sys.stderr,
        )
        return
    raise SandboxViolation(
        f"refusing to execute: sandbox marker {SANDBOX_MARKER} not found. "
        "Run inside the container: docker compose run --rm flakehunter ..."
    )


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Hard caps applied to a single run.

    Attributes:
        wall_clock_s: Timeout enforced by the parent, after which the run's
            whole process group is killed.
        cpu_seconds: ``RLIMIT_CPU``. Catches a spin loop that a wall-clock
            timeout would also catch, but sooner and more cheaply.
        address_space_mb: ``RLIMIT_AS``. Stops a runaway allocation from
            pushing the container into the OOM killer and taking the
            orchestrator down with it.
        max_open_files: ``RLIMIT_NOFILE``. Relevant to the unclosed-resource
            case, which leaks descriptors by construction.
    """

    wall_clock_s: float = 15.0
    cpu_seconds: int = 10
    address_space_mb: int = 512
    max_open_files: int = 256

    @classmethod
    def from_env(cls) -> "ResourceLimits":
        """Build limits from the environment, falling back to the defaults."""
        return cls(
            wall_clock_s=float(os.environ.get("FLAKEHUNTER_RUN_TIMEOUT_S", 15.0)),
            address_space_mb=int(os.environ.get("FLAKEHUNTER_RUN_MEMORY_MB", 512)),
        )

    def apply(self) -> None:
        """Apply these limits to the current process.

        Called in the child, after fork and before exec, so the limits bind to
        the test run and never to the orchestrator.
        """
        resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_seconds, self.cpu_seconds))
        limit_bytes = self.address_space_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
        resource.setrlimit(
            resource.RLIMIT_NOFILE, (self.max_open_files, self.max_open_files)
        )
        # No core dumps: 26k runs of a crashing test would fill the tmpfs.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Everything observed about one test run."""

    outcome: Outcome
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    strategy: Strategy

    @property
    def failed(self) -> bool:
        """Whether this run counts as a flake observation."""
        return self.outcome in (Outcome.FAIL, Outcome.TIMEOUT)


def classify(exit_code: int | None, timed_out: bool) -> Outcome:
    """Map a pytest exit status onto an :class:`Outcome`.

    The mapping follows pytest's documented exit codes. The one that matters
    is 5 (no tests collected): it means we pointed the harness at nothing, and
    treating it as a pass would report a perfect flake rate for a test that
    never ran.
    """
    if timed_out:
        return Outcome.TIMEOUT
    if exit_code is None:
        return Outcome.ERROR
    if exit_code == 0:
        return Outcome.PASS
    if exit_code == 1:
        return Outcome.FAIL
    if exit_code < 0:
        # Killed by a signal we did not send: a segfault or abort. The test
        # did not pass, and for flake accounting that is a failure.
        return Outcome.FAIL
    # 2 interrupted, 3 internal error, 4 usage error, 5 nothing collected.
    return Outcome.ERROR


def _truncate(raw: bytes) -> str:
    """Decode captured output, capping it at :data:`MAX_CAPTURED_BYTES`."""
    if len(raw) > MAX_CAPTURED_BYTES:
        kept = raw[:MAX_CAPTURED_BYTES]
        suffix = f"\n...[truncated {len(raw) - MAX_CAPTURED_BYTES} bytes]"
        return kept.decode("utf-8", "replace") + suffix
    return raw.decode("utf-8", "replace")


class SandboxExecutor:
    """Runs one test at a time inside the sandbox.

    Args:
        tracer: Trajectory writer. Every run emits a turn when
            ``trace_each_run`` is set.
        limits: Resource caps applied to each run.
        scratch_root: Where per-run workdirs are created. Defaults to the
            RAM-backed ``/scratch`` tmpfs from docker-compose.
        trace_each_run: Whether to emit one trajectory turn per individual
            run. True is the literal reading of "every tool execution routes
            through the tracer"; a 500-run batch then produces 500 records.
            Phase 1 revisits this once the benchmark reports real file sizes.
    """

    def __init__(
        self,
        tracer: Tracer,
        limits: ResourceLimits | None = None,
        scratch_root: str | os.PathLike[str] | None = None,
        trace_each_run: bool = True,
    ) -> None:
        assert_sandboxed()
        self.tracer = tracer
        self.limits = limits or ResourceLimits.from_env()
        self.scratch_root = Path(
            scratch_root or os.environ.get("FLAKEHUNTER_SCRATCH", tempfile.gettempdir())
        )
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.trace_each_run = trace_each_run
        self._stage_root = self.scratch_root / "stage"
        self._stage_root.mkdir(parents=True, exist_ok=True)
        self._staged: dict[str, Path] = {}

    def stage(self, project_dir: str | os.PathLike[str]) -> Path:
        """Copy a case into tmpfs once, and return the staged path.

        Corpus cases live on a bind mount from the Windows host, where Docker
        Desktop charges roughly 127 ms to copy even a four-file directory --
        measured, and the single largest component of a run. The same copy
        from tmpfs to tmpfs costs 1.2 ms. Staging once per case turns that
        127 ms from a per-run cost into a per-batch one.

        The cache is keyed on path alone. A caller that rewrites a project in
        place -- the patcher, applying a fix -- must call :meth:`clear_stage`
        or stage under a fresh path, or runs will silently use stale source.
        """
        project = Path(project_dir).resolve()
        cached = self._staged.get(str(project))
        if cached is not None and cached.is_dir():
            return cached
        target = Path(tempfile.mkdtemp(prefix="stage-", dir=self._stage_root))
        staged = target / project.name
        shutil.copytree(project, staged)
        self._staged[str(project)] = staged
        return staged

    def clear_stage(self, project_dir: str | os.PathLike[str] | None = None) -> None:
        """Drop staged copies so the next run re-reads from source."""
        if project_dir is None:
            for staged in self._staged.values():
                shutil.rmtree(staged.parent, ignore_errors=True)
            self._staged.clear()
            return
        key = str(Path(project_dir).resolve())
        staged = self._staged.pop(key, None)
        if staged is not None:
            shutil.rmtree(staged.parent, ignore_errors=True)

    def run_once(
        self,
        project_dir: str | os.PathLike[str],
        pytest_args: Sequence[str] = (),
        env_overrides: Mapping[str, str] | None = None,
        strategy: Strategy = Strategy.SPAWN,
    ) -> ExecutionResult:
        """Execute the project's tests exactly once in a fresh workdir.

        Args:
            project_dir: A corpus case's ``project/`` directory, or a patched
                copy of one. Copied into scratch; never mutated.
            pytest_args: Extra arguments, e.g. a specific test node id.
            env_overrides: Environment entries for the run, used by the
                EXPERIMENT phase to pin the clock or fix the hash seed.
            strategy: How to start the run.

        Returns:
            The observed result of the run.
        """
        project = Path(project_dir).resolve()
        if not project.is_dir():
            raise FileNotFoundError(f"project directory not found: {project}")

        source = self.stage(project)
        started_ns = time.perf_counter_ns()
        workdir = Path(tempfile.mkdtemp(prefix="run-", dir=self.scratch_root))
        try:
            target = workdir / "project"
            shutil.copytree(source, target)
            env = self._build_env(env_overrides)
            args = self._pytest_args(pytest_args)
            if strategy is Strategy.SPAWN:
                result = self._run_spawn(target, args, env, started_ns)
            else:
                result = self._run_fork(target, args, env, started_ns)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        if self.trace_each_run:
            self._trace(project, args, strategy, result)
        return result

    def _pytest_args(self, extra: Sequence[str]) -> list[str]:
        """Base pytest arguments shared by both strategies.

        ``-p no:cacheprovider`` stops pytest writing ``.pytest_cache`` into the
        workdir, which would otherwise be one more piece of state differing
        between runs -- a source of flakiness we would have introduced.
        """
        return ["-q", "--no-header", "-p", "no:cacheprovider", *extra]

    def _build_env(self, overrides: Mapping[str, str] | None) -> dict[str, str]:
        """Assemble the child environment."""
        env = dict(os.environ)
        # The orchestrator's credentials must never be visible to a test run.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_BASE_URL", None)
        env["PYTHONDONTWRITEBYTECODE"] = "0"
        if overrides:
            env.update(overrides)
        return env

    def _run_spawn(
        self,
        workdir: Path,
        args: Sequence[str],
        env: Mapping[str, str],
        started_ns: int,
    ) -> ExecutionResult:
        """Run via a full ``python -m pytest`` subprocess."""
        command = [sys.executable, "-m", "pytest", *args]
        timed_out = False
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command,
            cwd=workdir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=self.limits.apply,
        )
        try:
            out, err = process.communicate(timeout=self.limits.wall_clock_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_group(process.pid)
            out, err = process.communicate()

        exit_code = None if timed_out else process.returncode
        return ExecutionResult(
            outcome=classify(exit_code, timed_out),
            exit_code=exit_code,
            stdout=_truncate(out or b""),
            stderr=_truncate(err or b""),
            duration_ms=(time.perf_counter_ns() - started_ns) // 1_000_000,
            strategy=Strategy.SPAWN,
        )

    def _run_fork(
        self,
        workdir: Path,
        args: Sequence[str],
        env: Mapping[str, str],
        started_ns: int,
    ) -> ExecutionResult:
        """Run via ``os.fork()`` from a pytest-warmed parent.

        The child's exit is detected by EOF on a pipe rather than by polling,
        so a 20 ms run is not charged a polling interval it did not need.
        """
        import pytest  # imported in the parent so the fork inherits it warm

        out_path = workdir.parent / "stdout"
        err_path = workdir.parent / "stderr"
        read_fd, write_fd = os.pipe()

        pid = os.fork()
        if pid == 0:  # child
            os.close(read_fd)
            try:
                os.setsid()
                os.chdir(workdir)
                os.environ.clear()
                os.environ.update(env)
                self.limits.apply()
                with open(out_path, "wb") as out, open(err_path, "wb") as err:
                    os.dup2(out.fileno(), 1)
                    os.dup2(err.fileno(), 2)
                    code = pytest.main(list(args))
                os._exit(int(code))
            except BaseException:  # noqa: BLE001 - the child must never unwind
                os._exit(70)

        os.close(write_fd)
        timed_out = False
        try:
            ready, _, _ = select.select([read_fd], [], [], self.limits.wall_clock_s)
            if not ready:
                timed_out = True
                self._kill_group(pid)
            _, status = os.waitpid(pid, 0)
        finally:
            os.close(read_fd)

        if timed_out:
            exit_code = None
        elif os.WIFSIGNALED(status):
            exit_code = -os.WTERMSIG(status)
        else:
            exit_code = os.WEXITSTATUS(status)

        return ExecutionResult(
            outcome=classify(exit_code, timed_out),
            exit_code=exit_code,
            stdout=_truncate(out_path.read_bytes() if out_path.exists() else b""),
            stderr=_truncate(err_path.read_bytes() if err_path.exists() else b""),
            duration_ms=(time.perf_counter_ns() - started_ns) // 1_000_000,
            strategy=Strategy.FORK,
        )

    @staticmethod
    def _kill_group(pid: int) -> None:
        """Kill a run's whole process group.

        Killing only the direct child would strand the threads, async tasks
        and servers that half the corpus creates by construction; those
        orphans would then perturb later runs in the same batch.
        """
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def _trace(
        self,
        project: Path,
        args: Sequence[str],
        strategy: Strategy,
        result: ExecutionResult,
    ) -> None:
        """Emit one trajectory turn describing this run."""
        with self.tracer.turn(
            agent_name="sandbox.executor",
            model="n/a",
            instruction=f"Execute {project.name} once under {strategy.value}.",
        ) as turn:
            turn.call(
                "run_once",
                project=str(project),
                pytest_args=list(args),
                strategy=strategy.value,
                limits=self.limits.wall_clock_s,
            )
            turn.respond(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code if result.exit_code is not None else -1,
                duration_ms=result.duration_ms,
            )
            turn.reflect(f"outcome={result.outcome.value}")
