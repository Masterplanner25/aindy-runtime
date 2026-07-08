"""RTR-3 — AgentRun↔FlowRun status canonicalization + stuck-run recovery.

Two halves:

1. The ``condition_codes`` enums now mirror the literals the flow/agent engines
   actually write (``FlowRunStatus.EXECUTING``/``SUCCESS`` and
   ``AgentRunStatus.WAITING`` were written but unnamed), and the classification
   helpers (terminal sets + flow↔agent maps) are the single source of truth for
   "is this run finished?".

2. Stuck-run recovery no longer silently no-ops when the linked AgentRun is in a
   non-terminal state other than ``executing`` (e.g. ``delegated`` / ``waiting``),
   and the stuck-flow scan covers ``executing`` as well as ``running``.
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
from AINDY.db.database import Base
from AINDY.kernel.condition_codes import (
    AGENT_ACTIVE_STATUSES,
    AGENT_TERMINAL_STATUSES,
    FLOW_TERMINAL_STATUSES,
    AgentRunStatus,
    FlowRunStatus,
    agent_status_to_flow,
    flow_status_to_agent,
    is_agent_terminal,
    is_flow_terminal,
)

pytestmark = pytest.mark.runtime_only


# ── Enum / helper contract (no DB) ────────────────────────────────────────────

def test_flowrun_enum_covers_written_literals():
    values = {m.value for m in FlowRunStatus}
    # Every literal the flow engine writes must be a named enum member.
    for written in ("running", "executing", "waiting", "success", "failed"):
        assert written in values, f"{written!r} is written by the runner but unnamed"


def test_agentrun_enum_covers_written_literals():
    values = {m.value for m in AgentRunStatus}
    for written in (
        "pending_approval",
        "approved",
        "executing",
        "waiting",
        "delegated",
        "completed",
        "failed",
        "cancelled",
        "verify_failed",
    ):
        assert written in values, f"{written!r} is written but unnamed"


def test_terminal_classification():
    assert AGENT_TERMINAL_STATUSES == {"completed", "failed", "cancelled", "verify_failed"}
    assert FLOW_TERMINAL_STATUSES == {"success", "failed", "completed"}
    # Non-terminal active states are NOT terminal — this is the no-op-gap guard.
    for active in ("executing", "delegated", "waiting", "approved", "pending_approval"):
        assert not is_agent_terminal(active)
        assert active in AGENT_ACTIVE_STATUSES
    assert is_agent_terminal("completed")
    assert is_agent_terminal(None) is False
    assert is_flow_terminal("success") and is_flow_terminal("failed")
    assert not is_flow_terminal("running") and not is_flow_terminal("executing")


def test_flow_agent_status_maps_are_deterministic():
    assert flow_status_to_agent("success") == "completed"
    assert flow_status_to_agent("failed") == "failed"
    assert flow_status_to_agent("waiting") == "waiting"
    assert flow_status_to_agent("running") == "executing"
    assert flow_status_to_agent("executing") == "executing"
    assert flow_status_to_agent(None) == "failed"  # defensive default

    assert agent_status_to_flow("completed") == "success"
    assert agent_status_to_flow("failed") == "failed"
    assert agent_status_to_flow("delegated") == "executing"
    assert agent_status_to_flow("cancelled") == "failed"
    assert agent_status_to_flow("verify_failed") == "failed"


# ── Stuck-run recovery (DB) ───────────────────────────────────────────────────

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

    for tbl in ("flow_runs", "agent_runs", "agent_steps"):
        Base.metadata.tables[tbl].create(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _make_flow_run(db, *, status="running", stale=True):
    from AINDY.db.models.flow_run import FlowRun

    now = datetime.now(timezone.utc)
    fr = FlowRun(
        id=str(uuid.uuid4()),
        flow_name="agent_execution",
        workflow_type="agent_execution",
        status=status,
        state={},
        updated_at=now - timedelta(hours=2) if stale else now,
    )
    db.add(fr)
    db.flush()
    return fr


def _make_agent_run(db, *, flow_run_id, status):
    from AINDY.db.models.agent_run import AgentRun

    ar = AgentRun(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        goal="do a thing",
        status=status,
        flow_run_id=flow_run_id,
    )
    db.add(ar)
    db.flush()
    return ar


@pytest.mark.parametrize("agent_status", ["executing", "delegated", "waiting"])
def test_recover_agent_run_fails_non_terminal_linked_run(session, agent_status):
    """RTR-3: the historical ``!= 'executing'`` guard stranded delegated/waiting
    runs. Any non-terminal linked AgentRun must now be failed with the FlowRun."""
    from AINDY.agents.stuck_run_service import _recover_agent_run

    fr = _make_flow_run(session, status="running")
    ar = _make_agent_run(session, flow_run_id=str(fr.id), status=agent_status)

    _recover_agent_run(fr, session)
    session.flush()

    session.refresh(fr)
    session.refresh(ar)
    assert fr.status == "failed"
    assert ar.status == "failed"
    assert ar.completed_at is not None


@pytest.mark.parametrize("agent_status", ["completed", "failed", "cancelled", "verify_failed"])
def test_recover_agent_run_leaves_terminal_run_untouched(session, agent_status):
    from AINDY.agents.stuck_run_service import _recover_agent_run

    fr = _make_flow_run(session, status="running")
    ar = _make_agent_run(session, flow_run_id=str(fr.id), status=agent_status)

    _recover_agent_run(fr, session)
    session.flush()

    session.refresh(ar)
    # FlowRun is failed (it was stuck); the terminal AgentRun is NOT clobbered.
    assert fr.status == "failed"
    assert ar.status == agent_status


def test_scan_recovers_executing_flows_not_just_running(session):
    """RTR-3: a stale ``executing`` flow (crash mid-step) is stuck and recoverable."""
    from AINDY.agents.stuck_run_service import scan_and_recover_stuck_runs

    running = _make_flow_run(session, status="running", stale=True)
    executing = _make_flow_run(session, status="executing", stale=True)
    fresh = _make_flow_run(session, status="executing", stale=False)
    _make_agent_run(session, flow_run_id=str(running.id), status="executing")
    _make_agent_run(session, flow_run_id=str(executing.id), status="delegated")
    session.commit()

    recovered = scan_and_recover_stuck_runs(session, staleness_minutes=30)

    assert recovered == 2  # running + executing, not the fresh one
    session.refresh(running)
    session.refresh(executing)
    session.refresh(fresh)
    assert running.status == "failed"
    assert executing.status == "failed"
    assert fresh.status == "executing"
