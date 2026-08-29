"""The index must be usable once the indexer reports itself ready."""

from __future__ import annotations

from app.indexer import DOCUMENT_COUNT, BackgroundIndexer


def test_index_is_populated_once_ready() -> None:
    indexer = BackgroundIndexer()
    indexer.start()

    assert indexer.wait_until_ready(), "indexer never became ready"
    assert indexer.index is not None, "ready, but the index was not published"
    assert len(indexer.index) == DOCUMENT_COUNT
