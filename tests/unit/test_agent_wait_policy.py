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

pytestmark = pytest.mark.runtime_only


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


# --------------------------------------------------------------------------- #
# Real-PG parity fixes — regression guards for bugs the mocked path hid
# --------------------------------------------------------------------------- #

def test_grantable_tools_skips_wait_steps():
    """A WAIT step (no tool) must not make a plan un-grantable — else no
    wait-containing plan could be approved (mint_token would return None)."""
    from AINDY.agents.capability_service import get_grantable_tools
    from AINDY.platform_layer import runtime_agent_defaults

    runtime_agent_defaults.register()  # registers memory.recall / memory.write
    plan = {"steps": [
        {"tool": "memory.recall", "risk_level": "low"},
        {"wait_for": "approval.received"},
        {"tool": "memory.write", "risk_level": "low"},
    ]}
    granted = get_grantable_tools(plan, user_id="u", db=None, approval_mode="manual")
    assert granted == ["memory.recall", "memory.write"]  # wait step skipped, not fatal


def test_grantable_tools_rejects_unknown_named_tool():
    from AINDY.agents.capability_service import get_grantable_tools

    plan = {"steps": [{"tool": "does.not.exist", "risk_level": "low"}]}
    assert get_grantable_tools(plan, user_id="u", db=None, approval_mode="manual") == []


def test_nodus_agent_workflow_type_passes_engine_boundary():
    """The nodus_vm agent path must use a 'nodus'-labelled workflow_type, or
    run_nodus_script_via_flow's engine-boundary guard rejects it as a Python DAG."""
    from AINDY.runtime import enforce_engine_boundary

    # The label the nodus_vm path now uses — must be accepted for nodus.run.
    enforce_engine_boundary(entrypoint="nodus.run", workflow_type="nodus_agent_execution")
    # The old label is still (correctly) rejected — the guard is intact.
    with pytest.raises(ValueError):
        enforce_engine_boundary(entrypoint="nodus.run", workflow_type="agent_execution")


# --------------------------------------------------------------------------- #
# runtime.selftest — the diagnostic tool that drives real-PG retry/halt tests
# --------------------------------------------------------------------------- #

def test_runtime_selftest_success_echoes():
    from AINDY.platform_layer.runtime_agent_defaults import runtime_selftest

    out = runtime_selftest({"outcome": "success", "x": 1}, "u", None)
    assert out["ok"] is True
    assert out["echo"]["x"] == 1


def test_runtime_selftest_failure_raises_with_attempt_count():
    from AINDY.platform_layer.runtime_agent_defaults import runtime_selftest

    key = uuid.uuid4().hex
    with pytest.raises(RuntimeError, match=r"boom \(attempt 1\)"):
        runtime_selftest({"outcome": "fail", "error": "boom", "attempt_key": key}, "u", None)
    with pytest.raises(RuntimeError, match=r"boom \(attempt 2\)"):
        runtime_selftest({"outcome": "fail", "error": "boom", "attempt_key": key}, "u", None)


def test_runtime_selftest_registered_but_excluded_from_planner_catalog():
    from AINDY.agents.tool_registry import TOOL_REGISTRY
    from AINDY.platform_layer import runtime_agent_defaults as rad

    rad.register()
    assert "runtime.selftest" in TOOL_REGISTRY  # executable + capability-wired
    catalog = [t["name"] for t in rad.get_tools_for_run({})]
    assert "runtime.selftest" not in catalog  # but not offered to the planner
