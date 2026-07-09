"""ECOGAP-1 Phase 2 — crash continuation for nodus_vm agent runs.

`continue_crashed_agent_runs` (startup-only) re-drives a crashed executing nodus_vm
AgentRun from its last completed segment, gated to continuation-safe agent types,
opt-in behind AINDY_DURABLE_CONTINUATION, with a crash-loop attempt bound.
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
from AINDY.core import agent_continuation as ac
from AINDY.db.database import Base

pytestmark = pytest.mark.runtime_only

_AGENT = "ecogap1_p2_agent"
# Two segments: [a,b] then [c]. accumulated of len 2 → segment 0 done → resume at 1.
_SEGMENTS = [{"tool_steps": ["a", "b"], "base_index": 0}, {"tool_steps": ["c"], "base_index": 2}]


def test_count_completed_segments():
    assert ac._count_completed_segments(_SEGMENTS, 0) == 0
    assert ac._count_completed_segments(_SEGMENTS, 2) == 1
    assert ac._count_completed_segments(_SEGMENTS, 3) == 2


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

    for tbl in ("flow_runs", "agent_runs"):
        Base.metadata.tables[tbl].create(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def safe_agent():
    ac.mark_agent_type_continuation_safe(_AGENT)
    try:
        yield _AGENT
    finally:
        ac.CONTINUATION_SAFE_AGENT_TYPES.discard(_AGENT)


@pytest.fixture
def enabled():
    with (
        patch.object(ac, "_continuation_enabled", return_value=True),
        patch.object(ac, "_max_attempts", return_value=3),
        patch("AINDY.runtime.agent_plan_compiler.split_agent_plan", return_value=_SEGMENTS),
        patch(
            "AINDY.runtime.nodus_execution_service._build_agent_resume_callback",
            return_value=lambda: None,
        ) as build_cb,
        patch.object(ac.threading, "Thread") as thread,
    ):
        # make Thread(...).start() a no-op that records
        thread.return_value.start.return_value = None
        yield {"build_cb": build_cb, "thread": thread}


def _mk(session, *, status="executing", agent_type=_AGENT, workflow_type="nodus_agent_execution",
        steps=("r0", "r1"), attempts=None, with_flow=True):
    from AINDY.db.models.agent_run import AgentRun
    from AINDY.db.models.flow_run import FlowRun

    flow_id = None
    if with_flow:
        flow_id = str(uuid.uuid4())
        session.add(FlowRun(id=flow_id, flow_name="nodus_execute", workflow_type=workflow_type,
                            status="executing", state={}))
    result = {"steps": list(steps)}
    if attempts is not None:
        result["__continuation_attempts"] = attempts
    run = AgentRun(
        id=uuid.uuid4(), user_id=uuid.uuid4(), goal="g", status=status,
        agent_type=agent_type, plan={"steps": []}, result=result, flow_run_id=flow_id,
    )
    session.add(run)
    session.commit()
    return run


# ── Eligibility gates ─────────────────────────────────────────────────────────

def test_disabled_is_noop(session, safe_agent):
    _mk(session)
    with patch.object(ac, "_continuation_enabled", return_value=False):
        assert ac.continue_crashed_agent_runs(session) == 0


def test_unsafe_agent_type_skipped(session, enabled):
    _mk(session, agent_type="not_declared_safe")
    assert ac.continue_crashed_agent_runs(session) == 0
    enabled["build_cb"].assert_not_called()


def test_non_nodus_vm_run_skipped(session, safe_agent, enabled):
    # AGENT_FLOW default → workflow_type "agent_execution", not nodus_agent_execution
    _mk(session, workflow_type="agent_execution")
    assert ac.continue_crashed_agent_runs(session) == 0
    enabled["build_cb"].assert_not_called()


def test_no_flow_link_skipped(session, safe_agent, enabled):
    _mk(session, with_flow=False)
    assert ac.continue_crashed_agent_runs(session) == 0


# ── Happy path ────────────────────────────────────────────────────────────────

def test_continues_crashed_nodus_vm_run(session, safe_agent, enabled):
    run = _mk(session, steps=("r0", "r1"))  # 2 steps done → resume at segment 1
    count = ac.continue_crashed_agent_runs(session)

    assert count == 1
    enabled["build_cb"].assert_called_once()
    assert enabled["build_cb"].call_args.kwargs["next_segment_index"] == 1
    assert enabled["build_cb"].call_args.kwargs["claim_status"] == "executing"
    enabled["thread"].return_value.start.assert_called_once()
    session.refresh(run)
    assert run.result["__continuation_attempts"] == 1  # bound recorded
    assert run.result["steps"] == ["r0", "r1"]  # preserved


def test_nothing_left_is_skipped(session, safe_agent, enabled):
    # all 3 steps done → next_idx == len(segments) → nothing to run
    _mk(session, steps=("r0", "r1", "r2"))
    assert ac.continue_crashed_agent_runs(session) == 0
    enabled["build_cb"].assert_not_called()


# ── Crash-loop guard ──────────────────────────────────────────────────────────

def test_exhausted_attempts_dead_letters(session, safe_agent, enabled):
    run = _mk(session, steps=("r0", "r1"), attempts=3)  # == max
    count = ac.continue_crashed_agent_runs(session)

    assert count == 0  # not continued
    enabled["build_cb"].assert_not_called()
    session.refresh(run)
    assert run.status == "failed"
    assert "exhausted" in (run.error_message or "")
