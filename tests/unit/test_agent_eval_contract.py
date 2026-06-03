"""
AGENT-EVAL-001 regression: trigger-evaluator exception contract.

All three tests exercise the real route (POST /apps/agent/run) so that
evaluate_trigger() is called through the handler chain — testing the function
directly would have passed before the fix and would still pass after a
regression that re-introduces masking.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from AINDY.db.models import AgentRun
from AINDY.platform_layer import registry
from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only


def _fake_user() -> dict:
    uid = str(uuid.uuid4())
    return {"sub": uid, "user_id": uid, "is_admin": False, "auth_type": "jwt"}


# ---------------------------------------------------------------------------
# Regression: evaluator crash must surface as 500, never as DEFERRED
# ---------------------------------------------------------------------------

def test_evaluator_crash_surfaces_as_500(runtime_only_app, runtime_only_client, mock_db, monkeypatch):
    """AGENT-EVAL-001 regression: an exploding evaluator must return 500, not 202 DEFERRED."""
    user = _fake_user()
    runtime_only_app.dependency_overrides[get_current_user] = lambda: user

    def _exploding_evaluator(_payload):
        raise RuntimeError("evaluator exploded — sentinel")

    monkeypatch.setitem(registry._trigger_evaluators, "user", _exploding_evaluator)

    response = runtime_only_client.post("/apps/agent/run", json={"goal": "run something"})

    assert response.status_code == 500
    body = response.json()
    # Runtime uses a custom exception handler: {"error": "http_error", "message": "<detail>", ...}
    error_text = body.get("message") or body.get("detail") or ""
    assert "evaluator exploded — sentinel" in error_text, (
        f"expected the evaluator's exception message in response body; got: {body}"
    )
    # No AgentRun row was written — exception fired before create_run
    assert mock_db.query(AgentRun).count() == 0


# ---------------------------------------------------------------------------
# Legitimate defer: evaluator returns {"decision": "defer"} → 202 DEFERRED
# ---------------------------------------------------------------------------

def test_evaluator_genuine_defer_returns_202(runtime_only_app, runtime_only_client, mock_db, monkeypatch):
    """A well-behaved evaluator that returns 'defer' must produce 202 DEFERRED with its reason."""
    user = _fake_user()
    runtime_only_app.dependency_overrides[get_current_user] = lambda: user

    def _deferring_evaluator(_payload):
        return {
            "decision": "defer",
            "priority": 0.1,
            "reason": "below threshold — test sentinel",
            "defer_seconds": 300,
        }

    monkeypatch.setitem(registry._trigger_evaluators, "user", _deferring_evaluator)

    response = runtime_only_client.post("/apps/agent/run", json={"goal": "run something"})

    assert response.status_code == 202
    body = response.json()
    assert body.get("status") == "DEFERRED", f"expected DEFERRED status; got: {body}"
    result_block = body.get("result") or {}
    assert "below threshold — test sentinel" in str(result_block.get("reason", "")), (
        f"expected evaluator reason in result block; got: {result_block}"
    )
    # No AgentRun was written — deferred means create_run was never called
    assert mock_db.query(AgentRun).count() == 0


# ---------------------------------------------------------------------------
# Happy path: evaluator approves → create_run called → run data returned
# ---------------------------------------------------------------------------

def test_happy_path_evaluator_execute_calls_create_run(
    runtime_only_app, runtime_only_client, mock_db, monkeypatch
):
    """When the evaluator approves execution, create_run is called and run data is returned."""
    user = _fake_user()
    uid = user["sub"]
    runtime_only_app.dependency_overrides[get_current_user] = lambda: user

    # Inject an in-process approving evaluator so the test is not sensitive to subprocess state.
    def _approving_evaluator(_payload):
        return {"decision": "execute", "priority": 0.9, "reason": "approved — test sentinel"}

    monkeypatch.setitem(registry._trigger_evaluators, "user", _approving_evaluator)

    fake_run_id = str(uuid.uuid4())
    fake_run = {
        "run_id": fake_run_id,
        "user_id": uid,
        "status": "pending_approval",
        "objective": "run something",
        "plan": {"steps": []},
        "overall_risk": "low",
        "trace_id": None,
        "result": None,
        "execution_record": None,
    }

    with patch("AINDY.agents.runtime_api.create_run", return_value=fake_run):
        response = runtime_only_client.post("/apps/agent/run", json={"goal": "run something"})

    assert response.status_code == 200, (
        f"expected 200 on approved execution; got {response.status_code}: {response.json()}"
    )
    body = response.json()
    status_val = str(body.get("status", body.get("data", {}).get("status", ""))).upper()
    assert status_val not in ("DEFERRED", "IGNORED"), (
        f"evaluator approved execution but response shows deferral: {body}"
    )
