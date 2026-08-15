"""INFINITY-RUNTIME-1 item 3 — aggregate observability + execution support metrics.

Exercises build_support_metrics against a real session (tenant scoping, window
filtering, Infinity-event counts) and the clamp helper. Uses the shared db_session
fixture so the PG-typed tables render via the suite's SQLite compat shim.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from AINDY.core.system_event_types import SystemEventTypes
from AINDY.platform_layer.support_metrics_service import _clamp_window, build_support_metrics

pytestmark = [pytest.mark.runtime_only, pytest.mark.usefixtures("db_session")]

NOW = datetime.now(timezone.utc)


def _agent_run(user_id, status, when=NOW):
    from AINDY.db.models import AgentRun

    return AgentRun(
        id=uuid.uuid4(), user_id=user_id, goal="g", status=status,
        steps_total=1, plan={"steps": []}, created_at=when,
    )


def _job(user_id, status, when=NOW):
    from AINDY.db.models.job_log import JobLog

    return JobLog(id=str(uuid.uuid4()), source="test", status=status, user_id=user_id, created_at=when)


def _event(user_id, etype, when=NOW):
    from AINDY.db.models.system_event import SystemEvent

    return SystemEvent(id=uuid.uuid4(), type=etype, user_id=user_id, timestamp=when)


def _request(user_id, status_code, duration_ms, when=None):
    from AINDY.db.models.request_metric import RequestMetric

    return RequestMetric(
        user_id=user_id, method="GET", path="/x", status_code=status_code,
        duration_ms=duration_ms, created_at=(when or NOW).replace(tzinfo=None),
    )


@pytest.fixture
def seeded(db_session):
    from AINDY.db.models.system_health_log import SystemHealthLog

    me = uuid.uuid4()
    other = uuid.uuid4()
    old = NOW - timedelta(hours=48)  # outside a 24h window

    db_session.add_all([
        # agent runs — 2 mine (completed, failed), 1 other
        _agent_run(me, "completed"), _agent_run(me, "failed"), _agent_run(other, "completed"),
        # async jobs — 2 mine, 1 other
        _job(me, "success"), _job(me, "failed"), _job(other, "success"),
        # infinity events — score x2, recall x1, next_action x1 mine; 1 other; 1 unrelated mine
        _event(me, SystemEventTypes.SCORE_COMPUTED), _event(me, SystemEventTypes.SCORE_COMPUTED),
        _event(me, SystemEventTypes.RECALL_USED), _event(me, SystemEventTypes.NEXT_ACTION_CHOSEN),
        _event(other, SystemEventTypes.SCORE_COMPUTED),
        _event(me, SystemEventTypes.EXECUTION_COMPLETED),  # not an infinity-loop event
        # requests — 2 mine (200, 500), 1 other
        _request(me, 200, 100.0), _request(me, 500, 300.0), _request(other, 200, 50.0),
        # an old score event outside the window
        _event(me, SystemEventTypes.SCORE_COMPUTED, when=old),
        SystemHealthLog(status="healthy", timestamp=NOW.replace(tzinfo=None)),
    ])
    db_session.flush()
    return me, other


def test_clamp_window():
    assert _clamp_window(None) == 24  # unset -> default
    assert _clamp_window("bad") == 24
    assert _clamp_window(0) == 24  # 0 is falsy -> default, not the floor
    assert _clamp_window(-5) == 1  # negative clamps up to the floor
    assert _clamp_window(500) == 168  # above max clamps down
    assert _clamp_window(48) == 48


def test_rollup_shape_and_tenant_scoping(db_session, seeded):
    me, _other = seeded
    out = build_support_metrics(db_session, user_id=me, window_hours=24)

    assert set(out) == {"generated_at", "window_hours", "observability", "execution", "infinity_events"}
    assert out["window_hours"] == 24

    agent = out["execution"]["agent_runs"]
    assert agent["total"] == 2  # excludes other tenant
    assert agent["by_status"] == {"completed": 1, "failed": 1}

    jobs = out["execution"]["async_jobs"]
    assert jobs["total"] == 2
    assert jobs["by_status"] == {"success": 1, "failed": 1}

    ev = out["infinity_events"]
    assert ev["score_computed"] == 2  # old one is outside window; other tenant excluded
    assert ev["recall_used"] == 1
    assert ev["next_action_chosen"] == 1
    assert ev["total"] == 4  # execution.completed is NOT counted

    req = out["observability"]["requests"]
    assert req["total"] == 2
    assert req["errors"] == 1
    assert req["error_rate_pct"] == 50.0
    assert req["avg_latency_ms"] == 200.0

    assert out["observability"]["platform_health_status"] == "healthy"


def test_empty_tenant_returns_zeros(db_session, seeded):
    stranger = uuid.uuid4()
    out = build_support_metrics(db_session, user_id=stranger, window_hours=24)
    assert out["execution"]["agent_runs"] == {"total": 0, "by_status": {}}
    assert out["execution"]["async_jobs"] == {"total": 0, "by_status": {}}
    assert out["infinity_events"] == {"recall_used": 0, "score_computed": 0, "next_action_chosen": 0, "total": 0}
    assert out["observability"]["requests"]["total"] == 0
    assert out["observability"]["requests"]["error_rate_pct"] == 0.0


def test_window_excludes_old_rows(db_session, seeded):
    me, _ = seeded
    # A 1h window still includes the fresh rows (created at NOW) but never the 48h-old event.
    out = build_support_metrics(db_session, user_id=me, window_hours=1)
    assert out["infinity_events"]["score_computed"] == 2
