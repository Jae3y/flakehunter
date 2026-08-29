"""Persist agent results without ever losing evidence to a shallower run.

The bug this exists to prevent, observed in practice: a run in which every case
died on API quota wrote its results over the file, and a prior case_07 record
carrying two hypothesis rounds, two experiments and a rejected patch was
replaced by a bare ``ERROR``. It had to be restored by hand from an archive.

The original code was ``results: [o.to_dict() for o in outcomes]`` — this
invocation's cases, and nothing else. Cases absent from the run vanished, and
cases present in it overwrote whatever was there regardless of whether the new
record knew anything.

The rule here is one-directional: **a record may only be replaced by one of
equal or greater evidence depth.** A quota failure knows nothing, ranks lowest,
and therefore cannot displace anything. A completed run outranks an
interrupted one and replaces it. Ties go to the newer record, so a re-run that
reaches the same depth still wins.

Depth deliberately leads with *terminal status* rather than raw counts. A case
that reached PENDING with one round is a better record than one that flailed
through five and died, and counting experiments alone would get that backwards.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = ["evidence_depth", "merge_results", "save_agent_results"]

#: Terminal statuses ranked by how much they settle. ERROR is bottom because it
#: means "we never found out", which is the opposite of a result.
STATUS_RANK: dict[str, int] = {
    "PENDING": 4,
    "UNRESOLVED": 3,
    "NO_FLAKE": 2,
    "NOT RUN": 1,
    "ERROR": 0,
}


def evidence_depth(result: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    """How much a result actually establishes, for ordering.

    Ordered by significance, most significant first:

    1. terminal status — did the case reach a conclusion at all;
    2. whether a 500-run verification exists;
    3. experiments actually run — the only evidence that eliminates anything;
    4. hypothesis rounds;
    5. validator verdicts recorded.

    Args:
        result: One serialised :class:`CaseOutcome`.

    Returns:
        A tuple ordered so that larger means "knows more".
    """
    return (
        STATUS_RANK.get(str(result.get("status", "ERROR")), 0),
        1 if result.get("verification") else 0,
        len(result.get("experiments") or []),
        len(result.get("hypotheses") or []),
        len(result.get("validations") or []),
    )


def merge_results(
    existing: Iterable[Mapping[str, Any]],
    incoming: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Combine two sets of case results, keeping the deeper record per case.

    Cases present only in ``existing`` are preserved untouched — the common way
    evidence was lost was not being overwritten but being *omitted* by a run
    that covered fewer cases.

    Args:
        existing: Results already on disk.
        incoming: Results from the current run.

    Returns:
        One record per case, sorted by case name.
    """
    merged: dict[str, dict[str, Any]] = {r["case"]: dict(r) for r in existing}

    for result in incoming:
        case = result["case"]
        current = merged.get(case)
        if current is None or evidence_depth(result) >= evidence_depth(current):
            merged[case] = dict(result)
        else:
            # Keep the deeper record, but leave a trace that a shallower attempt
            # happened -- otherwise a quota-blocked re-run is invisible.
            superseded = current.setdefault("superseded_attempts", [])
            superseded.append(
                {
                    "status": result.get("status"),
                    "error": result.get("error"),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "note": (
                        "a later attempt produced less evidence than this record "
                        "and was not allowed to replace it"
                    ),
                }
            )

    return sorted(merged.values(), key=lambda r: r["case"])


def save_agent_results(
    path: Path,
    outcomes: Iterable[Mapping[str, Any]],
    model: str,
    trace_run_id: str,
    usage: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Merge this run's outcomes into the results file and write it.

    Called after every case, so an interrupted run still leaves results — and,
    because of the merge rule, cannot leave *worse* results than it found.

    Returns:
        The merged records, for the caller to report on.
    """
    previous: list[dict[str, Any]] = []
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8")).get("results", [])
        except (json.JSONDecodeError, OSError):
            previous = []

    merged = merge_results(previous, outcomes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "model": model,
                "trace_run_id": trace_run_id,
                "usage": dict(usage),
                "results": merged,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return merged
