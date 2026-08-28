"""Genuinely flaky test whose nondeterminism lives *between* interpreters.

Python randomises string hashing per process unless ``PYTHONHASHSEED`` is set,
so the iteration order of this set differs from one run to the next. Nothing
here is a random number generator faking a failure: the same code, the same
inputs and the same machine produce different orders because the interpreter
seeds its hash function differently at startup.

This is the smoke case's most useful property. It gives the Phase 0 gate a way
to prove that the executor *preserves* inter-process nondeterminism -- and that
the FORK strategy, which inherits its parent's hash seed, does not.
"""

LABELS = {"alpha", "beta", "gamma", "delta", "epsilon"}


def test_first_label_is_alpha() -> None:
    assert next(iter(LABELS)) == "alpha"
