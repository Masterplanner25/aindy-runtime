"""RT-MEMTXN-LEAK-1 — memory recall must not hold a DB connection across the slow embedding
API call (which pins it `idle in transaction` and exhausts the pool).

The safe fix is a REORDER: `MemoryNodeDAO.recall` generates the query embedding BEFORE any DB
query in the method, so the request-shared session holds no pooled connection during the
~seconds embedding API call. (We deliberately do NOT rollback the shared session to release
it — that would discard the request's in-flight Core-level updates.) End-to-end (no
`idle in transaction` under a real login) is app-side pg_stat_activity verification.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.runtime_only


def test_recall_generates_embedding_before_touching_the_db(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(
        "AINDY.memory.embedding_service.generate_query_embedding",
        lambda q: order.append("embed") or [0.0],
    )

    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    dao = MemoryNodeDAO.__new__(MemoryNodeDAO)  # bypass __init__ (no real session)
    dao.db = MagicMock()
    monkeypatch.setattr(dao, "_count_complete_embeddings", lambda **k: order.append("db_count") or 0)
    monkeypatch.setattr(dao, "_find_text_matches", lambda **k: [])

    result = dao.recall(query="how did we handle auth", limit=3, user_id="u1")
    assert result == []
    # The slow embedding API call happens BEFORE the first DB query in recall, so no pooled
    # connection is held open across it.
    assert order and order[0] == "embed"
    assert "db_count" in order and order.index("embed") < order.index("db_count")


def test_embedding_job_releases_connection_before_embedding(monkeypatch):
    """RT-MEMTXN-LEAK-1 follow-up — the embedding-write fan-out.

    `queue_system_event(required=True)` commits, expiring memory_node; reading `.content`
    then triggers a refresh SELECT that opens a FRESH transaction, and generate_embedding()
    used to run (seconds) with it open — one held connection per queued job, dozens per
    request, exhausting the pool. The job must commit (release the connection) BEFORE
    embedding.
    """
    import uuid as _uuid

    order: list[str] = []

    node = MagicMock()
    node.content = "remember this"
    node.id = _uuid.uuid4()
    node.extra = {}
    node.source_event_id = None
    node.embedding_pending = True
    node.embedding_status = "pending"
    node.user_id = "u1"

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = node
    db.commit.side_effect = lambda: order.append("commit")

    monkeypatch.setattr("AINDY.memory.embedding_jobs.queue_system_event", lambda **k: "evt-1")
    monkeypatch.setattr(
        "AINDY.memory.embedding_jobs.generate_embedding",
        lambda text: order.append(f"embed:{text}") or [0.1, 0.2],
    )

    from AINDY.memory import embedding_jobs

    embedding_jobs.process_embedding_job({"memory_id": str(node.id), "trace_id": "t"}, db)

    # The connection is returned to the pool (commit) BEFORE the slow embedding API call.
    assert order[0] == "commit"
    assert order[1] == "embed:remember this"  # embedded from a captured local, not a lazy attr


def test_recall_does_not_rollback_the_shared_session(monkeypatch):
    """The fix must never rollback the request-shared session (that discards in-flight writes)."""
    monkeypatch.setattr(
        "AINDY.memory.embedding_service.generate_query_embedding", lambda q: [0.0]
    )
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    dao = MemoryNodeDAO.__new__(MemoryNodeDAO)
    dao.db = MagicMock()
    monkeypatch.setattr(dao, "_count_complete_embeddings", lambda **k: 0)
    monkeypatch.setattr(dao, "_find_text_matches", lambda **k: [])

    dao.recall(query="x", limit=3, user_id="u1")
    dao.db.rollback.assert_not_called()
