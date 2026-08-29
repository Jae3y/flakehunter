"""Deployment settings are read from whichever source answers first."""

from __future__ import annotations

from app.config import Source, resolve

#: Four sources agree on the current environment. One legacy source was left
#: enabled after a migration and still reports the old value.
SOURCES = {
    Source("env", True, {"environment": "production"}),
    Source("consul", True, {"environment": "production"}),
    Source("file", True, {"environment": "production"}),
    Source("defaults", True, {"environment": "production"}),
    Source("legacy", True, {"environment": "staging"}),
}


def test_environment_resolves_to_production() -> None:
    assert resolve("environment", SOURCES) == "production"
