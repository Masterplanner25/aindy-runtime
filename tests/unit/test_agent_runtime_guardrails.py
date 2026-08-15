from __future__ import annotations

import uuid

import pytest

from AINDY.agents.agent_runtime.execution import execute_run
from AINDY.agents.runtime_guardrails import (
    AgentRuntimeGuardrailViolation,
    build_autonomous_submission_key,
    enforce_delegation_guardrails,
    enforce_replay_guardrails,
    enforce_run_creation_guardrails,
)
from AINDY.db.models import AgentRun
from AINDY.db.models.job_log import JobLog
from AINDY.platform_layer import async_job_service

pytestmark = pytest.mark.runtime_only


def _make_run(
    db_session,
    *,
    user_id: uuid.UUID | None = None,
    goal: str = "test objective",
    status: str = "approved",
    trace_id: str | None = "trace-1",
    parent_run_id=None,
    replayed_from_run_id: str | None = None,
    spawned_by_agent_id=None,
    capability_token: dict | None = None,
):
    run = AgentRun(
        user_id=user_id or uuid.uuid4(),
        goal=goal,
        plan={"steps": []},
        executive_summary=goal,
        overall_risk="low",
        status=status,
        steps_total=0,
        correlation_id=f"run_{uuid.uuid4()}",
        trace_id=trace_id,
        parent_run_id=parent_run_id,
        replayed_from_run_id=replayed_from_run_id,
        spawned_by_agent_id=spawned_by_agent_id,
        capability_token=capability_token,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def test_run_creation_guardrails_block_duplicate_objective_in_same_trace(db_session, monkeypatch):
    monkeypatch.setenv("AINDY_AGENT_MAX_SAME_OBJECTIVE_PER_TRACE", "1")
    user_id = uuid.uuid4()
    _make_run(db_session, user_id=user_id, goal="Write report", trace_id="trace-a")

    with pytest.raises(AgentRuntimeGuardrailViolation, match="same objective is already active"):
        enforce_run_creation_guardrails(
            db_session,
            user_id=str(user_id),
            objective=" write   report ",
            trace_id="trace-a",
        )


def test_delegation_guardrails_allow_first_child_within_limits(db_session, monkeypatch):
    monkeypatch.setenv("AINDY_AGENT_MAX_DELEGATION_DEPTH", "3")
    monkeypatch.setenv("AINDY_AGENT_MAX_CHILD_RUNS_PER_PARENT", "8")
    parent = _make_run(db_session, trace_id="trace-d")

    result = enforce_delegation_guardrails(
        db_session,
        parent_run=parent,
        selected_agent_id=str(uuid.uuid4()),
        trace_id="trace-d",
    )

    assert result["delegation_depth"] == 0
    assert result["child_runs_existing"] == 0


def test_delegation_guardrails_block_depth_limit(db_session, monkeypatch):
    monkeypatch.setenv("AINDY_AGENT_MAX_DELEGATION_DEPTH", "2")
    root = _make_run(db_session, trace_id="trace-depth")
    child = _make_run(db_session, parent_run_id=root.id, trace_id="trace-depth", spawned_by_agent_id=uuid.uuid4())
    grandchild = _make_run(
        db_session,
        parent_run_id=child.id,
        trace_id="trace-depth",
        spawned_by_agent_id=uuid.uuid4(),
    )

    with pytest.raises(AgentRuntimeGuardrailViolation, match="delegation depth 2"):
        enforce_delegation_guardrails(
            db_session,
            parent_run=grandchild,
            selected_agent_id=str(uuid.uuid4()),
            trace_id="trace-depth",
        )


def test_delegation_guardrails_block_duplicate_active_child(db_session, monkeypatch):
    monkeypatch.setenv("AINDY_AGENT_MAX_CHILD_RUNS_PER_PARENT", "8")
    parent = _make_run(db_session, trace_id="trace-child")
    selected_agent_id = uuid.uuid4()
    _make_run(
        db_session,
        parent_run_id=parent.id,
        trace_id="trace-child",
        spawned_by_agent_id=selected_agent_id,
    )

    with pytest.raises(AgentRuntimeGuardrailViolation, match="active child run already exists"):
        enforce_delegation_guardrails(
            db_session,
            parent_run=parent,
            selected_agent_id=str(selected_agent_id),
            trace_id="trace-child",
        )


def test_replay_guardrails_block_replay_chain_depth(db_session, monkeypatch):
    monkeypatch.setenv("AINDY_AGENT_MAX_REPLAY_DEPTH", "2")
    root = _make_run(db_session, trace_id="trace-replay")
    replay1 = _make_run(db_session, trace_id="trace-replay", replayed_from_run_id=str(root.id))
    replay2 = _make_run(db_session, trace_id="trace-replay", replayed_from_run_id=str(replay1.id))

    with pytest.raises(AgentRuntimeGuardrailViolation, match="Replay blocked: replay lineage depth 2"):
        enforce_replay_guardrails(db_session, original_run=replay2)


def test_submit_autonomous_async_job_suppresses_duplicate_submission(db_session, monkeypatch):
    user_id = uuid.uuid4()
    payload = {"goal": "same goal", "trace_id": "trace-dup"}
    trigger_context = {"goal": "same goal", "trace_id": "trace-dup", "importance": 0.95}
    submission_key = build_autonomous_submission_key(
        task_name="agent.create_run",
        payload=payload,
        user_id=str(user_id),
        source="agent_router",
        trigger_context=trigger_context,
    )
    existing = JobLog(
        id=str(uuid.uuid4()),
        source="agent_router",
        task_name="agent.create_run",
        payload={"__runtime_submission_key": submission_key},
        status="pending",
        user_id=user_id,
        trace_id="trace-dup",
    )
    db_session.add(existing)
    db_session.commit()
    monkeypatch.setattr(async_job_service, "evaluate_live_trigger", lambda **kwargs: {"decision": "execute", "priority": 1.0, "reason": "ok"})
    monkeypatch.setattr(async_job_service, "record_decision", lambda **kwargs: None)

    response = async_job_service.submit_autonomous_async_job(
        task_name="agent.create_run",
        payload=payload,
        user_id=user_id,
        source="agent_router",
        trigger_type="user",
        trigger_context=trigger_context,
        db=db_session,
    )

    assert response["status"] == "IGNORED"
    assert response["result"]["reason"] == "Duplicate autonomous submission suppressed by runtime guardrails."


def test_submit_autonomous_async_job_allows_distinct_submission(db_session, monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(async_job_service, "evaluate_live_trigger", lambda **kwargs: {"decision": "execute", "priority": 1.0, "reason": "ok"})
    monkeypatch.setattr(async_job_service, "record_decision", lambda **kwargs: None)
    monkeypatch.setattr(async_job_service, "submit_async_job", lambda **kwargs: "job-123")

    response = async_job_service.submit_autonomous_async_job(
        task_name="agent.create_run",
        payload={"goal": "fresh goal", "trace_id": "trace-fresh"},
        user_id=user_id,
        source="agent_router",
        trigger_type="user",
        trigger_context={"goal": "fresh goal", "trace_id": "trace-fresh", "importance": 0.95},
        db=db_session,
    )

    assert response["status"] == "QUEUED"
    assert response["result"]["job_log_id"] == "job-123"


def test_execute_run_fails_clearly_when_delegation_guardrail_blocks(db_session, monkeypatch):
    monkeypatch.setenv("AINDY_AGENT_MAX_DELEGATION_DEPTH", "1")
    root = _make_run(db_session, trace_id="trace-exec", capability_token={"allowed_capabilities": []})
    child = _make_run(
        db_session,
        parent_run_id=root.id,
        trace_id="trace-exec",
        capability_token={"allowed_capabilities": []},
    )
    selected_agent_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.execution.register_or_update_agent",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.execution.decide_execution_mode",
        lambda *args, **kwargs: {
            "mode": "delegate",
            "selected_agent": {"agent_id": selected_agent_id},
            "candidates": [],
        },
    )
    monkeypatch.setattr(
        "AINDY.agents.agent_runtime.execution.record_agent_event",
        lambda *args, **kwargs: "event-1",
    )

    result = execute_run(run_id=str(child.id), user_id=str(child.user_id), db=db_session)

    assert result is not None
    assert result["status"] == "failed"
    assert "delegation depth" in (result.get("error_message") or "").lower()
    assert result["result"]["guardrail_code"] == "delegation_depth_exceeded"
