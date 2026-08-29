"""Registry behaviour, and the metrics plugin that populates it."""

from __future__ import annotations

from app.registry import plugin_count, registered, reset


def test_registry_is_empty_until_a_plugin_is_installed() -> None:
    assert plugin_count() == 0, f"unexpected plugins present: {registered()}"


def test_metrics_plugin_registers_itself() -> None:
    import app.metrics

    assert app.metrics.collect() == {"requests": 0, "errors": 0}
    assert "metrics" in registered()


def test_registry_can_be_reset() -> None:
    reset()

    assert plugin_count() == 0
