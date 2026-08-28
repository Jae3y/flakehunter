# case_00_smoke — plumbing, not evaluation

This case is **not** one of the twelve. It exists so Phase 0 has something to
execute before the real corpus exists, and it is excluded from every results
table.

It contains four tests, each proving one property of the sandbox executor:

| File | Proves |
|---|---|
| `test_pass.py` | A clean run is reported `PASS`. Also the benchmark workload. |
| `test_fail.py` | A failing assertion is `FAIL`, not `ERROR`. |
| `test_hang.py` | A non-terminating test is killed and reported `TIMEOUT`, and its orphaned thread is reaped with the process group. |
| `test_hashorder.py` | Inter-process nondeterminism survives the executor. |

The last one is the important one. Because `PYTHONHASHSEED` is randomised per
interpreter, this test flakes only when each run is a genuinely fresh process.
If a future change to the harness ever collapses runs into a shared
interpreter, this test stops flaking — and the gate fails loudly instead of
silently reporting perfect stability for a corpus that no longer flakes.
