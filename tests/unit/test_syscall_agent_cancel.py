"""Unit tests for the sys.v1.agent.cancel syscall handler (AGENT-HARDEN-1).

Covers the operator kill-switch contract: atomic non-terminal → ``cancelled``
CAS, idempotent no-op on already-terminal runs, wait_state clearing, tenant
isolation, and input validation. The cooperative mid-plan halt (the close
trigger) is exercised in test_agent_vm_execution.py against the segment chain.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from AINDY.db.database import Base
import AINDY.db.model_registry  # noqa: F401  (populate metadata)
from AINDY.kernel.syscall_registry import (
    SYSCALL_REGISTRY,
    SyscallContext,
    _handle_agent_cancel,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.tables["agent_runs"].create(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _mute_event(monkeypatch):
    """Isolate the handler from the system-event store; capture emitted events."""
    events = []
    monkeypatch.setattr(
        "AINDY.core.execution_signal_helper.record_agent_event",
        lambda **kw: events.append(kw),
    )
    return events


def _ctx(user_id: str, db) -> SyscallContext:
    return SyscallContext(
        execution_unit_id="eu-test",
        user_id=user_id,
        capabilities=["agent.cancel"],
        trace_id="trace-test",
        metadata={"_db": db},
    )


def _make_run(db, *, user_id=None, status="executing", wait_state=None):
    from AINDY.db.models import AgentRun

    run = AgentRun(
        id=uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        goal="g",
        status=status,
        steps_total=1,
        wait_state=wait_state,
    )
    db.add(run)
    db.commit()
    return run


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def test_registered_with_agent_cancel_capability():
    entry = SYSCALL_REGISTRY["sys.v1.agent.cancel"]
    assert entry.capability == "agent.cancel"
    assert entry.stable is True


# --------------------------------------------------------------------------- #
# Happy path — cancel an active run
# --------------------------------------------------------------------------- #

def test_cancel_executing_run(session, _mute_event):
    run = _make_run(session, status="executing")

    result = _handle_agent_cancel(
        {"run_id": str(run.id)}, _ctx(str(run.user_id), session)
    )

    assert result["cancelled"] is True
    assert result["previous_status"] == "executing"
    assert result["status"] == "cancelled"

    session.refresh(run)
    assert run.status == "cancelled"
    assert run.completed_at is not None
    assert run.error_message == "cancelled"
    # One CANCELLED lifecycle event emitted.
    assert [e["event_type"] for e in _mute_event] == ["CANCELLED"]


def test_cancel_records_reason(session):
    run = _make_run(session, status="approved")

    result = _handle_agent_cancel(
        {"run_id": str(run.id), "reason": "operator halt"},
        _ctx(str(run.user_id), session),
    )

    assert result["cancelled"] is True
    session.refresh(run)
    assert run.error_message == "cancelled: operator halt"


def test_cancel_waiting_run_clears_wait_state(session):
    run = _make_run(
        session, status="waiting", wait_state={"event_type": "x", "resume_segment_index": 1}
    )

    result = _handle_agent_cancel(
        {"run_id": str(run.id)}, _ctx(str(run.user_id), session)
    )

    assert result["cancelled"] is True
    session.refresh(run)
    assert run.status == "cancelled"
    assert run.wait_state is None


# --------------------------------------------------------------------------- #
# Idempotent no-op on already-terminal runs
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_cancel_terminal_run_is_noop(session, terminal, _mute_event):
    run = _make_run(session, status=terminal)

    result = _handle_agent_cancel(
        {"run_id": str(run.id)}, _ctx(str(run.user_id), session)
    )

    assert result["cancelled"] is False
    assert result["status"] == terminal
    session.refresh(run)
    assert run.status == terminal  # untouched
    assert _mute_event == []  # no event for a no-op


# --------------------------------------------------------------------------- #
# Validation + isolation
# --------------------------------------------------------------------------- #

def test_missing_run_id_raises(session):
    with pytest.raises(ValueError, match="requires 'run_id'"):
        _handle_agent_cancel({}, _ctx(str(uuid.uuid4()), session))


def test_invalid_run_id_raises(session):
    with pytest.raises(ValueError, match="invalid run_id"):
        _handle_agent_cancel(
            {"run_id": "not-a-uuid"}, _ctx(str(uuid.uuid4()), session)
        )


def test_cross_tenant_cancel_denied(session):
    owner = uuid.uuid4()
    other = uuid.uuid4()
    run = _make_run(session, user_id=owner, status="executing")

    # A different tenant must not be able to cancel the owner's run.
    with pytest.raises(ValueError, match="no agent run"):
        _handle_agent_cancel({"run_id": str(run.id)}, _ctx(str(other), session))

    session.refresh(run)
    assert run.status == "executing"  # untouched
