"""RTR-4 — multi-agent delegation hardening (gaps a + b).

Gap (b) per-delegate capability narrowing: a delegated child token is clamped to
the intersection of the parent's grant and the delegate's registered capabilities
(least privilege; no escalation via delegation).

Gap (a) approval handshake: under ``AINDY_DELEGATION_HANDSHAKE`` a child is held at
``awaiting_delegation`` until the delegate accepts (→ ``approved``) or rejects
(→ ``failed``, and the waiting parent is failed too). Gap (c), token-scoped
private memory, is deferred to a follow-up.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import tests.fixtures.db  # noqa: F401  — registers JSONB/UUID/Vector SQLite compilers
import AINDY.db.model_registry  # noqa: F401  — populate metadata
from AINDY.agents import agent_coordinator as coord
from AINDY.agents import capability_service as cap
from AINDY.db.database import Base

pytestmark = pytest.mark.runtime_only


# ── Gap (b): capability ceiling ───────────────────────────────────────────────

class _FakeParent:
    def __init__(self, caps):
        self.capability_token = {"allowed_capabilities": list(caps)}


def test_ceiling_is_parent_intersect_delegate():
    parent = _FakeParent(["cap.a", "cap.b", "cap.c"])
    selected = {"agent_id": str(uuid.uuid4()), "capabilities": ["cap.b", "cap.c", "cap.d"]}
    ceiling = coord._delegate_capability_ceiling(None, parent_run=parent, selected_agent=selected)
    assert ceiling == ["cap.b", "cap.c"]  # never widens beyond parent; excludes cap.d


def test_ceiling_falls_back_to_parent_when_delegate_undeclared():
    parent = _FakeParent(["cap.a", "cap.b"])
    selected = {"agent_id": str(uuid.uuid4())}  # no capabilities and no db row
    ceiling = coord._delegate_capability_ceiling(None, parent_run=parent, selected_agent=selected)
    assert ceiling == ["cap.a", "cap.b"]


def test_ceiling_falls_back_to_delegate_when_parent_unknown():
    parent = _FakeParent([])  # parent grant unknown
    selected = {"agent_id": str(uuid.uuid4()), "capabilities": ["cap.x"]}
    ceiling = coord._delegate_capability_ceiling(None, parent_run=parent, selected_agent=selected)
    assert ceiling == ["cap.x"]


def test_mint_token_clamps_allowed_capabilities_to_ceiling():
    plan = {"steps": [{"tool": "toolA"}]}
    with (
        patch.object(cap, "get_grantable_tools", return_value=["toolA"]),
        patch.object(cap, "get_plan_required_capabilities", return_value=["cap.a", "cap.b", "cap.c"]),
        patch.object(cap, "create_run_capability_mappings"),
        patch.object(cap, "_get_capabilities_for_tool", return_value=["cap.a"]),
    ):
        token = cap.mint_token(
            run_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            plan=plan,
            db=None,
            approval_mode="manual",
            agent_type="default",
            capability_ceiling=["cap.a", "cap.b"],
        )
    assert token is not None
    assert token["allowed_capabilities"] == ["cap.a", "cap.b"]  # cap.c dropped
    assert token["granted_tools"] == ["toolA"]  # kept: cap.a ∈ ceiling


def test_mint_token_empty_ceiling_yields_no_token():
    plan = {"steps": [{"tool": "toolA"}]}
    with (
        patch.object(cap, "get_grantable_tools", return_value=["toolA"]),
        patch.object(cap, "get_plan_required_capabilities", return_value=["cap.a"]),
        patch.object(cap, "create_run_capability_mappings"),
        patch.object(cap, "_get_capabilities_for_tool", return_value=["cap.a"]),
    ):
        token = cap.mint_token(
            run_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            plan=plan,
            db=None,
            approval_mode="manual",
            agent_type="default",
            capability_ceiling=[],
        )
    assert token is None


# ── Gap (a): approval handshake ───────────────────────────────────────────────

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

    for tbl in ("agent_runs",):
        Base.metadata.tables[tbl].create(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _mk_run(db, *, status, parent_run_id=None):
    from AINDY.db.models.agent_run import AgentRun

    run = AgentRun(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        goal="delegated task",
        status=status,
        parent_run_id=parent_run_id,
    )
    db.add(run)
    db.flush()
    return run


@pytest.fixture
def _quiet_events():
    """Isolate the state machine from the event/bus persistence layer."""
    published = []

    def _fake_queue(**kw):  # bus → queue_system_event
        published.append(kw.get("event_type"))
        return str(uuid.uuid4())

    with (
        patch("AINDY.agents.agent_message_bus.queue_system_event", side_effect=_fake_queue),
        patch("AINDY.core.execution_signal_helper.record_agent_event", return_value=None),
    ):
        yield published


def test_handshake_disabled_by_default():
    with patch("AINDY.config.settings") as s:
        s.AINDY_DELEGATION_HANDSHAKE = False
        assert coord._delegation_handshake_enabled() is False
        s.AINDY_DELEGATION_HANDSHAKE = True
        assert coord._delegation_handshake_enabled() is True


def test_respond_accept_promotes_child_to_approved(session, _quiet_events):
    parent = _mk_run(session, status="delegated")
    child = _mk_run(session, status="awaiting_delegation", parent_run_id=parent.id)
    session.commit()

    result = coord.respond_to_delegation(session, child_run_id=str(child.id), accept=True)

    assert result["ok"] is True
    session.refresh(child)
    session.refresh(parent)
    assert child.status == "approved"
    assert child.approved_at is not None
    assert parent.status == "delegated"  # parent untouched on accept
    assert any("operation_accept" in str(e) for e in _quiet_events)


def test_respond_reject_fails_child_and_parent(session, _quiet_events):
    parent = _mk_run(session, status="delegated")
    child = _mk_run(session, status="awaiting_delegation", parent_run_id=parent.id)
    session.commit()

    result = coord.respond_to_delegation(
        session, child_run_id=str(child.id), accept=False, reason="not my domain"
    )

    assert result["ok"] is True
    session.refresh(child)
    session.refresh(parent)
    assert child.status == "failed"
    assert child.error_message == "not my domain"
    assert parent.status == "failed"
    assert parent.error_message == "delegation_rejected"
    assert any("operation_reject" in str(e) for e in _quiet_events)


def test_respond_on_non_awaiting_child_is_wrong_status(session, _quiet_events):
    child = _mk_run(session, status="approved")
    session.commit()
    result = coord.respond_to_delegation(session, child_run_id=str(child.id), accept=True)
    assert result["ok"] is False
    assert result["error_code"] == "wrong_status"


def test_respond_on_missing_child_is_not_found(session, _quiet_events):
    result = coord.respond_to_delegation(session, child_run_id=str(uuid.uuid4()), accept=True)
    assert result["ok"] is False
    assert result["error_code"] == "not_found"
