"""DEC-124 on PostgreSQL: a write handler's savepoint on a caller's session.

The unit suite proves the shape on SQLite. Here the real driver: a failing handler keeps the
caller's uncommitted work, and `memory.write`, whose DAO commits inside the savepoint, still
succeeds (the savepoint is already closed by that commit, and the handler skips it).
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def pg():
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("savepoints are measured on the real driver")
    engine = create_engine(url)
    trace = f"dec124-{uuid.uuid4().hex[:10]}"
    from AINDY.db.models.user import User

    with Session(engine) as s:
        user = User(email=f"sp-{uuid.uuid4().hex[:8]}@aindy.test", username=f"sp-{uuid.uuid4().hex[:8]}",
                    hashed_password="x", is_active=True)
        s.add(user)
        s.commit()
        user_id = user.id
    try:
        yield engine, user_id, trace
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM memory_nodes WHERE user_id = :u"), {"u": user_id})
            c.execute(text("DELETE FROM system_events WHERE trace_id = :t"), {"t": trace})
            c.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        engine.dispose()


def _ctx(db, user_id, trace, capability):
    from AINDY.kernel.syscall_dispatcher import SyscallContext

    return SyscallContext(execution_unit_id=str(uuid.uuid4()), user_id=str(user_id), capabilities=[capability],
                          trace_id=trace, metadata={"_db": db})


def _caller_event(db, user_id, trace):
    from AINDY.db.models.system_event import SystemEvent

    db.add(SystemEvent(id=uuid.uuid4(), type="caller.pending", user_id=user_id, trace_id=trace, payload={}))
    db.flush()


def _types(engine, trace) -> set[str]:
    with engine.connect() as c:
        return {r[0] for r in c.execute(text("SELECT type FROM system_events WHERE trace_id = :t"), {"t": trace})}


def test_a_failing_handler_keeps_the_callers_work(pg, monkeypatch):
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO
    from AINDY.db.models.system_event import SystemEvent
    from AINDY.kernel.syscall_registry import _handle_memory_delete

    engine, user_id, trace = pg

    def _delete(self, node_id, user_id=None):
        self.db.add(SystemEvent(id=uuid.uuid4(), type="handler.partial", user_id=None, trace_id=trace, payload={}))
        self.db.flush()
        raise RuntimeError("the delete failed half way")

    monkeypatch.setattr(MemoryNodeDAO, "delete_by_id", _delete)
    with Session(engine) as db:
        _caller_event(db, user_id, trace)
        with pytest.raises(RuntimeError):
            _handle_memory_delete({"node_id": str(uuid.uuid4())}, _ctx(db, user_id, trace, "memory.delete"))
        db.commit()

    types = _types(engine, trace)
    assert "caller.pending" in types
    assert "handler.partial" not in types


def test_memory_write_whose_dao_commits_still_succeeds(pg):
    from AINDY.kernel.syscall_registry import _handle_memory_write

    engine, user_id, trace = pg
    with Session(engine) as db:
        _caller_event(db, user_id, trace)
        result = _handle_memory_write(
            {"content": "written inside a savepoint", "tags": ["dec124"], "node_type": "insight"},
            _ctx(db, user_id, trace, "memory.write"),
        )
        db.commit()

    assert result["node"]
    with engine.connect() as c:
        stored = c.execute(text("SELECT count(*) FROM memory_nodes WHERE user_id = :u"), {"u": user_id}).scalar_one()
    assert stored == 1
    assert "caller.pending" in _types(engine, trace)
