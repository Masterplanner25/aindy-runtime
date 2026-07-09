"""ECOGAP-1 Phase 1 — transparent crash continuation of non-waiting flows.

`try_continue_flow_run` re-drives a stranded running/executing FlowRun from its
last-committed node (atomic claim → PersistentFlowRunner.resume on a bg thread)
instead of failing it — opt-in (`AINDY_DURABLE_CONTINUATION`), gated to flows
declared continuation-safe, with a durable attempt counter that dead-letters a
crash-looping run.
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
from AINDY.core import flow_continuation as fc
from AINDY.db.database import Base
from AINDY.runtime.flow_engine import registry as flow_registry

pytestmark = pytest.mark.runtime_only

_FLOW = "ecogap1_test_flow"


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

    Base.metadata.tables["flow_runs"].create(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def safe_flow():
    """Register a continuation-safe flow for the duration of a test."""
    flow_registry.FLOW_REGISTRY[_FLOW] = {"nodes": {}, "start": "n0"}
    flow_registry.mark_flow_continuation_safe(_FLOW)
    try:
        yield _FLOW
    finally:
        flow_registry.FLOW_REGISTRY.pop(_FLOW, None)
        flow_registry.CONTINUATION_SAFE_FLOWS.discard(_FLOW)


@pytest.fixture
def enabled():
    with (
        patch.object(fc, "_continuation_enabled", return_value=True),
        patch.object(fc, "_max_attempts", return_value=3),
        patch.object(fc, "_dispatch_resume") as dispatch,
    ):
        yield dispatch


def _mk_flow(session, *, status="running", flow_name=_FLOW, workflow_type=None, attempts=None):
    from AINDY.db.models.flow_run import FlowRun

    state = {"trace_id": "t"}
    if attempts is not None:
        state["__continuation_attempts"] = attempts
    fr = FlowRun(
        id=str(uuid.uuid4()),
        flow_name=flow_name,
        workflow_type=workflow_type,
        status=status,
        state=state,
    )
    session.add(fr)
    session.commit()
    return fr


# ── Registry opt-in ───────────────────────────────────────────────────────────

def test_registry_opt_in_defaults_empty():
    assert flow_registry.is_flow_continuation_safe("never_registered") is False


# ── Eligibility gates ─────────────────────────────────────────────────────────

def test_disabled_is_noop(session, safe_flow):
    fr = _mk_flow(session, status="running")
    with patch.object(fc, "_continuation_enabled", return_value=False):
        assert fc.try_continue_flow_run(fr, session) is False
    session.refresh(fr)
    assert fr.status == "running"  # untouched


def test_not_continuation_safe_is_ineligible(session, enabled):
    from AINDY.db.models.flow_run import FlowRun  # noqa: F401

    fr = _mk_flow(session, status="running", flow_name="some_unsafe_flow")
    # flow not registered / not marked safe
    assert fc.try_continue_flow_run(fr, session) is False
    enabled.assert_not_called()


def test_agent_execution_is_ineligible(session, safe_flow, enabled):
    fr = _mk_flow(session, status="executing", workflow_type="agent_execution")
    assert fc.try_continue_flow_run(fr, session) is False
    enabled.assert_not_called()


# ── Happy path: claim + increment + dispatch ──────────────────────────────────

@pytest.mark.parametrize("status", ["running", "executing"])
def test_continues_stranded_flow(session, safe_flow, enabled, status):
    fr = _mk_flow(session, status=status)
    result = fc.try_continue_flow_run(fr, session)

    assert result is True
    session.refresh(fr)
    assert fr.status == "executing"  # claimed
    assert fr.state["__continuation_attempts"] == 1  # durably incremented
    enabled.assert_called_once()
    assert enabled.call_args.kwargs["run_id"] == str(fr.id)
    assert enabled.call_args.kwargs["flow_name"] == _FLOW


def test_attempt_counter_increments_across_calls(session, safe_flow, enabled):
    fr = _mk_flow(session, status="running", attempts=1)
    fc.try_continue_flow_run(fr, session)
    session.refresh(fr)
    assert fr.state["__continuation_attempts"] == 2


# ── Crash-loop guard: dead-letter after exhausting attempts ────────────────────

def test_exhausted_attempts_dead_letters(session, safe_flow, enabled):
    fr = _mk_flow(session, status="running", attempts=3)  # == max
    result = fc.try_continue_flow_run(fr, session)

    assert result is True  # handled (dead-lettered), caller must NOT fail it
    session.refresh(fr)
    assert fr.status == "failed"
    assert "continuation_exhausted" in (fr.dead_letter_reason or "")
    assert fr.dead_lettered_at is not None
    enabled.assert_not_called()  # no resume dispatched


# ── Scan wiring: startup continues, watchdog fails ────────────────────────────

def _make_stale(session, fr):
    from datetime import timedelta, timezone
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.kernel.clock import utcnow

    stale = utcnow().astimezone(timezone.utc) - timedelta(hours=2)
    session.query(FlowRun).filter(FlowRun.id == fr.id).update(
        {"updated_at": stale}, synchronize_session=False
    )
    session.commit()


def test_startup_scan_continues_safe_flow(session, safe_flow):
    from AINDY.agents.stuck_run_service import scan_and_recover_stuck_runs

    fr = _mk_flow(session, status="running")
    _make_stale(session, fr)

    with (
        patch.object(fc, "_continuation_enabled", return_value=True),
        patch.object(fc, "_dispatch_resume"),
    ):
        stats = scan_and_recover_stuck_runs(
            session, staleness_minutes=30, return_stats=True, continue_stranded=True
        )

    assert stats["continued"] == 1
    assert stats["recovered"] == 0
    session.refresh(fr)
    assert fr.status == "executing"  # continued, NOT failed


def test_watchdog_scan_fails_safe_flow(session, safe_flow):
    """continue_stranded defaults False (periodic watchdog) → safe flow is failed,
    not continued (never double-drive a possibly-live run mid-operation)."""
    from AINDY.agents.stuck_run_service import scan_and_recover_stuck_runs

    fr = _mk_flow(session, status="running")
    _make_stale(session, fr)

    with (
        patch.object(fc, "_continuation_enabled", return_value=True),
        patch.object(fc, "_dispatch_resume"),
    ):
        stats = scan_and_recover_stuck_runs(session, staleness_minutes=30, return_stats=True)

    assert stats.get("continued", 0) == 0
    assert stats["recovered"] == 1
    session.refresh(fr)
    assert fr.status == "failed"
