"""AGENT-HARDEN-3 — compensating-undo engine + sys.v1.agent.undo.

Reversal walks a run's successful EffectRecords newest-first, invoking each
syscall's registered ``compensate`` hook; effects without one are surfaced as
``irreversible`` (not skipped) and compensator errors as ``failed``. Every attempt
is written to the append-only ``effect_reversals`` audit log.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import tests.fixtures.db  # noqa: F401  — registers JSONB/UUID/Vector SQLite compilers
import AINDY.db.model_registry  # noqa: F401  — populate metadata
from AINDY.core.effect_compensation import undo_run_effects
from AINDY.db.database import Base
from AINDY.kernel.syscall_registry import (
    SYSCALL_REGISTRY,
    SyscallContext,
    SyscallEntry,
    _handle_agent_undo,
    register_syscall,
)

pytestmark = pytest.mark.runtime_only

_REVERSIBLE = "sys.v1.test.reversible"
_IRREVERSIBLE = "sys.v1.test.irreversible"
_BOOM = "sys.v1.test.boom"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_off(dbapi_connection, _rec):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.close()

    for tbl in ("execution_units", "effect_records", "effect_reversals", "agent_runs"):
        Base.metadata.tables[tbl].create(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def compensators():
    """Register test syscalls (one reversible, one raising) and clean up."""
    calls = []

    def _reversible(effect, _context):
        calls.append(effect)
        return {"undone": (effect.get("result_payload") or {}).get("id")}

    def _boom(_effect, _context):
        raise RuntimeError("compensator exploded")

    register_syscall(_REVERSIBLE, handler=lambda p, c: {"ok": True}, capability="test", compensate=_reversible)
    register_syscall(_IRREVERSIBLE, handler=lambda p, c: {"ok": True}, capability="test")
    register_syscall(_BOOM, handler=lambda p, c: {"ok": True}, capability="test", compensate=_boom)
    try:
        yield calls
    finally:
        for name in (_REVERSIBLE, _IRREVERSIBLE, _BOOM):
            try:
                del SYSCALL_REGISTRY[name]
            except Exception:
                pass


def _seed_eu(db, *, user_id, run_id):
    from AINDY.db.models import ExecutionUnit

    eu = ExecutionUnit(
        type="agent", status="completed", user_id=user_id,
        source_type="agent_run", source_id=str(run_id), priority="normal",
    )
    db.add(eu)
    db.flush()
    return eu


def _seed_effect(db, *, eu, action_type, status="success", result=None, created_at=None):
    from AINDY.db.models import EffectRecord

    rec = EffectRecord(
        action_id=uuid.uuid4().hex,
        action_type=action_type,
        input_hash="h",
        execution_id=eu.id,
        status=status,
        result_payload=result,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(rec)
    db.flush()
    return rec


def _reversals(db):
    from AINDY.db.models import EffectReversal

    return db.query(EffectReversal).all()


# --------------------------------------------------------------------------- #
# undo_run_effects — core engine
# --------------------------------------------------------------------------- #

def test_reversible_effect_is_compensated_and_logged(session, compensators):
    run_id = uuid.uuid4()
    eu = _seed_eu(session, user_id=uuid.uuid4(), run_id=run_id)
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, result={"id": "node-1"})
    session.commit()

    summary = undo_run_effects(str(run_id), db=session)

    assert summary["reversed"] == [_REVERSIBLE]
    assert summary["irreversible"] == [] and summary["failed"] == []
    assert len(compensators) == 1  # compensator invoked once
    rows = _reversals(session)
    assert len(rows) == 1
    assert rows[0].status == "reversed"
    assert rows[0].run_id == str(run_id)
    assert rows[0].receipt == {"undone": "node-1"}


def test_effect_without_compensator_is_irreversible(session, compensators):
    run_id = uuid.uuid4()
    eu = _seed_eu(session, user_id=uuid.uuid4(), run_id=run_id)
    _seed_effect(session, eu=eu, action_type=_IRREVERSIBLE, result={"sent": True})
    session.commit()

    summary = undo_run_effects(str(run_id), db=session)

    assert summary["irreversible"] == [_IRREVERSIBLE]
    assert summary["reversed"] == []
    rows = _reversals(session)
    assert rows[0].status == "irreversible"
    assert "no compensator" in (rows[0].detail or "")


def test_compensator_failure_is_recorded_and_isolated(session, compensators):
    run_id = uuid.uuid4()
    eu = _seed_eu(session, user_id=uuid.uuid4(), run_id=run_id)
    now = datetime.now(timezone.utc)
    # boom is newer → processed first; reversible is older → still processed after.
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, result={"id": "n"}, created_at=now - timedelta(seconds=5))
    _seed_effect(session, eu=eu, action_type=_BOOM, created_at=now)
    session.commit()

    summary = undo_run_effects(str(run_id), db=session)

    assert summary["failed"] and summary["failed"][0]["action_type"] == _BOOM
    assert "exploded" in summary["failed"][0]["error"]
    assert summary["reversed"] == [_REVERSIBLE]  # the other effect still ran
    statuses = sorted(r.status for r in _reversals(session))
    assert statuses == ["failed", "reversed"]


def test_effects_reversed_newest_first(session, compensators):
    run_id = uuid.uuid4()
    eu = _seed_eu(session, user_id=uuid.uuid4(), run_id=run_id)
    now = datetime.now(timezone.utc)
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, result={"id": "old"}, created_at=now - timedelta(seconds=10))
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, result={"id": "new"}, created_at=now)
    session.commit()

    undo_run_effects(str(run_id), db=session)

    # Compensator saw the newest effect first (reverse execution order).
    assert [(c["result_payload"] or {}).get("id") for c in compensators] == ["new", "old"]


def test_only_successful_effects_are_undone(session, compensators):
    run_id = uuid.uuid4()
    eu = _seed_eu(session, user_id=uuid.uuid4(), run_id=run_id)
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, status="pending")
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, status="failed")
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, status="success", result={"id": "ok"})
    session.commit()

    summary = undo_run_effects(str(run_id), db=session)

    assert summary["reversed"] == [_REVERSIBLE]  # only the successful one
    assert len(_reversals(session)) == 1


def test_no_execution_unit_returns_error_summary(session, compensators):
    summary = undo_run_effects(str(uuid.uuid4()), db=session)
    assert summary["reversed"] == [] and summary["irreversible"] == []
    assert "no execution unit" in summary.get("error", "")


# --------------------------------------------------------------------------- #
# SyscallEntry.reversible
# --------------------------------------------------------------------------- #

def test_syscall_entry_reversible_flag():
    assert SyscallEntry(handler=lambda p, c: {}, capability="c").reversible is False
    assert SyscallEntry(handler=lambda p, c: {}, capability="c", compensate=lambda e, c: None).reversible is True
    assert SYSCALL_REGISTRY["sys.v1.agent.undo"].capability == "agent.undo"


# --------------------------------------------------------------------------- #
# sys.v1.agent.undo handler — tenant scope + validation
# --------------------------------------------------------------------------- #

def _ctx(user_id, db):
    return SyscallContext(
        execution_unit_id="eu", user_id=str(user_id),
        capabilities=["agent.undo"], trace_id="t", metadata={"_db": db},
    )


def _seed_agent_run(db, user_id):
    from AINDY.db.models import AgentRun

    run = AgentRun(id=uuid.uuid4(), user_id=user_id, goal="g", status="completed", steps_total=0)
    db.add(run)
    db.commit()
    return run


def test_undo_handler_happy_path(session, compensators):
    user_id = uuid.uuid4()
    run = _seed_agent_run(session, user_id)
    eu = _seed_eu(session, user_id=user_id, run_id=run.id)
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, result={"id": "z"})
    session.commit()

    result = _handle_agent_undo({"run_id": str(run.id)}, _ctx(user_id, session))
    assert result["reversed"] == [_REVERSIBLE]


def test_undo_handler_cross_tenant_denied(session, compensators):
    owner, other = uuid.uuid4(), uuid.uuid4()
    run = _seed_agent_run(session, owner)
    with pytest.raises(ValueError, match="no agent run"):
        _handle_agent_undo({"run_id": str(run.id)}, _ctx(other, session))


def test_undo_handler_missing_run_id(session):
    with pytest.raises(ValueError, match="requires 'run_id'"):
        _handle_agent_undo({}, _ctx(uuid.uuid4(), session))


def test_undo_handler_invalid_run_id(session):
    with pytest.raises(ValueError, match="invalid run_id"):
        _handle_agent_undo({"run_id": "nope"}, _ctx(uuid.uuid4(), session))
