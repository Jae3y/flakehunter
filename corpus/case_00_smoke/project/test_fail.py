"""Deterministically failing test. Proves the executor reports FAIL, not ERROR."""


def test_always_fails() -> None:
    assert sum(range(10)) == 46, "deliberate failure for the Phase 0 gate"
