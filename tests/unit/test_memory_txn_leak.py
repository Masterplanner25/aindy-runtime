"""RT-MEMTXN-LEAK-1 — memory-node reads must not hold a DB connection across the slow
embedding API call (which pins it `idle in transaction` and exhausts the pool).

Covers the `release_read_transaction` guard and that `MemoryNodeDAO.recall` releases the
connection BEFORE generating the query embedding. End-to-end (no `idle in transaction`
under a real login) is app-side pg_stat_activity verification.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from AINDY.memory.embedding_service import release_read_transaction

pytestmark = pytest.mark.runtime_only


def _clean_db() -> MagicMock:
    db = MagicMock()
    db.new = []
    db.dirty = []
    db.deleted = []
    return db


def test_release_rolls_back_a_clean_read_session():
    db = _clean_db()
    release_read_transaction(db)
    db.rollback.assert_called_once()


def test_release_skips_when_session_has_pending_writes():
    db = _clean_db()
    db.dirty = [object()]  # a caller mid-write must never be rolled back
    release_read_transaction(db)
    db.rollback.assert_not_called()


def test_release_is_none_safe():
    release_read_transaction(None)  # must not raise


def test_release_swallows_rollback_errors():
    db = _clean_db()
    db.rollback.side_effect = RuntimeError("boom")
    release_read_transaction(db)  # pool hygiene, never a correctness gate → no raise


def test_recall_releases_connection_before_embedding(monkeypatch):
    """The connection must be released (rollback) BEFORE the slow embedding call."""
    order: list[str] = []
    monkeypatch.setattr(
        "AINDY.memory.embedding_service.release_read_transaction",
        lambda db: order.append("release"),
    )
    monkeypatch.setattr(
        "AINDY.memory.embedding_service.generate_query_embedding",
        lambda q: order.append("embed") or [0.0],
    )

    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    dao = MemoryNodeDAO.__new__(MemoryNodeDAO)  # bypass __init__ (no real session)
    dao.db = MagicMock()
    monkeypatch.setattr(dao, "_count_complete_embeddings", lambda **k: 0)
    monkeypatch.setattr(dao, "_find_text_matches", lambda **k: [])

    result = dao.recall(query="how did we handle auth", limit=3, user_id="u1")
    assert result == []
    assert order == ["release", "embed"]  # released the connection, THEN made the API call
