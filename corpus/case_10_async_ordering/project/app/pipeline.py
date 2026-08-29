"""Fan-out rendering for the dashboard."""

from __future__ import annotations

import asyncio

#: Cost of rendering a panel, in loop iterations. Every panel costs the same,
#: so no panel is intrinsically "first to finish" -- completion order is
#: decided by thread scheduling alone, which is the nondeterminism this case
#: is about.
PANEL_WORK = 120_000


def _render(name: str, iterations: int) -> str:
    """Render one panel. CPU-bound, so it runs in a worker thread."""
    total = 0
    for index in range(iterations):
        total += index % 7
    return f"{name}:{total}"


async def render_panels(names: list[str]) -> list[str]:
    """Render every panel, collecting results as each finishes."""
    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, _render, name, PANEL_WORK) for name in names]

    rendered: list[str] = []
    for completed in asyncio.as_completed(tasks):
        rendered.append(await completed)
    return rendered
