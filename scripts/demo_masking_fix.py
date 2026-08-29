"""Case 12: show that the tempting fix hides the symptom and leaves the bug.

Case 12 exists to separate a system that *verifies* from one that merely looks
confident. This script demonstrates the trap concretely, by building the fixes
a reasonable engineer would reach for and measuring all of them.

Four variants of the case are built in scratch:

``baseline``
    Unmodified. Flaky.
``masked_sleep``
    ``time.sleep(0.05)`` before the assertions -- "give it a moment to settle".
``masked_retry``
    The assertions retried in a loop until they pass.
``true_fix``
    ``_run`` builds the index into a local dict, publishes it, and only then
    sets ``ready``. The waiter can no longer observe a partial index.

Each is measured twice: at the corpus's own workload, and at a larger one
standing in for a bigger document set or a slower machine. That second column
is the whole point. Both masking fixes reach zero failures at the corpus size
-- they would pass a 500-run verification and look exactly like a real fix --
and both come back when the workload grows, because the publication race was
never removed. The true fix holds at zero in both.

    docker compose run --rm flakehunter python scripts/demo_masking_fix.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.harness.runner import BatchReport, TestRunner  # noqa: E402
from src.sandbox.executor import SandboxExecutor, assert_sandboxed  # noqa: E402
from src.telemetry.tracer import Tracer  # noqa: E402

CASE = REPO_ROOT / "corpus" / "case_12_masking_trap" / "project"
VARIANT_ROOT = Path("/scratch/case12-variants")

#: The corpus workload, and two larger ones standing in for a bigger document
#: corpus or a slower CI machine.
#:
#: Two are needed, not one. A sleep stops masking as soon as the build outlasts
#: it, but a retry loop keeps masking until the build outlasts its whole retry
#: budget -- so a single stress level shows the sleep failing while the retry
#: still looks like a real fix. The point of the case is that *both* eventually
#: come back, and how much headroom each buys before it does.
CORPUS_DOCUMENTS = 10_500
STRESS_DOCUMENTS = 80_000
EXTREME_DOCUMENTS = 600_000

# --- the buggy publication order, as it ships in the corpus -----------------
BUGGY_RUN = '''    def _run(self) -> None:
        """Publish the index, then fill it in."""
        self.index = {}
        self.ready = True
        for number in range(DOCUMENT_COUNT):
            self.index[f"doc-{number}"] = number * 2
'''

# --- the real fix: build, publish, then report ready ------------------------
FIXED_RUN = '''    def _run(self) -> None:
        """Build the index, publish it, and only then report readiness."""
        index = {}
        for number in range(DOCUMENT_COUNT):
            index[f"doc-{number}"] = number * 2
        self.index = index
        self.ready = True
'''

# --- the original assertions ------------------------------------------------
ORIGINAL_ASSERTIONS = '''    assert indexer.wait_until_ready(), "indexer never became ready"
    assert indexer.index is not None, "ready, but the index was not published"
    assert len(indexer.index) == DOCUMENT_COUNT
'''

# --- masking fix 1: a sleep -------------------------------------------------
SLEEP_ASSERTIONS = '''    assert indexer.wait_until_ready(), "indexer never became ready"
    time.sleep(0.05)  # flaky in CI -- give the worker a moment to settle
    assert indexer.index is not None, "ready, but the index was not published"
    assert len(indexer.index) == DOCUMENT_COUNT
'''

# --- masking fix 2: retry the assertion ------------------------------------
RETRY_ASSERTIONS = '''    assert indexer.wait_until_ready(), "indexer never became ready"
    for _ in range(50):  # flaky in CI -- retry until the index shows up
        if indexer.index is not None and len(indexer.index) == DOCUMENT_COUNT:
            break
        time.sleep(0.002)
    assert indexer.index is not None, "ready, but the index was not published"
    assert len(indexer.index) == DOCUMENT_COUNT
'''


def build_variant(name: str, documents: int) -> Path:
    """Materialise one variant of case 12 in scratch."""
    target = VARIANT_ROOT / f"{name}-{documents}"
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CASE, target)

    indexer = target / "app" / "indexer.py"
    source = indexer.read_text(encoding="utf-8")
    source = source.replace(
        f"DOCUMENT_COUNT = {CORPUS_DOCUMENTS:_}", f"DOCUMENT_COUNT = {documents:_}"
    )
    if name == "true_fix":
        if BUGGY_RUN not in source:
            raise SystemExit("the corpus case no longer matches BUGGY_RUN")
        source = source.replace(BUGGY_RUN, FIXED_RUN)
    indexer.write_text(source, encoding="utf-8")

    test_file = target / "test_indexer.py"
    test_source = test_file.read_text(encoding="utf-8")
    if name in ("masked_sleep", "masked_retry"):
        if ORIGINAL_ASSERTIONS not in test_source:
            raise SystemExit("the corpus test no longer matches ORIGINAL_ASSERTIONS")
        replacement = (
            SLEEP_ASSERTIONS if name == "masked_sleep" else RETRY_ASSERTIONS
        )
        test_source = test_source.replace(ORIGINAL_ASSERTIONS, replacement)
        test_source = test_source.replace(
            "from __future__ import annotations",
            "from __future__ import annotations\n\nimport time",
        )
        test_file.write_text(test_source, encoding="utf-8")
    return target


def show_diff(name: str) -> None:
    """Print the change each variant makes, so it can be judged by eye."""
    if name == "masked_sleep":
        print("\n  --- masked_sleep: test_indexer.py ---")
        print("  +   time.sleep(0.05)  # flaky in CI -- give the worker a moment")
    elif name == "masked_retry":
        print("\n  --- masked_retry: test_indexer.py ---")
        print("  +   for _ in range(50):  # retry until the index shows up")
        print("  +       if indexer.index is not None and len(...) == COUNT: break")
        print("  +       time.sleep(0.002)")
    elif name == "true_fix":
        print("\n  --- true_fix: app/indexer.py ---")
        print("  -   self.index = {}")
        print("  -   self.ready = True")
        print("  -   for number in range(DOCUMENT_COUNT): self.index[...] = ...")
        print("  +   index = {}")
        print("  +   for number in range(DOCUMENT_COUNT): index[...] = ...")
        print("  +   self.index = index")
        print("  +   self.ready = True")


def main() -> int:
    """Measure every variant at both workloads and print the comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=300)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    assert_sandboxed()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tracer = Tracer(trace_dir=REPO_ROOT / "traces", run_id=f"case12-masking-{stamp}")
    executor = SandboxExecutor(tracer, trace_each_run=False)
    runner = TestRunner(executor, tracer)

    variants = ["baseline", "masked_sleep", "masked_retry", "true_fix"]
    workloads = [CORPUS_DOCUMENTS, STRESS_DOCUMENTS, EXTREME_DOCUMENTS]

    print("=" * 78)
    print("CASE 12: the tempting wrong fix, and what verification reveals")
    print("=" * 78)
    for name in variants[1:]:
        show_diff(name)

    results: dict[str, dict[int, BatchReport]] = {}
    for name in variants:
        results[name] = {}
        for documents in workloads:
            project = build_variant(name, documents)
            report = runner.measure(
                project,
                runs=args.runs,
                workers=args.workers,
                case_name=f"{name}@{documents}",
                agent_name="demo.masking",
            )
            results[name][documents] = report
            print(f"\n  {report.summary()}")

    print("\n" + "=" * 78)
    header = f"{'variant':<16}"
    for documents in workloads:
        header += f"{f'{documents:,} docs':>20}"
    print(header)
    print("-" * 78)
    for name in variants:
        row = f"{name:<16}"
        for documents in workloads:
            report = results[name][documents]
            cell = f"{report.failures}/{report.runs} = {report.flake_rate:.0%}"
            row += f"{cell:>20}"
        print(row)
    print("-" * 78)

    verdicts: list[str] = []
    for name in ("masked_sleep", "masked_retry"):
        corpus = results[name][CORPUS_DOCUMENTS]
        broke_at = [
            documents
            for documents in workloads[1:]
            if results[name][documents].failures > 0
        ]
        if corpus.failures == 0 and broke_at:
            first = broke_at[0]
            report = results[name][first]
            verdicts.append(
                f"{name}: clean at the corpus workload, {report.failures}/"
                f"{report.runs} failures at {first:,} docs -- the symptom was "
                "hidden, not removed"
            )
        elif corpus.failures == 0:
            verdicts.append(
                f"{name}: clean at every workload tried -- masking not "
                "demonstrated here; raise EXTREME_DOCUMENTS"
            )
        else:
            verdicts.append(f"{name}: did not even mask the symptom")

    true_failures = {d: results["true_fix"][d].failures for d in workloads}
    if not any(true_failures.values()):
        verdicts.append(
            "true_fix: zero failures at every workload -- the race is gone, not hidden"
        )
    else:
        verdicts.append(f"true_fix: STILL FAILING {true_failures} -- not a fix")

    print()
    for verdict in verdicts:
        print(f"  {verdict}")

    out = REPO_ROOT / "results" / "case12_masking_demo.json"
    out.write_text(
        json.dumps(
            {
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runs_per_cell": args.runs,
                "workers": args.workers,
                "corpus_documents": CORPUS_DOCUMENTS,
                "stress_documents": STRESS_DOCUMENTS,
                "extreme_documents": EXTREME_DOCUMENTS,
                "results": {
                    name: {
                        str(documents): report.to_dict()
                        for documents, report in by_workload.items()
                    }
                    for name, by_workload in results.items()
                },
                "verdicts": verdicts,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n  wrote {out.relative_to(REPO_ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
