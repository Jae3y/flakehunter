"""Persist a case's established state so quota exhaustion is not total loss.

The free tier allows 20 requests per day per model. A case that gets three
rounds in before the cap lands has *learned* things — an empirical flake rate,
a set of eliminated hypotheses, experiments whose outcomes are facts — and
throwing them away means the next attempt spends its first requests
rediscovering them. On a 20-request budget that is the difference between
finishing a case and not.

So the loop checkpoints after every round. A resumed case skips CONFIRM
entirely (free of requests but minutes of CPU), and starts from the hypotheses
already ruled out rather than proposing them again.

**Provenance matters.** LLM-derived state — hypotheses, experiment designs — is
only restored when the checkpoint was written by the *same model* the run is
using. A hypothesis proposed by one model and inherited by another would make
the arm a blend rather than a comparison. Execution-derived state — the CONFIRM
measurement, the experiment outcomes — is model-independent and always
restored, because a flake rate is a property of the code.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.harness.runner import BatchReport

__all__ = ["CaseCheckpoint", "checkpoint_path", "load_checkpoint", "save_checkpoint"]

#: Where checkpoints live. Outside corpus/, which is read-only by design.
CHECKPOINT_DIR = Path("results/checkpoints")


@dataclass
class CaseCheckpoint:
    """Everything a resumed case can start from instead of rediscovering."""

    case: str
    model: str
    confirm: BatchReport | None = None
    rounds_completed: int = 0
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    experiments: list[dict[str, Any]] = field(default_factory=list)
    eliminated: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    seen_signatures: list[list[Any]] = field(default_factory=list)
    llm_state_usable: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise."""
        return {
            "case": self.case,
            "model": self.model,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "confirm": self.confirm.to_dict() if self.confirm else None,
            "rounds_completed": self.rounds_completed,
            "hypotheses": self.hypotheses,
            "experiments": self.experiments,
            "eliminated": self.eliminated,
            "history": self.history,
            "seen_signatures": self.seen_signatures,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any], model: str) -> "CaseCheckpoint":
        """Rebuild, dropping LLM-derived state written by a different model.

        The CONFIRM measurement survives a model change; reasoning does not.
        """
        same_model = payload.get("model") == model
        confirm = (
            BatchReport.from_dict(payload["confirm"]) if payload.get("confirm") else None
        )
        if not same_model:
            return cls(
                case=payload["case"],
                model=model,
                confirm=confirm,
                llm_state_usable=False,
                note=(
                    f"checkpoint was written by {payload.get('model')!r}; only the "
                    f"execution-derived CONFIRM measurement was restored"
                ),
            )
        return cls(
            case=payload["case"],
            model=model,
            confirm=confirm,
            rounds_completed=payload.get("rounds_completed", 0),
            hypotheses=payload.get("hypotheses", []),
            experiments=payload.get("experiments", []),
            eliminated=payload.get("eliminated", []),
            history=payload.get("history", []),
            seen_signatures=[list(sig) for sig in payload.get("seen_signatures", [])],
            note=payload.get("note", ""),
        )

    def describe(self) -> str:
        """One line for the run log."""
        if self.confirm is None:
            return "no checkpoint"
        parts = [f"confirm {self.confirm.failures}/{self.confirm.runs}"]
        if self.rounds_completed:
            parts.append(f"{self.rounds_completed} round(s) done")
        if self.eliminated:
            parts.append(f"eliminated {sorted(set(self.eliminated))}")
        if not self.llm_state_usable:
            parts.append("(reasoning discarded: different model)")
        return "resuming: " + ", ".join(parts)


def checkpoint_path(case: str, root: Path | None = None) -> Path:
    """Where a case's checkpoint lives."""
    return (root or CHECKPOINT_DIR) / f"{case}.json"


def load_checkpoint(
    case: str, model: str, root: Path | None = None
) -> CaseCheckpoint | None:
    """Load a case's checkpoint, or None when there is nothing to resume."""
    path = checkpoint_path(case, root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return CaseCheckpoint.from_dict(payload, model)


def save_checkpoint(checkpoint: CaseCheckpoint, root: Path | None = None) -> Path:
    """Write a checkpoint, creating the directory if needed."""
    path = checkpoint_path(checkpoint.case, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoint.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path
