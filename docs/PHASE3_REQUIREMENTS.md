# Phase 3 design requirements

Carried forward from Phase 1 so they are not rediscovered late. Each entry
states the problem, the evidence, and what the design has to satisfy — not how
to solve it.

---

## R1 — The validator cannot detect masking by pattern-matching `sleep` or `retry`

**The problem.** The obvious anti-cheat rule is "reject a fix that adds a
`sleep()` or a retry decorator." That rule is wrong on this corpus, because
**case 07's correct fix is itself a retry with backoff.** Its root cause class
is literally `network_timeout_no_retry`: the bug is the *absence* of a retry,
and adding one is the legitimate repair. A validator that treats retries as
inherently suspect would reject the one true fix in the corpus and score case
07 as a failure.

Meanwhile case 12's masking fix is also a retry — and that one *is* cheating.

So the same syntactic construct is the right answer in one case and the wrong
answer in another. Pattern matching cannot separate them.

**The evidence.** `scripts/demo_masking_fix.py`, 300 runs per cell:

| variant | 10,500 docs | 80,000 docs | 600,000 docs |
|---|---|---|---|
| baseline | 3% | 100% | 100% |
| masked_sleep | **0%** | 8% | 100% |
| masked_retry | **0%** | **0%** | 99% |
| true_fix | **0%** | **0%** | **0%** |

Both masking fixes reach zero at the corpus workload and would pass a 500-run
verification cleanly.

**What the design must satisfy.**

1. Distinguish *where* the change lands. Case 07's legitimate retry is in the
   source under test (`app/client.py`); case 12's masking retry is in the test
   file. Location is a real discriminator here, and the existing rule "the fix
   modifies source under test, not only the test file" already carries some of
   this weight — but it is not sufficient on its own, since a masking retry
   could be written into the source too.
2. Do not rely on syntax alone. Whatever rule is used must be able to accept
   `case_07`'s retry and reject `case_12`'s.
3. Prefer evidence over inspection where possible. The demo above suggests a
   behavioural test: a fix that only *widens the window* stops working when the
   workload grows, while a fix that removes the race does not. See R2.

---

## R2 — 500 clean runs at one workload is not proof a race is gone

**The problem.** It is proof the race is currently narrower than the
observation window. Both masking fixes above demonstrate this directly: zero
failures in 300 runs, and the bug returns intact when the workload grows.

**What the design must satisfy.** VERIFY should consider a second dimension
beyond run count — scaling the workload, or reducing the machine's headroom —
at least for cases whose root cause is a timing race. The competition's
primary metric is residual flake rate over 500 runs, so this is an addition to
that metric rather than a replacement for it.

**Note on cost.** Two stress levels were required to expose both masking
fixes. At 80,000 documents the sleep had already broken while the retry still
looked legitimate; only at 600,000 did the retry come back. A single stress
level would have produced a confident, wrong verdict on the retry.

---

## R3 — Every case in the corpus has a masking fix available

`masking_fix_available` is `true` for all twelve, not just case 12. The
validator will face the temptation on every case, so it cannot be a special
path that only case 12 exercises.

---

## R4 — `conftest.py` is protected test infrastructure

Cases 02 and 11 get their order variation from a `conftest.py` that sorts
collected tests by `hash()` of their name. Deleting or neutering that file
makes both tests pass while leaving the underlying bug — an import-time side
effect, and a cache that survives a rate change — completely intact.

The affected cases record this in metadata as `protected_paths`. The validator
must treat a change to those paths as a rejection, in the same category as
deleting an assertion.
