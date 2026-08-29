"""Fan-out rendering for the dashboard."""

from __future__ import annotations

import asyncio

#: Base cost of rendering a panel, in loop iterations.
BASE_WORK = 30_000

#: Extra cost each successive panel carries. Later panels are heavier, so they
#: normally finish later -- which is exactly why completion order usually
#: matches layout order, and only sometimes does not.
WORK_STEP = 6_000


def _render(name: str, iterations: int) -> str:
    """Render one panel. CPU-bound, so it runs in a worker thread."""
    total = 0
    for index in range(iterations):
        total += index % 7
    return f"{name}:{total}"


async def render_panels(names: list[str]) -> list[str]:
    """Render every panel, collecting results as each finishes."""
    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, _render, name, BASE_WORK + position * WORK_STEP)
        for position, name in enumerate(names)
    ]

    rendered: list[str] = []
    for completed in asyncio.as_completed(tasks):
        rendered.append(await completed)
    return rendered
