"""RTR-1 Phase 2e — planner WAIT-step policy + resume (approval) service."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from AINDY.db.database import Base
import AINDY.db.model_registry  # noqa: F401  (populate metadata)
from AINDY.agents.agent_runtime.planning import (
    AGENT_APPROVAL_EVENT,
    apply_wait_policy,
)
from AINDY.agents import runtime_api


# --------------------------------------------------------------------------- #
# apply_wait_policy — backend-aware WAIT reconciliation
# --------------------------------------------------------------------------- #

def test_wait_steps_stripped_on_agent_flow_backend():
    """AGENT_FLOW cannot execute WAIT steps — they must be removed."""
    plan = {"steps": [
        {"tool": "a", "risk_level": "low"},
        {"wait_for": "approval.received"},
        {"tool": "b", "risk_level": "high"},
    ]}
    out = apply_wait_policy(plan, backend="agent_flow")
    assert [s.get("tool") for s in out["steps"]] == ["a", "b"]
    assert all("wait_for" not in s for s in out["steps"])


def test_wait_steps_preserved_on_vm_backend():
    """On nodus_vm, an LLM-emitted WAIT step is kept as-is (flag off → no insertion)."""
    plan = {"steps": [
        {"tool": "a", "risk_level": "low"},
        {"wait_for": "approval.received"},
        {"tool": "b", "risk_level": "high"},
    ]}
    out = apply_wait_policy(plan, backend="nodus_vm")
    assert [s.get("tool") or s.get("wait_for") for s in out["steps"]] == [
        "a", "approval.received", "b",
    ]


def test_policy_inserts_approval_wait_before_first_high_risk(monkeypatch):
    """With the opt-in flag on and nodus_vm, a WAIT is inserted before the first high step."""
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.planning.settings.AINDY_AGENT_WAIT_BEFORE_HIGH_RISK",
        True, raising=False,
    )
    plan = {"steps": [
        {"tool": "search", "risk_level": "low"},
        {"tool": "draft", "risk_level": "medium"},
        {"tool": "send", "risk_level": "high"},
        {"tool": "notify", "risk_level": "high"},
    ]}
    out = apply_wait_policy(plan, backend="nodus_vm")
    kinds = [s.get("tool") or ("WAIT:" + s["wait_for"]) for s in out["steps"]]
    # Exactly one WAIT, immediately before the first high-risk step (`send`).
    assert kinds == ["search", "draft", "WAIT:" + AGENT_APPROVAL_EVENT, "send", "notify"]
    wait_step = out["steps"][2]
    assert "correlation_key" not in wait_step  # executor scopes to the run's correlation


def test_policy_no_high_risk_no_insert(monkeypatch):
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.planning.settings.AINDY_AGENT_WAIT_BEFORE_HIGH_RISK",
        True, raising=False,
    )
    plan = {"steps": [{"tool": "a", "risk_level": "low"}, {"tool": "b", "risk_level": "medium"}]}
    out = apply_wait_policy(plan, backend="nodus_vm")
    assert all("wait_for" not in s for s in out["steps"])


def test_policy_disabled_by_default(monkeypatch):
    """Flag defaults off — no insertion even on nodus_vm."""
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.planning.settings.AINDY_AGENT_WAIT_BEFORE_HIGH_RISK",
        False, raising=False,
    )
    plan = {"steps": [{"tool": "send", "risk_level": "high"}]}
    out = apply_wait_policy(plan, backend="nodus_vm")
    assert out["steps"] == [{"tool": "send", "risk_level": "high"}]


# --------------------------------------------------------------------------- #
# resume_agent_run_runtime — the approval action (publish_event)
# --------------------------------------------------------------------------- #

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


def _waiting_run(session, *, correlation_id="corr-1", correlation_key=None, status="waiting"):
    from AINDY.db.models import AgentRun

    run = AgentRun(
        id=uuid.uuid4(), user_id=uuid.uuid4(), goal="g",
        status=status, correlation_id=correlation_id,
        wait_state={
            "event_type": "agent.approval.granted",
            "correlation_key": correlation_key,
            "resume_segment_index": 1,
        } if status == "waiting" else None,
    )
    session.add(run)
    session.commit()
    return run


def test_resume_publishes_wait_event(session, monkeypatch):
    run = _waiting_run(session, correlation_id="corr-1")
    calls = []
    monkeypatch.setattr(
        "AINDY.kernel.event_bus.publish_event",
        lambda event_type, *, correlation_id=None: calls.append((event_type, correlation_id)) or 1,
    )
    out = runtime_api.resume_agent_run_runtime(db=session, user_id=str(run.user_id), run_id=str(run.id))
    assert out["resumed_event"] == "agent.approval.granted"
    assert out["correlation_id"] == "corr-1"  # falls back to run.correlation_id
    assert out["waiters_notified"] == 1
    assert calls == [("agent.approval.granted", "corr-1")]


def test_resume_uses_wait_state_correlation_key_when_present(session, monkeypatch):
    run = _waiting_run(session, correlation_id="corr-run", correlation_key="ck-explicit")
    calls = []
    monkeypatch.setattr(
        "AINDY.kernel.event_bus.publish_event",
        lambda event_type, *, correlation_id=None: calls.append(correlation_id) or 2,
    )
    out = runtime_api.resume_agent_run_runtime(db=session, user_id=str(run.user_id), run_id=str(run.id))
    assert out["correlation_id"] == "ck-explicit"
    assert calls == ["ck-explicit"]


def test_resume_non_waiting_run_conflicts(session, monkeypatch):
    from fastapi import HTTPException

    run = _waiting_run(session, status="executing")
    monkeypatch.setattr("AINDY.kernel.event_bus.publish_event", lambda *a, **k: pytest.fail("must not publish"))
    with pytest.raises(HTTPException) as ei:
        runtime_api.resume_agent_run_runtime(db=session, user_id=str(run.user_id), run_id=str(run.id))
    assert ei.value.status_code == 409


def test_resume_wrong_owner_not_found(session, monkeypatch):
    from fastapi import HTTPException

    run = _waiting_run(session)
    monkeypatch.setattr("AINDY.kernel.event_bus.publish_event", lambda *a, **k: pytest.fail("must not publish"))
    with pytest.raises(HTTPException) as ei:
        runtime_api.resume_agent_run_runtime(db=session, user_id=str(uuid.uuid4()), run_id=str(run.id))
    assert ei.value.status_code == 404
