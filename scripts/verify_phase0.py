"""Phase 0 verification: demonstrate the safety properties, do not assert them.

Each check prints the evidence it rests on, so the output can be read rather
than trusted.

    docker compose run --rm flakehunter python scripts/verify_phase0.py tracer
    docker compose run --rm flakehunter python scripts/verify_phase0.py gaps
    docker compose run --rm flakehunter python scripts/verify_phase0.py isolation
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sandbox.executor import SandboxExecutor, assert_sandboxed  # noqa: E402
from src.telemetry.tracer import (  # noqa: E402
    GAP_AGENT_NAME,
    MAX_CONSECUTIVE_GAPS,
    MAX_WRITE_ATTEMPTS,
    Tracer,
    TraceWriteError,
)

SEPARATOR = "=" * 78


def banner(title: str) -> None:
    """Print a section header."""
    print(f"\n{SEPARATOR}\n{title}\n{SEPARATOR}")


def demo_tracer() -> int:
    """Write a few ordinary turns and show the file they produced."""
    banner("1. TRACER: a trivial run, and the JSONL it wrote")

    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id="verify-tracer")
    if tracer.path.exists():
        tracer.path.unlink()

    with tracer.turn("demo.agent", "claude-opus-5", "Count the corpus cases.") as turn:
        turn.call("list_dir", path="corpus")
        turn.respond(stdout="13 entries", exit_code=0)
        turn.reflect("Twelve evaluation cases plus the smoke case.")
        turn.spend(prompt=412, completion=88)

    with tracer.turn("demo.agent", "n/a", "Run the smoke test once.") as turn:
        turn.call("run_once", project="corpus/case_00_smoke/project")
        turn.respond(stdout="1 passed", exit_code=0, duration_ms=507)
        turn.reflect("Passed. A single run cannot distinguish stable from flaky.")

    with tracer.turn("demo.orchestrator", "n/a", "Seek approval.") as turn:
        turn.checkpoint(prompted=True, decision="approved", note="verification demo")
        turn.reflect("Human approved; the patch may leave the sandbox.")

    print(f"\nwrote {tracer.path.relative_to(REPO_ROOT)}\n")
    raw = tracer.path.read_text(encoding="utf-8")
    print("--- raw file contents ---")
    print(raw)

    records = [json.loads(line) for line in raw.strip().splitlines()]
    ids = [record["turn_id"] for record in records]
    contiguous = all(b - a == 1 for a, b in zip(ids, ids[1:]))
    print("--- turn_id sequence ---")
    print(f"turn_ids           : {ids}")
    print(f"monotonic          : {ids == sorted(ids)}")
    print(f"strictly increasing: {contiguous}")
    print(f"unique             : {len(set(ids)) == len(ids)}")
    print(f"records            : {len(records)}")
    ok = ids == sorted(ids) and len(set(ids)) == len(ids) and contiguous
    return 0 if ok else 1


class FailingWriter:
    """Wraps ``Tracer._append_line`` to fail a scripted number of times."""

    def __init__(self, tracer: Tracer, failures: int) -> None:
        self.real = tracer._append_line
        self.remaining = failures
        self.attempts = 0
        self.started = time.perf_counter()

    def __call__(self, line: str) -> None:
        self.attempts += 1
        elapsed_ms = (time.perf_counter() - self.started) * 1000
        if self.remaining > 0:
            self.remaining -= 1
            print(f"      attempt {self.attempts} at t+{elapsed_ms:6.1f}ms -> OSError")
            raise OSError("simulated disk failure")
        print(f"      attempt {self.attempts} at t+{elapsed_ms:6.1f}ms -> written")
        self.real(line)


def demo_gaps() -> int:
    """Show retry, then gap-marking, then escalation on the third gap."""
    banner("2. GAP-MARKER POLICY: retry -> gap-mark -> escalate on 3 consecutive")

    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id="verify-gaps")
    if tracer.path.exists():
        tracer.path.unlink()
    problems: list[str] = []

    def write_turn(label: str, failures: int) -> BaseException | None:
        writer = FailingWriter(tracer, failures)
        tracer._append_line = writer  # type: ignore[method-assign]
        print(f"\n  [{label}] injecting {failures} write failure(s)")
        try:
            with tracer.turn("demo.agent", "n/a", label) as turn:
                turn.reflect(label)
            return None
        except TraceWriteError as exc:
            return exc
        finally:
            tracer._append_line = writer.real  # type: ignore[method-assign]
            print(
                f"      -> attempts={writer.attempts} "
                f"consecutive_gaps={tracer.consecutive_gaps} "
                f"total_gaps={tracer.total_gaps}"
            )

    print(
        f"\n  MAX_WRITE_ATTEMPTS={MAX_WRITE_ATTEMPTS}  "
        f"MAX_CONSECUTIVE_GAPS={MAX_CONSECUTIVE_GAPS}"
    )

    # A transient blip: two failures, third attempt lands. No gap.
    if write_turn("transient: 2 failures then success", 2) is not None:
        problems.append("a transient failure escalated instead of being retried")
    if tracer.total_gaps != 0:
        problems.append("a successfully retried write was recorded as a gap")

    # Every attempt fails: the record becomes a gap marker, the run continues.
    if write_turn("gap 1: all attempts fail", MAX_WRITE_ATTEMPTS) is not None:
        problems.append("the first gap halted the run")
    if tracer.consecutive_gaps != 1:
        problems.append(f"expected 1 consecutive gap, got {tracer.consecutive_gaps}")

    # A healthy write clears the streak.
    write_turn("healthy write resets the streak", 0)
    if tracer.consecutive_gaps != 0:
        problems.append("a successful write did not reset the streak")

    # Three consecutive gaps: escalate on the third, not before.
    escalated_at: int | None = None
    for attempt in range(1, MAX_CONSECUTIVE_GAPS + 1):
        error = write_turn(f"consecutive gap {attempt}", MAX_WRITE_ATTEMPTS)
        if error is not None:
            escalated_at = attempt
            print(f"      -> RAISED TraceWriteError: {error}")
            break
    if escalated_at != MAX_CONSECUTIVE_GAPS:
        problems.append(
            f"escalated at gap {escalated_at}, expected {MAX_CONSECUTIVE_GAPS}"
        )

    print("\n--- resulting trajectory ---")
    records = [
        json.loads(line)
        for line in tracer.path.read_text(encoding="utf-8").strip().splitlines()
    ]
    for record in records:
        kind = "GAP " if record["agent_name"] == GAP_AGENT_NAME else "real"
        detail = record["reflection"].replace("\n", " ")[:86]
        print(f"  turn_id={record['turn_id']:>2}  {kind}  {detail}")

    ids = [record["turn_id"] for record in records]
    contiguous = ids == list(range(len(ids)))
    print(f"\n  turn_ids: {ids}  contiguous={contiguous}")
    if not contiguous:
        problems.append("gap markers broke the turn_id sequence")

    verdict = "FAILED: " + "; ".join(problems) if problems else "all behaviours confirmed"
    print(f"\n  {verdict}")
    return 1 if problems else 0


#: Probe executed *through the sandbox executor*, i.e. from exactly the
#: position agent-authored code under test occupies.
ESCAPE_PROBE = '''
"""Probe: what can code under test actually reach?"""

import json
import os
import resource


def test_report_isolation():
    findings = {
        "uid": os.getuid(),
        "cwd": os.getcwd(),
        "tmpdir": os.environ.get("TMPDIR"),
        "sees_ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY" in os.environ,
        "sees_ANTHROPIC_BASE_URL": "ANTHROPIC_BASE_URL" in os.environ,
        "host_paths_visible": {
            path: os.path.exists(path)
            for path in [
                "/mnt/c", "/mnt/host", "/mnt/wsl", "/host", "/c",
                "/run/desktop", "/Users", "/home/HP", "/var/run/docker.sock",
            ]
        },
        "root_entries": sorted(os.listdir("/")),
        "rlimit_as_bytes": resource.getrlimit(resource.RLIMIT_AS)[0],
        "rlimit_nofile": resource.getrlimit(resource.RLIMIT_NOFILE)[0],
        "rlimit_cpu_s": resource.getrlimit(resource.RLIMIT_CPU)[0],
        "rlimit_core": resource.getrlimit(resource.RLIMIT_CORE)[0],
    }
    targets = [
        "/app/src/telemetry/tracer.py",
        "/app/corpus/case_00_smoke/README.md",
        "/app/scripts/verify_phase0.py",
    ]
    for target in targets:
        try:
            with open(target, "a"):
                pass
        except OSError as exc:
            findings.setdefault("write_denied", {})[target] = type(exc).__name__
        else:
            findings.setdefault("writable", []).append(target)
    print("PROBE_JSON " + json.dumps(findings))
'''


def demo_isolation() -> int:
    """Run a probe through the executor and report what it could reach."""
    banner("3. SANDBOX ISOLATION: what code under test can actually see")

    marker = Path("/.flakehunter-sandbox")
    print("\n--- the orchestrator's own view ---")
    print(f"  sandbox marker {marker}: {marker.exists()}")
    print(f"  uid                              : {os.getuid()} (0 would be root)")
    print(f"  / entries                        : {sorted(os.listdir('/'))}")

    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id="verify-isolation")
    executor = SandboxExecutor(tracer, trace_each_run=False)

    probe_dir = Path("/scratch/escape-probe")
    probe_dir.mkdir(parents=True, exist_ok=True)
    (probe_dir / "test_probe.py").write_text(ESCAPE_PROBE, encoding="utf-8")

    result = executor.run_once(probe_dir, pytest_args=["-s", "test_probe.py"])
    payload = None
    for line in result.stdout.splitlines():
        if line.startswith("PROBE_JSON "):
            payload = json.loads(line[len("PROBE_JSON ") :])
    if payload is None:
        print("\n  PROBE FAILED TO REPORT\n" + result.stdout[-2000:])
        return 1

    print("\n--- what a test run could reach ---")
    print(f"  uid                      : {payload['uid']} (non-root)")
    print(f"  cwd                      : {payload['cwd']}")
    print(f"  TMPDIR                   : {payload['tmpdir']}")
    print(f"  sees ANTHROPIC_API_KEY   : {payload['sees_ANTHROPIC_API_KEY']}")
    print(f"  sees ANTHROPIC_BASE_URL  : {payload['sees_ANTHROPIC_BASE_URL']}")
    print(f"  RLIMIT_AS (bytes)        : {payload['rlimit_as_bytes']:,}")
    print(f"  RLIMIT_NOFILE            : {payload['rlimit_nofile']}")
    print(f"  RLIMIT_CPU (s)           : {payload['rlimit_cpu_s']}")
    print(f"  RLIMIT_CORE              : {payload['rlimit_core']}")

    print("\n  host paths probed from inside the run:")
    for path, exists in payload["host_paths_visible"].items():
        print(f"    {path:<24} visible={exists}")

    print(f"\n  / as seen by the run: {payload['root_entries']}")

    print("\n  write attempts against mounted host directories:")
    for target, error in payload.get("write_denied", {}).items():
        print(f"    DENIED   {target}  ({error})")
    for target in payload.get("writable", []):
        print(f"    WRITABLE {target}   <-- ISOLATION HOLE")

    problems: list[str] = []
    if payload["sees_ANTHROPIC_API_KEY"] or payload["sees_ANTHROPIC_BASE_URL"]:
        problems.append("orchestrator credentials were visible to code under test")
    reachable = [p for p, seen in payload["host_paths_visible"].items() if seen]
    if reachable:
        problems.append(f"host paths reachable: {reachable}")
    if payload.get("writable"):
        problems.append(f"read-only mounts were writable: {payload['writable']}")
    if payload["uid"] == 0:
        problems.append("code under test ran as root")

    verdict = "FAILED: " + "; ".join(problems) if problems else "no isolation holes found"
    print(f"\n  {verdict}")
    return 1 if problems else 0


def main() -> int:
    """Run the requested check, or all of them."""
    assert_sandboxed()
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    checks = {"tracer": demo_tracer, "gaps": demo_gaps, "isolation": demo_isolation}
    if which == "all":
        return max(check() for check in checks.values())
    if which not in checks:
        print(f"unknown check {which!r}; pick from {sorted(checks)}", file=sys.stderr)
        return 2
    return checks[which]()


if __name__ == "__main__":
    raise SystemExit(main())
