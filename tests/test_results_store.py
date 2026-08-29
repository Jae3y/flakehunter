"""Evidence must survive a run that learns nothing.

Written after a real loss: a run in which every case died on API quota wrote
its results over the file, replacing a case_07 record carrying two hypothesis
rounds, two experiments and a rejected patch with a bare ERROR. The record had
to be restored by hand.

The rule under test is one-directional -- a record may only be replaced by one
of equal or greater evidence depth -- so these tests are mostly about what
*fails* to happen.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agent.results_store import evidence_depth, merge_results, save_agent_results


def result(case: str, status: str, *, experiments=0, rounds=0, verified=False, validations=0):
    """Build a serialised outcome with a given amount of evidence."""
    return {
        "case": case,
        "status": status,
        "experiments": [{"n": i} for i in range(experiments)],
        "hypotheses": [{"round": i} for i in range(rounds)],
        "validations": [{"attempt": i} for i in range(validations)],
        "verification": {"runs": 500, "failures": 0} if verified else None,
    }


class TestEvidenceDepth:
    """Depth must rank by what a record settles, not by how much it flailed."""

    def test_a_quota_error_ranks_lowest(self) -> None:
        assert evidence_depth(result("c", "ERROR")) < evidence_depth(
            result("c", "UNRESOLVED")
        )

    def test_a_verified_pending_outranks_everything(self) -> None:
        pending = result("c", "PENDING", experiments=1, rounds=1, verified=True)
        flailed = result("c", "UNRESOLVED", experiments=5, rounds=5)
        assert evidence_depth(pending) > evidence_depth(flailed)

    def test_more_experiments_break_a_status_tie(self) -> None:
        assert evidence_depth(result("c", "UNRESOLVED", experiments=3)) > evidence_depth(
            result("c", "UNRESOLVED", experiments=1)
        )


class TestMergeNeverLosesEvidence:
    """The specific regression: a shallow run must not clobber a deep record."""

    def test_a_quota_error_cannot_replace_real_evidence(self) -> None:
        existing = [result("case_07", "UNRESOLVED", experiments=2, rounds=2, validations=3)]
        incoming = [result("case_07", "ERROR")]

        merged = merge_results(existing, incoming)

        assert len(merged) == 1
        assert merged[0]["status"] == "UNRESOLVED"
        assert len(merged[0]["experiments"]) == 2

    def test_the_shallow_attempt_is_still_recorded(self) -> None:
        """Refusing the overwrite must not make the attempt invisible."""
        existing = [result("case_07", "UNRESOLVED", experiments=2)]
        incoming = [{**result("case_07", "ERROR"), "error": "quota exhausted"}]

        merged = merge_results(existing, incoming)

        superseded = merged[0]["superseded_attempts"]
        assert len(superseded) == 1
        assert "quota" in superseded[0]["error"]

    def test_cases_absent_from_the_run_are_preserved(self) -> None:
        """The commonest loss was omission, not overwrite."""
        existing = [
            result("case_07", "UNRESOLVED", experiments=2),
            result("case_12", "PENDING", experiments=1, verified=True),
        ]
        incoming = [result("case_02", "ERROR")]

        merged = merge_results(existing, incoming)

        assert {r["case"] for r in merged} == {"case_02", "case_07", "case_12"}
        assert next(r for r in merged if r["case"] == "case_12")["status"] == "PENDING"

    def test_a_deeper_run_does_replace(self) -> None:
        """The rule must not freeze progress."""
        existing = [result("case_05", "UNRESOLVED", experiments=2)]
        incoming = [result("case_05", "PENDING", experiments=3, verified=True)]

        merged = merge_results(existing, incoming)

        assert merged[0]["status"] == "PENDING"

    def test_an_equal_depth_rerun_wins(self) -> None:
        """Ties go to the newer record, so a re-run is not ignored."""
        existing = [{**result("case_03", "UNRESOLVED", experiments=1), "tag": "old"}]
        incoming = [{**result("case_03", "UNRESOLVED", experiments=1), "tag": "new"}]

        assert merge_results(existing, incoming)[0]["tag"] == "new"


class TestSaveRoundTrip:
    """The file on disk must show the same protection."""

    def test_a_quota_only_run_leaves_the_file_no_worse(self, tmp_path: Path) -> None:
        path = tmp_path / "agent_results.json"
        save_agent_results(
            path,
            [result("case_07", "UNRESOLVED", experiments=2, rounds=2)],
            "m",
            "run-1",
            {},
        )

        # Every case in the second run failed on quota, as actually happened.
        save_agent_results(
            path,
            [result("case_07", "ERROR"), result("case_02", "ERROR")],
            "m",
            "run-2",
            {},
        )

        saved = json.loads(path.read_text(encoding="utf-8"))["results"]
        by_case = {r["case"]: r for r in saved}
        assert by_case["case_07"]["status"] == "UNRESOLVED"
        assert len(by_case["case_07"]["experiments"]) == 2
        assert by_case["case_02"]["status"] == "ERROR"

    def test_a_corrupt_existing_file_does_not_abort_the_write(self, tmp_path: Path) -> None:
        path = tmp_path / "agent_results.json"
        path.write_text("{ not json", encoding="utf-8")

        merged = save_agent_results(path, [result("case_01", "PENDING")], "m", "r", {})

        assert [r["case"] for r in merged] == ["case_01"]
