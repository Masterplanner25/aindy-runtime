"""FR-44 through the route: `POST /apps/agent/runs/{id}/resume` on a process that never saw the park.

The unit tests beside FR-38's (`test_fr38_nodus_vm_authority_gate.py`) drive the service function
with a per-process scheduler stand-in. This one calls the ROUTE, with a real, empty
`SchedulerEngine` as the serving process and the real `publish_event`. It is the topology the app
hit: a run parked by another process, an api whose scheduler holds nothing for it.
Before the fix: 200, `waiters_notified: 0`, the run left `waiting` until a restart.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from AINDY.db.models import AgentRun
from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only

EVENT = "invoice.approved"


@pytest.fixture
def client(runtime_only_app):
    from AINDY.routes.agent_router import router as _agent_router

    runtime_only_app.include_router(_agent_router, prefix="/apps")
    with TestClient(runtime_only_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def serving_engine():
    """This process's scheduler: real matching logic, and it never saw the park."""
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=eng):
        yield eng


def _parked_elsewhere(db, user_id: uuid.UUID) -> AgentRun:
    run = AgentRun(
        user_id=user_id, goal="g", status="waiting", steps_total=1,
        plan={"steps": [{"tool": "noop.tool", "args": {}, "risk_level": "low", "description": "d"}]},
        correlation_id=f"run_{uuid.uuid4()}",
        wait_state={"event_type": EVENT, "resume_segment_index": 0},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_the_route_arms_the_wait_and_wakes_the_run(runtime_only_app, client, mock_db, serving_engine):
    uid = uuid.uuid4()
    runtime_only_app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(uid), "user_id": str(uid), "is_admin": False, "auth_type": "jwt"}
    run = _parked_elsewhere(mock_db, uid)
    assert serving_engine.waiting_for(str(run.id)) is None, "liveness: nothing here holds the wait"

    response = client.post(f"/apps/agent/runs/{run.id}/resume")

    assert response.status_code == 200, response.text
    body = response.json()
    data = body.get("data", body)
    assert data["waiters_notified"] == 1, data
    woken = serving_engine.dequeue_next()
    assert woken is not None and str(woken.run_id) == str(run.id), "the run was not re-enqueued here"
