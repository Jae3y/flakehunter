"""Run the full agent loop across the corpus.

    docker compose run --rm flakehunter python scripts/run_agent.py

Every case ends in one of:

``PENDING``     a patch survived validation and 500-run verification, and is
                waiting in ``results/pending_approval/<case>/`` for a human.
``UNRESOLVED``  the rounds cap was hit, or the same hypothesis came back with
                no new discriminating evidence.
``NO_FLAKE``    the test never failed during CONFIRM, so there was nothing to
                diagnose.
``ERROR``       the provider call failed after retries.

Nothing is written to ``corpus/``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agent.orchestrator import AgentConfig, run_agent_case  # noqa: E402
from src.harness.protocol import runs_for, workers_for  # noqa: E402
from src.harness.runner import TestRunner  # noqa: E402
from src.llm.client import GeminiClient  # noqa: E402
from src.sandbox.executor import SandboxExecutor, assert_sandboxed  # noqa: E402
from src.telemetry.tracer import Tracer  # noqa: E402

CORPUS = REPO_ROOT / "corpus"
EXCLUDED = {"case_00_smoke"}


def discover_cases(only: list[str] | None) -> list[Path]:
    """Corpus cases, smoke case excluded.

    When ``only`` is given, its order is preserved rather than re-sorted. A
    long run can be interrupted, so being able to put the cases that matter
    most at the front is worth more than alphabetical tidiness.
    """
    available = {
        path.name: path
        for path in sorted(CORPUS.iterdir())
        if path.is_dir() and path.name.startswith("case_") and path.name not in EXCLUDED
    }
    if not only:
        return list(available.values())

    selected: list[Path] = []
    for name in only:
        key = name.strip()
        match = available.get(key) or next(
            (p for n, p in available.items() if n.split("_")[1] == key), None
        )
        if match is not None and match not in selected:
            selected.append(match)
    return selected


def main() -> int:
    """Run the agent over every selected case and record the outcomes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="*", default=None)
    parser.add_argument("--verify-runs", type=int, default=None)
    parser.add_argument("--confirm-runs", type=int, default=None)
    parser.add_argument("--max-rounds", type=int, default=5)
    args = parser.parse_args()

    assert_sandboxed()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_id = f"agent-{stamp}"
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=run_id)
    client = GeminiClient(tracer)
    executor = SandboxExecutor(tracer, trace_each_run=False)
    runner = TestRunner(executor, tracer)
    approval_root = REPO_ROOT / "results" / "pending_approval"
    approval_root.mkdir(parents=True, exist_ok=True)

    cases = discover_cases(args.cases)
    print("=" * 78)
    print(f"AGENT LOOP: model={client.model}  trajectory=traces/{run_id}.jsonl")
    print("=" * 78)

    outcomes = []
    started = time.perf_counter()
    for case in cases:
        workers = workers_for(case.name)
        config = AgentConfig(
            confirm_runs=args.confirm_runs or runs_for(case.name, "confirm"),
            experiment_runs=runs_for(case.name, "experiment"),
            verify_runs=args.verify_runs or runs_for(case.name, "verify"),
            stress_runs=runs_for(case.name, "stress"),
            workers=workers,
            max_rounds=args.max_rounds,
        )
        print(f"\n{'-' * 78}\n{case.name}  (workers={workers})\n{'-' * 78}")
        outcome = run_agent_case(
            case, client, executor, runner, config, approval_root, run_id
        )
        outcomes.append(outcome)

        print(f"  status   : {outcome.status}")
        print(
            f"  cause    : {outcome.concluded_class} "
            f"(expected {outcome.expected_class}) "
            f"{'MATCH' if outcome.cause_identified else 'no match'}"
        )
        print(f"  rounds   : {outcome.rounds}, experiments: {len(outcome.experiments)}")
        if outcome.verify_report:
            print(
                f"  residual : {outcome.verify_report.failures}/"
                f"{outcome.verify_report.runs} = "
                f"{outcome.verify_report.flake_rate:.2%}"
            )
        if outcome.stuck_reason:
            print(f"  note     : {outcome.stuck_reason}")
        print(f"  tokens   : {outcome.prompt_tokens} in / {outcome.output_tokens} out")
        print(f"  wall     : {outcome.wall_s / 60:.1f} min")

        # Written after every case, so an interrupted run still leaves results.
        (REPO_ROOT / "results" / "agent_results.json").write_text(
            json.dumps(
                {
                    "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "model": client.model,
                    "trace_run_id": run_id,
                    "usage": client.usage_summary(),
                    "results": [o.to_dict() for o in outcomes],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    elapsed = time.perf_counter() - started
    print("\n" + "=" * 78)
    print(f"{'case':<34}{'status':>12}{'cause?':>8}{'residual':>11}")
    print("-" * 78)
    for outcome in outcomes:
        residual = outcome.residual_flake_rate
        print(
            f"{outcome.case:<34}{outcome.status:>12}"
            f"{('yes' if outcome.cause_identified else 'no'):>8}"
            f"{(f'{residual:.2%}' if residual is not None else '-'):>11}"
        )
    print("-" * 78)
    pending = sum(1 for o in outcomes if o.status == "PENDING")
    identified = sum(1 for o in outcomes if o.cause_identified)
    print(f"  PENDING approval : {pending}/{len(outcomes)}")
    print(f"  cause identified : {identified}/{len(outcomes)}")
    print(f"  wall clock       : {elapsed / 60:.1f} min")
    print(f"  tokens           : {client.usage_summary()}")
    print("\n  wrote results/agent_results.json\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
