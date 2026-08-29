"""Process-wide registry of active plugins."""

from __future__ import annotations

#: Module-level registry, shared by everything in the process.
_PLUGINS: list[str] = []


def register(name: str) -> None:
    """Add ``name`` to the active plugin registry."""
    _PLUGINS.append(name)


def reset() -> None:
    """Empty the registry."""
    _PLUGINS.clear()


def registered() -> list[str]:
    """The plugins currently registered."""
    return list(_PLUGINS)


def plugin_count() -> int:
    """How many plugins are registered."""
    return len(_PLUGINS)
