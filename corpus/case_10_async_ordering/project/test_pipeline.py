"""Dashboard panels are rendered top to bottom, so order is part of the output."""

from __future__ import annotations

import asyncio

from app.pipeline import render_panels

PANELS = ["header", "revenue", "latency", "errors", "footer"]


def test_panels_come_back_in_layout_order() -> None:
    rendered = asyncio.run(render_panels(PANELS))

    assert [entry.split(":")[0] for entry in rendered] == PANELS
