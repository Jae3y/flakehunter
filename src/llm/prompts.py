"""Prompt material shared by the baseline and the agent.

Both arms import from here. That is the point: the comparison is only
meaningful if the baseline and the agent are given the same taxonomy, the same
view of the project, and the same instructions about what a fix must be. The
one thing the baseline does not get is the repeat-execution harness, and that
absence is the independent variable under test -- so it must be the *only*
difference, which is easiest to guarantee when the shared material has exactly
one definition.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "ROOT_CAUSE_CLASSES",
    "FIX_RULES",
    "PATCH_SCHEMA",
    "render_project",
    "taxonomy_block",
]

#: The root-cause taxonomy, matching the `root_cause_class` field in every
#: case's metadata.json. Both arms classify into this same list, so
#: classification accuracy is comparable between them.
ROOT_CAUSE_CLASSES: dict[str, str] = {
    "race_condition": "Two or more threads touch shared state without synchronisation.",
    "test_order_dependency": "A test passes alone but fails after another mutates global state.",
    "resource_leak_port_collision": "A resource is checked then released before use, or never closed.",
    "clock_dependence": "Behaviour depends on the wall clock, a date boundary, or the timezone.",
    "hash_iteration_order": "Code assumes a stable iteration order over a set or dict.",
    "unseeded_randomness": "The code under test draws from an unseeded random source.",
    "network_timeout_no_retry": "A network call has a tight timeout and no retry or backoff.",
    "tempfile_collision": "Temporary paths are predictable, so concurrent writers collide.",
    "float_tolerance": "Floating-point results are compared exactly, or summed in a varying order.",
    "async_ordering": "Async or concurrent results are consumed in completion order, not submission order.",
    "cache_leak": "A cache or memo survives a state change it should have been invalidated by.",
    "publication_ordering": "State is published, or a ready flag set, before the value is fully constructed.",
}

#: Rules a fix must satisfy. Given to both arms so neither is penalised for
#: not knowing what will be checked.
FIX_RULES = """A fix must satisfy all of the following:

1. It must remove the nondeterminism, not hide it. Widening a timing window --
   adding a sleep, retrying an assertion until it passes, or increasing a
   timeout -- leaves the bug in place and is not a fix.
2. It must not weaken the test. Do not delete assertions, loosen them, make
   them trivially true, skip the test, mark it xfail, or add a retry decorator
   to it.
3. It must modify the source under test. A change confined to the test file is
   not a fix, with the narrow exception of a test that is itself wrong.
4. It must not modify conftest.py. That file is test infrastructure that
   supplies the conditions the bug appears under; changing it hides the bug.
5. It must be minimal. Change what the root cause requires and nothing else.

Note carefully: rule 1 forbids a retry that *masks* a race. It does not forbid
a retry that is itself the correct repair -- if the root cause is the absence
of retry or backoff around a fallible call, adding one is the fix."""

#: Schema both arms return, so their outputs are directly comparable.
PATCH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "root_cause_class": {
            "type": "string",
            "enum": list(ROOT_CAUSE_CLASSES),
        },
        "root_cause_explanation": {
            "type": "string",
            "description": "Why the test is nondeterministic, in two or three sentences.",
        },
        "confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "files": {
            "type": "array",
            "description": "Every file to rewrite, with its complete new contents.",
            "items": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the project root, e.g. app/counter.py",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "The complete new contents of the file.",
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["path", "new_content", "rationale"],
            },
        },
    },
    "required": ["root_cause_class", "root_cause_explanation", "confidence", "files"],
}


def taxonomy_block() -> str:
    """Render the taxonomy as prompt text."""
    lines = [f"  {name}: {description}" for name, description in ROOT_CAUSE_CLASSES.items()]
    return "Root cause classes:\n" + "\n".join(lines)


def render_project(project: Path) -> str:
    """Render every file in a case's project as labelled source blocks.

    Both arms get the same view: the whole project, nothing summarised.
    """
    blocks: list[str] = []
    for path in sorted(project.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(project)
        blocks.append(
            f"--- FILE: {relative} ---\n{path.read_text(encoding='utf-8')}"
        )
    return "\n\n".join(blocks)
