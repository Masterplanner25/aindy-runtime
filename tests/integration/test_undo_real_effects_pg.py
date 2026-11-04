"""UNDO-COMPENSATORS-1 on real Postgres: what the SQLite suite cannot prove.

`tests/unit/test_undo_finds_real_effects.py` runs with foreign keys off. Here the foreign key from
`effect_records.execution_id` is live, a compensator's savepoint is a real one, and the delete's
`ON DELETE CASCADE` fires. Nothing writes `execution_id` by hand.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

_WRITE = "sys.v1.memory.write"


class _OkResourceManager:
    def check_quota(self, _unit):
        return True, None

    def record_usage(self, _unit, _usage):
        return None


@pytest.fixture
def world(testing_session_factory, monkeypatch):
    """A real user and an agent run's unit, with the gate's own sessions on this database."""
    from AINDY.db.models import ExecutionUnit
    from AINDY.db.models.user import User
    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.services.auth_service import hash_password

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    suffix = uuid.uuid4().hex[:10]
    run_id = str(uuid.uuid4())
    session = testing_session_factory()
    user = User(
        email=f"undo-{suffix}@aindy.test", username=f"undo-{suffix}",
        hashed_password=hash_password("x"), is_active=True,
    )
    session.add(user)
    session.commit()
    unit = ExecutionUnit(
        type="agent", status="completed", user_id=user.id,
        source_type="agent_run", source_id=run_id, priority="normal",
    )
    session.add(unit)
    session.commit()
    facts = {"run_id": run_id, "user_id": str(user.id), "unit_id": unit.id, "session": session}
    with patch("AINDY.db.database.SessionLocal", testing_session_factory), patch.object(
        D, "_get_rm", lambda: _OkResourceManager()
    ), patch("AINDY.db.dao.memory_node_dao.MemoryNodeDAO._enqueue_embedding", lambda *a, **k: None):
        yield facts
    session.close()


def _dispatch_write(world, content, *, unit=None):
    from AINDY.kernel.syscall_dispatcher import SyscallContext, SyscallDispatcher

    dispatcher = SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **kw: None
    return dispatcher.dispatch(
        _WRITE, {"content": content},
        SyscallContext(
            execution_unit_id=str(unit or uuid.uuid4()), user_id=world["user_id"],
            capabilities=["memory.write"], trace_id=world["run_id"],
        ),
    )


def _write_in_run(world, content):
    from AINDY.kernel.effect_ledger import effect_run_scope

    with effect_run_scope(world["run_id"]):
        envelope = _dispatch_write(world, content)
    assert envelope["status"] == "success", envelope["error"]
    return envelope["data"]["node"]["id"]


def _exists(factory, node_id) -> bool:
    from AINDY.memory.memory_persistence import MemoryNodeModel

    fresh = factory()
    try:
        return fresh.query(MemoryNodeModel).filter(MemoryNodeModel.id == uuid.UUID(node_id)).first() is not None
    finally:
        fresh.close()


def test_a_real_write_is_linked_found_and_deleted_durably(world, testing_session_factory):
    from AINDY.core.effect_compensation import undo_run_effects
    from AINDY.db.models import EffectRecord, EffectReversal

    node_id = _write_in_run(world, "written by the run")
    db = world["session"]
    linked = db.query(EffectRecord).filter(
        EffectRecord.execution_id == world["unit_id"], EffectRecord.action_type == _WRITE
    ).one()

    summary = undo_run_effects(world["run_id"], db=db)

    assert summary["reversed"] == [_WRITE], summary
    # Read from a new session: a flush would look the same as a commit on this one (variant 15).
    assert not _exists(testing_session_factory, node_id)
    fresh = testing_session_factory()
    try:
        audit = fresh.query(EffectReversal).filter(EffectReversal.effect_record_id == linked.id).one()
        assert audit.status == "reversed" and audit.receipt["deleted"] is True
    finally:
        fresh.close()


def test_a_refusal_keeps_the_session_usable_for_the_next_effect(world, testing_session_factory):
    """On Postgres a statement error poisons the transaction unless a savepoint contains it."""
    from AINDY.core.effect_compensation import undo_run_effects
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    db = world["session"]
    kept = _write_in_run(world, "another node links here")
    gone = _write_in_run(world, "nothing built on this one")
    outside = MemoryNodeDAO(db).save(content="outside the run", user_id=world["user_id"], node_type="insight")
    MemoryNodeDAO(db).create_link(outside["id"], kept, "related", 0.5, user_id=world["user_id"])
    db.commit()

    summary = undo_run_effects(world["run_id"], db=db)

    assert summary["reversed"] == [_WRITE] and len(summary["failed"]) == 1, summary
    assert _exists(testing_session_factory, kept)
    assert not _exists(testing_session_factory, gone)


def test_the_delete_cascades_to_the_nodes_causal_edges(world, testing_session_factory):
    """DEC-109 lets an edge go with its node, so the cascade it relies on has to be real."""
    from AINDY.core.effect_compensation import undo_run_effects
    from AINDY.db.models.event_edge import EventEdge
    from AINDY.db.models.system_event import SystemEvent

    db = world["session"]
    node_id = _write_in_run(world, "has a causal edge")
    source_event = SystemEvent(type="test.undo.cause", user_id=uuid.UUID(world["user_id"]), payload={})
    db.add(source_event)
    db.flush()
    edge = EventEdge(
        source_event_id=source_event.id,
        target_memory_node_id=uuid.UUID(node_id),
        relationship_type="caused_by",
    )
    db.add(edge)
    db.commit()
    edge_id = edge.id

    assert undo_run_effects(world["run_id"], db=db)["reversed"] == [_WRITE]

    fresh = testing_session_factory()
    try:
        assert fresh.query(EventEdge).filter(EventEdge.id == edge_id).first() is None
    finally:
        fresh.close()


def test_a_prefixed_run_scope_cannot_poison_the_gate(world):
    """`run_<uuid>` engages the gate but is not a UUID. It must not reach a UUID comparison (#157)."""
    from AINDY.db.models import EffectRecord

    scope = f"run_{uuid.uuid4()}"
    envelope = _dispatch_write(world, "under a prefixed scope", unit=scope)

    assert envelope["status"] == "success", envelope["error"]
    db = world["session"]
    db.expire_all()
    row = db.query(EffectRecord).filter(EffectRecord.tenant_id == world["user_id"]).one()
    assert row.status == "success" and row.execution_id is None
