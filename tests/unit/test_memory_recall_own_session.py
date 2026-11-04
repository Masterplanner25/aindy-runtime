"""
DB-NODUS-BUDGET-1 (real fix) — recall must not open a transaction on the caller's session.

Verified against real Postgres: a `memory_nodes` SELECT issued on the flow runner's
session leaves that session inside a transaction, and the connection then sits
idle-in-transaction for the whole of node execution. With the nodus ceiling at 45s and
the DB idle cap below it, Postgres terminates the connection mid-run.

The caller's transaction cannot simply be rolled back afterwards — RT-MEMTXN-LEAK-1
tried that (`release_read_transaction`) and it broke `test_agent_approve_idempotency`,
because `session.dirty` does not see Core `db.execute(UPDATE)` or outer transactions.
So the read gets its own short-lived session instead: no transaction is started on the
caller's session at all.

Default ON (DEC-106); only `AINDY_MEMORY_RECALL_OWN_SESSION` = 0/false/no/off disables it.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from AINDY.runtime.memory.orchestrator import MemoryOrchestrator


pytestmark = pytest.mark.runtime_only

_VAR = "AINDY_MEMORY_RECALL_OWN_SESSION"


def _resolve(db):
    return MemoryOrchestrator._resolve_read_session(db)


# ── explicitly off — the caller's session ───────────────────────────────────

@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " No "])
def test_an_explicit_off_uses_the_callers_session(monkeypatch, value):
    monkeypatch.setenv(_VAR, value)
    caller = MagicMock()
    session, owns = _resolve(caller)
    assert session is caller and owns is False


# ── on by default (DEC-106) — dedicated read session ────────────────────────

def test_unset_returns_an_owned_session(monkeypatch):
    monkeypatch.delenv(_VAR, raising=False)
    own = MagicMock(name="own_session")
    caller = MagicMock(name="caller_session")
    with patch("AINDY.db.database.SessionLocal", return_value=own):
        session, owns = _resolve(caller)
    assert session is own, "recall must not run on the caller's session by default"
    assert owns is True


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", " Yes ", "", "  ", "banana"])
def test_any_other_value_returns_an_owned_session(monkeypatch, value):
    monkeypatch.setenv(_VAR, value)
    own = MagicMock(name="own_session")
    caller = MagicMock(name="caller_session")
    with patch("AINDY.db.database.SessionLocal", return_value=own):
        session, owns = _resolve(caller)
    assert session is own, "recall must not run on the caller's session when enabled"
    assert owns is True, "the caller of _resolve_read_session must know to close it"


def test_falls_back_to_caller_when_session_factory_raises(monkeypatch):
    """Recall must never become unavailable because the read session could not be made."""
    monkeypatch.setenv(_VAR, "1")
    caller = MagicMock(name="caller_session")
    with patch("AINDY.db.database.SessionLocal", side_effect=RuntimeError("no engine")):
        session, owns = _resolve(caller)
    assert session is caller
    assert owns is False, "must not claim ownership of a session it did not create"


# ── the session is actually released ────────────────────────────────────────

def _run_get_context(orch, db):
    return orch.get_context(user_id="u1", query="q", db=db, task_type="analysis")


def test_owned_session_is_closed_after_a_successful_recall(monkeypatch):
    monkeypatch.setenv(_VAR, "1")
    own = MagicMock(name="own_session")
    orch = MemoryOrchestrator(dao=lambda _db: None)  # no dao -> empty candidates, still exercises the path
    with patch("AINDY.db.database.SessionLocal", return_value=own):
        _run_get_context(orch, MagicMock())
    own.close.assert_called_once()


def test_owned_session_is_closed_even_when_recall_raises(monkeypatch):
    """The close lives in `finally` — a failing recall must not leak the connection."""
    monkeypatch.setenv(_VAR, "1")
    own = MagicMock(name="own_session")

    def _exploding_dao(_db):
        raise RuntimeError("boom")

    orch = MemoryOrchestrator(dao=_exploding_dao)
    with patch("AINDY.db.database.SessionLocal", return_value=own):
        ctx = _run_get_context(orch, MagicMock())
    own.close.assert_called_once()
    assert ctx is not None, "a failed recall still returns an empty context, not an exception"


def test_callers_session_is_never_closed(monkeypatch):
    """With the flag off we borrow the caller's session — closing it would be a bug."""
    monkeypatch.setenv(_VAR, "false")
    caller = MagicMock(name="caller_session")
    orch = MemoryOrchestrator(dao=lambda _db: None)
    _run_get_context(orch, caller)
    caller.close.assert_not_called()


# ── what the default changes: the documented cost, on a real engine ─────────

def test_recall_does_not_see_the_callers_uncommitted_write(monkeypatch, tmp_path):
    """DEC-106's stated behaviour change, on two connections: a flush is not a commit here."""
    import uuid

    from sqlalchemy.orm import sessionmaker

    from AINDY.memory.memory_persistence import MemoryNodeModel
    from tests.fixtures.db import build_private_engine

    engine = build_private_engine(tmp_path / "own_session.db")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", factory)
    monkeypatch.delenv(_VAR, raising=False)
    node_id = uuid.uuid4()

    def _visible(session) -> int:
        return session.query(MemoryNodeModel).filter(MemoryNodeModel.id == node_id).count()

    caller = factory()
    try:
        caller.add(MemoryNodeModel(id=node_id, content="not yet committed", node_type="insight"))
        caller.flush()
        assert _visible(caller) == 1, "liveness: the caller's own session sees its flushed row"

        own, owns = _resolve(caller)
        assert owns is True and own is not caller
        assert _visible(own) == 0, "the default read session must not see an uncommitted write"
        own.close()

        caller.commit()
        own, _ = _resolve(caller)
        assert _visible(own) == 1, "once committed, the read session sees it"
        own.close()
    finally:
        caller.close()
        engine.dispose()
