"""`causal_trace` follows a chain only through the caller's own nodes (MEM-TRACE-SCOPE-1).

The first lookup was scoped to the caller, and every hop after it was not, so a shared event id
returned another user's node. The handler is driven for real because the leak is what it returns.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

OWNER = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
SECRET = "another user's private note"


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "trace_scope.db")
    session_factory = sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )
    monkeypatch.setattr("AINDY.db.database.SessionLocal", session_factory)
    try:
        yield session_factory
    finally:
        engine.dispose()


def _node(factory, user_id, content, *, source_event=None, root_event=None):
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    path = f"/memory/{user_id}/probe/note/{uuid.uuid4()}"
    db = factory()
    try:
        MemoryNodeDAO(db).save(
            content=content,
            user_id=user_id,
            node_type="insight",
            generate_embedding=False,
            source_event_id=source_event,
            root_event_id=root_event,
            path=path,
        )
        db.commit()
    finally:
        db.close()
    return path


def _trace(path, user_id):
    from AINDY.kernel.syscall_dispatcher import dispatch_syscall

    result = dispatch_syscall(
        "sys.v1.memory.trace",
        {"path": path},
        user_id=user_id,
        capability="memory.read",
        execution_unit_id=str(uuid.uuid4()),
    )
    assert result["status"] == "success", result
    return result["data"]["chain"]


def test_the_chain_never_crosses_into_another_users_node(factory):
    shared_event = str(uuid.uuid4())
    _node(factory, OTHER, SECRET, root_event=shared_event)
    start = _node(factory, OWNER, "my note", source_event=shared_event)

    chain = _trace(start, OWNER)

    assert [node["content"] for node in chain] == ["my note"]


def test_control_the_chain_still_follows_the_callers_own_nodes(factory):
    """Liveness: the test above must not pass because following a chain stopped working."""
    shared_event = str(uuid.uuid4())
    _node(factory, OWNER, "my earlier note", root_event=shared_event)
    start = _node(factory, OWNER, "my note", source_event=shared_event)

    chain = _trace(start, OWNER)

    assert [node["content"] for node in chain] == ["my note", "my earlier note"]


def test_the_callers_own_ancestor_is_found_beside_another_users(factory):
    """Both match the event. An unscoped `.first()` could return either one."""
    shared_event = str(uuid.uuid4())
    _node(factory, OTHER, SECRET, root_event=shared_event)
    _node(factory, OWNER, "my earlier note", root_event=shared_event)
    start = _node(factory, OWNER, "my note", source_event=shared_event)

    chain = _trace(start, OWNER)

    assert [node["content"] for node in chain] == ["my note", "my earlier note"]
