"""The locked measurement protocol.

Every flake rate in this project — the corpus baseline, the baseline arm's
residual, the agent's verification — is measured under these settings. They are
here, in one module, rather than as defaults scattered across scripts, because
the comparison that matters is between two arms, and a comparison in which the
arms were measured differently is not evidence of anything.

## Why the worker count is per-case

Measured, not assumed. `scripts/concurrency_drift.py` ran every timing-
sensitive case serially and at 8 workers:

| case | serial | 8 workers | drift |
|---|---|---|---|
| case_01 race condition | 0.0% | 25.0% | 25.0 pts |
| case_03 port collision | 45.5% | 33.0% | 12.5 pts |
| case_04 clock dependence | 6.0% | 2.0% | 4.0 pts |
| case_07 network timeout | 2.5% | 8.0% | 5.5 pts |
| case_08 tempfile collision | 1.0% | 4.5% | 3.5 pts |
| case_09 float tolerance | 15.5% | 13.5% | 2.0 pts |
| case_10 async ordering | 0.0% | 11.5% | 11.5 pts |
| case_12 masking trap | 0.0% | 0.0% | 0.0 pts |

Case 01 read **0.0% serially**. Its flakiness was not being amplified by the
harness's concurrency, it was being *created* by it — eight concurrent runs
oversubscribing the CPU is what forced the GIL to preempt mid-update. The same
was true of case 10.

That is also the explanation for the drift that prompted this investigation.
Case 01's rate wandered between 18.9% and 56.8% across sessions while case 06,
whose nondeterminism comes from the RNG rather than from thread timing, sat at
23–27% in every condition tested. A rate produced by contention tracks machine
load, and machine load was never a controlled variable.

Both cases were rebuilt so their nondeterminism is intrinsic — present when the
test runs alone — rather than manufactured by the measurement. See
`docs/CHANGELOG.md` 003.

The rule agreed with the maintainer: drift beyond 10–15 points pins that case
to a lower worker count, applied identically to both arms.
"""

from __future__ import annotations

__all__ = [
    "CANONICAL_RUNS",
    "DEFAULT_WORKERS",
    "PINNED_WORKERS",
    "RUN_COUNTS",
    "runs_for",
    "workers_for",
]

#: Runs behind every headline flake rate.
CANONICAL_RUNS = 500

#: Worker count for cases whose rate does not depend on contention.
DEFAULT_WORKERS = 8

#: Cases pinned lower because parallelism moved their rate past the threshold.
#: Serial removes self-inflicted contention entirely, so the rate measured is
#: the case's own.
PINNED_WORKERS: dict[str, int] = {
    "case_01_race_condition": 1,
    "case_10_async_ordering": 1,
}

#: Runs per phase. Verification is the number of record; the others only have
#: to be large enough to separate hypotheses, and spending 500 runs on an
#: experiment that a hundred would settle is wasted wall clock.
RUN_COUNTS: dict[str, int] = {
    "confirm": 200,
    "experiment": 150,
    "stress": 200,
    "verify": CANONICAL_RUNS,
}

#: Phases whose run count is halved for pinned (serial) cases, which cost
#: roughly eight times as much wall clock per run. Verification is deliberately
#: not in this list: the headline number stays at 500 for every case.
_HALVED_WHEN_SERIAL = ("confirm", "experiment", "stress")


def workers_for(case_name: str) -> int:
    """The locked worker count for a case."""
    return PINNED_WORKERS.get(case_name, DEFAULT_WORKERS)


def runs_for(case_name: str, phase: str) -> int:
    """The locked run count for a case and phase.

    Args:
        case_name: Corpus case directory name.
        phase: One of ``confirm``, ``experiment``, ``stress``, ``verify``.

    Returns:
        Runs to execute.
    """
    runs = RUN_COUNTS[phase]
    if case_name in PINNED_WORKERS and phase in _HALVED_WHEN_SERIAL:
        return max(runs // 2, 50)
    return runs
