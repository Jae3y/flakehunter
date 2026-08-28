"""Deterministically passing test. Baseline for the execution benchmark."""


def test_always_passes() -> None:
    assert sum(range(10)) == 45
