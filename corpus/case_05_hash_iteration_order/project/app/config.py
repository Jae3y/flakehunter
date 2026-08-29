"""Configuration resolution across several enabled sources."""

from __future__ import annotations


class Source:
    """One place a setting can come from."""

    def __init__(self, name: str, enabled: bool, values: dict[str, str]) -> None:
        self.name = name
        self.enabled = enabled
        self.values = values

    def __repr__(self) -> str:
        return f"Source({self.name!r})"


def resolve(setting: str, sources: set[Source]) -> str:
    """Return ``setting`` from the first enabled source that defines it.

    Sources are held in a set because order was never thought to matter --
    every source is supposed to agree.
    """
    for source in sources:
        if source.enabled and setting in source.values:
            return source.values[setting]
    raise KeyError(setting)
