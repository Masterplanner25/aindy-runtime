"""
AGENT-APPROVE-001b — orphaned approved watchdog.

_recover_orphaned_approved_runs() scans for AgentRun rows stuck in 'approved'
status past the staleness threshold (a process crash killed the background
execute_run thread before it could commit 'executing'), then re-dispatches
execute_run in a fresh background thread.

Four shapes:
  1. Orphaned run is re-dispatched with correct run_id / user_id / db args.
  2. A run approved within the threshold is NOT re-dispatched.
  3. Runs in non-approved terminal / in-progress statuses are ignored.
  4. Multiple orphaned runs all get individual re-dispatch threads.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from AINDY.db.models import AgentRun
from AINDY.platform_layer.scheduler_service import (
    _recover_orphaned_approved_runs,
    ORPHANED_APPROVED_THRESHOLD_MINUTES,
)

pytestmark = pytest.mark.runtime_only


class _SyncThread:
    """threading.Thread stand-in: calls target() synchronously inside start()."""

    def __init__(self, **kwargs):
        self._target = kwargs["target"]

    def start(self):
        self._target()


def _make_approved_run(db, *, user_id: uuid.UUID, approved_ago_minutes: int) -> AgentRun:
    run = AgentRun(
        user_id=user_id,
        goal="watchdog test",
        plan={"steps": []},
        executive_summary="watchdog test",
        overall_risk="low",
        status="approved",
        steps_total=0,
        approved_at=datetime.now(timezone.utc) - timedelta(minutes=approved_ago_minutes),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Shape 1: stale approved run → execute_run re-dispatched with correct args
# ---------------------------------------------------------------------------

def test_orphaned_run_is_redispatched(mock_db):
    uid = uuid.uuid4()
    run = _make_approved_run(mock_db, user_id=uid, approved_ago_minutes=ORPHANED_APPROVED_THRESHOLD_MINUTES + 5)

    execute_calls: list[dict] = []

    def _fake_execute(*, run_id, user_id, db):
        execute_calls.append({"run_id": run_id, "user_id": user_id})

    with (
        patch("AINDY.db.database.SessionLocal", return_value=mock_db),
        patch("AINDY.platform_layer.scheduler_service.threading.Thread", _SyncThread),
        patch("AINDY.agents.agent_runtime.execute_run", side_effect=_fake_execute),
    ):
        _recover_orphaned_approved_runs()

    assert len(execute_calls) == 1
    assert execute_calls[0]["run_id"] == run.id
    assert execute_calls[0]["user_id"] == str(uid)


# ---------------------------------------------------------------------------
# Shape 2: run approved within threshold → no re-dispatch
# ---------------------------------------------------------------------------

def test_recent_approved_run_skipped(mock_db):
    _make_approved_run(mock_db, user_id=uuid.uuid4(), approved_ago_minutes=1)

    with (
        patch("AINDY.db.database.SessionLocal", return_value=mock_db),
        patch("AINDY.platform_layer.scheduler_service.threading.Thread", _SyncThread),
        patch("AINDY.agents.agent_runtime.execute_run") as mock_execute,
    ):
        _recover_orphaned_approved_runs()

    mock_execute.assert_not_called()


# ---------------------------------------------------------------------------
# Shape 3: non-approved statuses are ignored even when old
# ---------------------------------------------------------------------------

def test_non_approved_statuses_ignored(mock_db):
    uid = uuid.uuid4()
    for status in ("executing", "completed", "failed", "pending_approval", "delegated"):
        run = AgentRun(
            user_id=uid,
            goal="watchdog test",
            plan={"steps": []},
            executive_summary="watchdog test",
            overall_risk="low",
            status=status,
            steps_total=0,
            approved_at=datetime.now(timezone.utc) - timedelta(minutes=ORPHANED_APPROVED_THRESHOLD_MINUTES + 5),
        )
        mock_db.add(run)
    mock_db.commit()

    with (
        patch("AINDY.db.database.SessionLocal", return_value=mock_db),
        patch("AINDY.platform_layer.scheduler_service.threading.Thread", _SyncThread),
        patch("AINDY.agents.agent_runtime.execute_run") as mock_execute,
    ):
        _recover_orphaned_approved_runs()

    mock_execute.assert_not_called()


# ---------------------------------------------------------------------------
# Shape 4: multiple orphaned runs → all get individual re-dispatch threads
# ---------------------------------------------------------------------------

def test_multiple_orphaned_runs_all_redispatched(mock_db):
    uid = uuid.uuid4()
    runs = [
        _make_approved_run(mock_db, user_id=uid, approved_ago_minutes=ORPHANED_APPROVED_THRESHOLD_MINUTES + 5 + i)
        for i in range(3)
    ]

    execute_calls: list[dict] = []

    def _fake_execute(*, run_id, user_id, db):
        execute_calls.append({"run_id": run_id, "user_id": user_id})

    with (
        patch("AINDY.db.database.SessionLocal", return_value=mock_db),
        patch("AINDY.platform_layer.scheduler_service.threading.Thread", _SyncThread),
        patch("AINDY.agents.agent_runtime.execute_run", side_effect=_fake_execute),
    ):
        _recover_orphaned_approved_runs()

    assert len(execute_calls) == 3
    recovered_ids = {c["run_id"] for c in execute_calls}
    assert recovered_ids == {r.id for r in runs}
