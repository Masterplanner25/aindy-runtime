"""AGENT-HARDEN-4 (PR2) — mode=simulate on the agent execute path.

Runs a plan shadowed (no tool executes), persists a predicted-effect report under
run.result["simulation"] for the approval inbox, and never changes run status.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import tests.fixtures.db  # noqa: F401  — registers JSONB/UUID SQLite compilers
import AINDY.db.model_registry  # noqa: F401
from AINDY.db.database import Base
from AINDY.runtime import nodus_execution_service as svc
from AINDY.runtime.nodus_execution_service import _extract_simulated_effects, simulate_agent_run
from AINDY.kernel.syscall_registry import (
    SYSCALL_REGISTRY,
    SyscallContext,
    _handle_agent_simulate,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_off(conn, _rec):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.close()

    Base.metadata.tables["agent_runs"].create(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _make_run(db, *, user_id=None, status="pending_approval", plan=None, capability_token=None):
    from AINDY.db.models import AgentRun

    run = AgentRun(
        id=uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        goal="g",
        status=status,
        steps_total=1,
        plan=plan or {"steps": []},
        capability_token=capability_token,
    )
    db.add(run)
    db.commit()
    return run


def _fake_flow_result(effects, output_state):
    return {
        "status": "SUCCESS", "run_id": "fr1", "trace_id": "t",
        "state": {
            "nodus_output_state": output_state,
            "nodus_status": "success",
            "nodus_execute_result": {"simulated_effects": effects},
        },
        "data": {"status": "success", "output_state": output_state},
    }


# --------------------------------------------------------------------------- #
# _extract_simulated_effects
# --------------------------------------------------------------------------- #

def test_extract_from_state_summary():
    fr = _fake_flow_result([{"tool": "send", "executed": False}], {})
    assert _extract_simulated_effects(fr) == [{"tool": "send", "executed": False}]


def test_extract_data_fallback():
    fr = {"data": {"simulated_effects": [{"tool": "x"}]}}
    assert _extract_simulated_effects(fr) == [{"tool": "x"}]


def test_extract_empty_for_non_simulate():
    assert _extract_simulated_effects({"state": {}, "data": {}}) == []
    assert _extract_simulated_effects(None) == []


# --------------------------------------------------------------------------- #
# simulate_agent_run — report + persistence, no status change
# --------------------------------------------------------------------------- #

def test_simulate_persists_report_without_changing_status(session, monkeypatch):
    user_id = uuid.uuid4()
    plan = {"steps": [{"tool": "send_email", "args": {"to": "x"}, "risk_level": "high"}]}
    run = _make_run(session, user_id=user_id, status="pending_approval", plan=plan)

    effects = [{"tool": "send_email", "args": {"to": "x"}, "executed": False, "capability_ok": True}]
    output_state = {"__step_0_result": {"success": True, "result": {"simulated": True}, "error": None}}
    monkeypatch.setattr(
        svc, "run_nodus_script_via_flow",
        lambda **kw: _fake_flow_result(effects, output_state),
    )

    report = simulate_agent_run(
        run_id=str(run.id), plan=plan, user_id=str(user_id), db=session,
        execution_token={"token_hash": "h"},
    )

    assert report["simulated"] is True
    assert report["simulated_effects"] == effects
    assert report["steps_total"] == 1 and report["effects_total"] == 1

    session.refresh(run)
    assert run.status == "pending_approval"  # preview never changes status
    assert run.result["simulation"]["simulated_effects"] == effects


def test_simulate_threads_simulate_flag(session, monkeypatch):
    """The flow call must carry simulate=True in extra_initial_state."""
    user_id = uuid.uuid4()
    plan = {"steps": [{"tool": "a", "args": {}}]}
    run = _make_run(session, user_id=user_id, plan=plan)

    captured = {}

    def _capture(**kw):
        captured.update(kw)
        return _fake_flow_result([], {"__step_0_result": {"success": True, "result": {}, "error": None}})

    monkeypatch.setattr(svc, "run_nodus_script_via_flow", _capture)
    simulate_agent_run(run_id=str(run.id), plan=plan, user_id=str(user_id), db=session)

    assert captured["extra_initial_state"]["simulate"] is True


def test_simulate_invalid_plan_returns_error(session, monkeypatch):
    user_id = uuid.uuid4()
    # A WAIT step with a non-string wait_for makes split_agent_plan raise ValueError.
    plan = {"steps": [{"wait_for": 123}]}
    run = _make_run(session, user_id=user_id, plan=plan)
    monkeypatch.setattr(svc, "run_nodus_script_via_flow", lambda **kw: pytest.fail("must not run"))

    report = simulate_agent_run(run_id=str(run.id), plan=plan, user_id=str(user_id), db=session)
    assert report["simulated"] is True and "error" in report


# --------------------------------------------------------------------------- #
# sys.v1.agent.simulate handler
# --------------------------------------------------------------------------- #

def _ctx(user_id, db):
    return SyscallContext(
        execution_unit_id="eu", user_id=str(user_id),
        capabilities=["agent.simulate"], trace_id="t", metadata={"_db": db},
    )


def test_registered_with_capability():
    entry = SYSCALL_REGISTRY["sys.v1.agent.simulate"]
    assert entry.capability == "agent.simulate" and entry.stable is True


def test_handler_delegates_and_reuses_token(session, monkeypatch):
    user_id = uuid.uuid4()
    run = _make_run(
        session, user_id=user_id, plan={"steps": [{"tool": "a", "args": {}}]},
        capability_token={"token_hash": "h", "granted_tools": ["a"]},
    )
    seen = {}
    monkeypatch.setattr(
        svc, "simulate_agent_run",
        lambda **kw: seen.update(kw) or {"simulated": True, "steps": [], "simulated_effects": []},
    )
    # mint must NOT be called when the run already has a token.
    monkeypatch.setattr(
        "AINDY.agents.capability_service.mint_token",
        lambda **kw: pytest.fail("should reuse existing token"),
    )

    result = _handle_agent_simulate({"run_id": str(run.id)}, _ctx(user_id, session))
    assert result["simulated"] is True
    assert seen["execution_token"] == {"token_hash": "h", "granted_tools": ["a"]}


def test_handler_mints_preview_token_when_absent(session, monkeypatch):
    user_id = uuid.uuid4()
    run = _make_run(session, user_id=user_id, plan={"steps": [{"tool": "a", "args": {}}]}, capability_token=None)
    monkeypatch.setattr(
        "AINDY.agents.capability_service.mint_token",
        lambda **kw: {"token_hash": "preview", "granted_tools": ["a"]},
    )
    seen = {}
    monkeypatch.setattr(
        svc, "simulate_agent_run",
        lambda **kw: seen.update(kw) or {"simulated": True, "steps": [], "simulated_effects": []},
    )
    _handle_agent_simulate({"run_id": str(run.id)}, _ctx(user_id, session))
    assert seen["execution_token"]["token_hash"] == "preview"


def test_handler_cross_tenant_denied(session):
    owner, other = uuid.uuid4(), uuid.uuid4()
    run = _make_run(session, user_id=owner)
    with pytest.raises(ValueError, match="no agent run"):
        _handle_agent_simulate({"run_id": str(run.id)}, _ctx(other, session))


def test_handler_missing_run_id(session):
    with pytest.raises(ValueError, match="requires 'run_id'"):
        _handle_agent_simulate({}, _ctx(uuid.uuid4(), session))
