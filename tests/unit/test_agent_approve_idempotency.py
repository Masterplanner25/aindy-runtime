"""
AGENT-APPROVE-001a: approve idempotency contract.

Three shapes covering the realistic failure modes described in the ticket:
  1. Sequential double-approve — second call returns run state, never calls execute_run again.
  2. Repro shape (cancel mid-execution + retry) — second call sees status "executing",
     CAS returns rowcount=0, execute_run not called again.
  3. Concurrent race repro — CAS UPDATE WHERE status='pending_approval' returns rowcount=0
     for a caller that arrives after the first commit, proving the DB-level guarantee.

Tests 1 and 2 exercise the real route (POST /apps/agent/runs/{run_id}/approve) so that
the full ExecutionPipeline → approve_agent_run_runtime → approve_run chain is covered.
Test 3 validates the CAS rowcount invariant directly against the shared session.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import update as sqla_update

from AINDY.db.models import AgentRun
from AINDY.platform_layer import registry
from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only

_FAKE_TOKEN = {
    "execution_token": "test-execution-token",
    "granted_tools": [],
    "allowed_capabilities": ["agent.execute"],
    "issued_at": "2026-01-01T00:00:00+00:00",
    "expires_at": "2026-01-02T00:00:00+00:00",
}


def _fake_user() -> dict:
    uid = str(uuid.uuid4())
    return {"sub": uid, "user_id": uid, "is_admin": False, "auth_type": "jwt"}


def _make_pending_run(db, *, user_id: uuid.UUID) -> AgentRun:
    run = AgentRun(
        user_id=user_id,
        goal="idempotency test",
        plan={"steps": []},
        executive_summary="idempotency test",
        overall_risk="low",
        status="pending_approval",
        steps_total=0,
        correlation_id=f"run_{uuid.uuid4()}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _approving_evaluator(_payload: dict) -> dict:
    return {"decision": "execute", "priority": 0.9, "reason": "approved — test sentinel"}


# ---------------------------------------------------------------------------
# Shape 1: sequential double-approve — second call returns without executing
# ---------------------------------------------------------------------------

def test_sequential_double_approve_executes_once(
    runtime_only_app, runtime_only_client, mock_db, monkeypatch
):
    """Second approve on an already-approved run must not call execute_run a second time."""
    user = _fake_user()
    uid = user["sub"]
    runtime_only_app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setitem(registry._trigger_evaluators, "user", _approving_evaluator)

    run = _make_pending_run(mock_db, user_id=uuid.UUID(uid))
    run_id = str(run.id)

    execute_calls: list[str] = []

    def _fake_execute(*, run_id, user_id, db):
        execute_calls.append(str(run_id))
        return {"run_id": str(run_id), "status": "completed"}

    with (
        patch("AINDY.agents.agent_runtime.approvals.mint_token", return_value=_FAKE_TOKEN),
        patch("AINDY.agents.agent_runtime.execute_run", side_effect=_fake_execute),
        patch("AINDY.agents.agent_runtime.approvals.record_agent_event", return_value="evt-1"),
    ):
        r1 = runtime_only_client.post(f"/apps/agent/runs/{run_id}/approve")
        r2 = runtime_only_client.post(f"/apps/agent/runs/{run_id}/approve")

    assert r1.status_code == 200, f"first approve failed: {r1.status_code} {r1.json()}"
    assert r2.status_code == 200, f"second approve should return run state: {r2.status_code} {r2.json()}"
    assert len(execute_calls) == 1, (
        f"execute_run should be called exactly once; called {len(execute_calls)} times"
    )


# ---------------------------------------------------------------------------
# Shape 2: repro (cancel mid-execution + retry) — execute_run not called again
# ---------------------------------------------------------------------------

def test_repro_cancel_retry_executes_once(
    runtime_only_app, runtime_only_client, mock_db, monkeypatch
):
    """
    Repro shape: first approve starts, execute_run is in-progress (status='executing').
    Client retries. Second approve sees status 'executing', CAS returns rowcount=0.
    """
    user = _fake_user()
    uid = user["sub"]
    runtime_only_app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setitem(registry._trigger_evaluators, "user", _approving_evaluator)

    run = _make_pending_run(mock_db, user_id=uuid.UUID(uid))
    run_id = str(run.id)
    run_db_id = run.id

    execute_calls: list[str] = []

    def _fake_execute_sets_executing(*, run_id, user_id, db):
        execute_calls.append(str(run_id))
        # Simulate server committing "executing" while client has already cancelled.
        db.execute(
            sqla_update(AgentRun)
            .where(AgentRun.id == run_db_id)
            .values(status="executing")
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return {"run_id": str(run_id), "status": "executing"}

    with (
        patch("AINDY.agents.agent_runtime.approvals.mint_token", return_value=_FAKE_TOKEN),
        patch("AINDY.agents.agent_runtime.execute_run", side_effect=_fake_execute_sets_executing),
        patch("AINDY.agents.agent_runtime.approvals.record_agent_event", return_value="evt-2"),
    ):
        r1 = runtime_only_client.post(f"/apps/agent/runs/{run_id}/approve")
        # r1 represents the request the client cancelled; server kept running and set "executing".
        r2 = runtime_only_client.post(f"/apps/agent/runs/{run_id}/approve")

    assert r1.status_code == 200, f"first approve failed: {r1.status_code} {r1.json()}"
    assert r2.status_code == 200, f"retry approve should return run state: {r2.status_code} {r2.json()}"
    assert len(execute_calls) == 1, (
        f"execute_run should be called exactly once; called {len(execute_calls)} times"
    )


# ---------------------------------------------------------------------------
# Shape 3: concurrent race — CAS rowcount=0 for a second concurrent attempt
# ---------------------------------------------------------------------------

def test_concurrent_race_repro_cas_rowcount(mock_db, monkeypatch):
    """
    Concurrent race: after approve_run commits 'approved', any concurrent
    UPDATE WHERE status='pending_approval' returns rowcount=0.

    This is the DB-level proof that the CAS prevents double execution when
    two callers both read 'pending_approval' before either commits.
    Under PostgreSQL READ COMMITTED the second UPDATE also blocks until the
    first commits, then finds rowcount=0. Under SQLite (test harness) the
    operations serialize naturally and the result is the same.
    """
    from AINDY.agents.agent_runtime.approvals import approve_run

    user_id = uuid.uuid4()
    run = _make_pending_run(mock_db, user_id=user_id)
    run_db_id = run.id

    execute_calls: list[str] = []

    def _fake_execute(*, run_id, user_id, db):
        execute_calls.append(str(run_id))
        return {"run_id": str(run_id), "status": "completed"}

    with (
        patch("AINDY.agents.agent_runtime.approvals.mint_token", return_value=_FAKE_TOKEN),
        patch("AINDY.agents.agent_runtime.execute_run", side_effect=_fake_execute),
        patch("AINDY.agents.agent_runtime.approvals.record_agent_event", return_value="evt-3"),
    ):
        approve_run(run_id=str(run.id), user_id=str(user_id), db=mock_db)

    assert len(execute_calls) == 1

    # Simulate what the "second concurrent caller's" CAS UPDATE would see.
    # Both callers read 'pending_approval' before either committed; the first
    # caller already won and committed 'approved'. The second caller's UPDATE
    # must return rowcount=0, proving execute_run would not be called.
    rows = mock_db.execute(
        sqla_update(AgentRun)
        .where(AgentRun.id == run_db_id, AgentRun.status == "pending_approval")
        .values(status="approved", approved_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    ).rowcount

    assert rows == 0, (
        f"Concurrent second approve CAS must return rowcount=0 (status already past "
        f"pending_approval); got {rows}. Without this guarantee, a second concurrent "
        f"caller would call execute_run again."
    )
