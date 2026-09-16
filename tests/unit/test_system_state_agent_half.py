"""SYSTEM-STATE-TENANT-1 — the system-wide snapshot counted zero agent runs, always.

`compute_current_state` is a SYSTEM-wide reading: `FlowRun`, `SystemEvent` and
`RequestMetric` are read across every tenant directly on the caller's session. Its two
agent inputs went through `sys.v1.agent.count_runs` / `list_recent_durations` with
`user_id=None` — and the dispatcher refuses an empty tenant at step 2b before any handler
runs, on every call, request or not. The service swallowed the error envelope into
`count=0` / `durations=[]`, so `active_runs` and `avg_execution_time` never included an
agent run. Even a supplied tenant would have been the wrong shape: both handlers scope to
ONE user by construction.

Found by the app (its SYSCALL-SILENT-ERRORS-1, 2026-09-16) from `aindy_syscall_outcome_total`.
These tests drive the real function on a seeded session and read the snapshot, so a
regression to the syscall path — or to any per-tenant filter — reads as a wrong number,
not a warning line.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from AINDY.platform_layer import system_state_service as sss

pytestmark = [pytest.mark.runtime_only, pytest.mark.usefixtures("db_session")]

NOW = datetime.now(timezone.utc)


def _agent_run(user_id, status, *, created=NOW, started=None, completed=None):
    from AINDY.db.models import AgentRun

    return AgentRun(
        id=uuid.uuid4(), user_id=user_id, goal="g", status=status, steps_total=1,
        plan={"steps": []}, created_at=created, started_at=started, completed_at=completed,
    )


@pytest.fixture(autouse=True)
def _fresh_cache():
    sss._STATE_CACHE.update({"expires_at": None, "persisted_at": None, "value": None})
    yield
    sss._STATE_CACHE.update({"expires_at": None, "persisted_at": None, "value": None})


def test_active_runs_counts_agent_runs_across_every_tenant(db_session):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    db_session.add_all([
        _agent_run(tenant_a, "executing"),
        _agent_run(tenant_a, "approved"),
        _agent_run(tenant_b, "pending_approval"),
        _agent_run(tenant_b, "completed"),  # terminal — not active
        _agent_run(tenant_b, "failed"),
    ])
    db_session.flush()

    snapshot = sss.compute_current_state(db_session, force_refresh=True, persist_snapshot=False)

    # Two tenants, three active rows: a per-tenant count reads 2 or 1, the refused
    # syscall read 0. Only the system-wide reading is 3.
    assert snapshot["active_runs"] == 3


def test_avg_execution_time_includes_agent_durations(db_session):
    started = NOW - timedelta(minutes=10)
    db_session.add_all([
        _agent_run(uuid.uuid4(), "completed", created=started,
                   started=started, completed=started + timedelta(seconds=4)),
        _agent_run(uuid.uuid4(), "completed", created=started,
                   started=started, completed=started + timedelta(seconds=8)),
    ])
    db_session.flush()

    snapshot = sss.compute_current_state(db_session, force_refresh=True, persist_snapshot=False)

    # No flow runs and no request metrics in the window: the average is over the two
    # agent durations (4000, 8000) plus the request-metric term (0.0) → 4000.0.
    assert snapshot["avg_execution_time"] == pytest.approx(4000.0)


def test_agent_durations_respect_the_one_hour_window(db_session):
    old = NOW - timedelta(hours=3)
    db_session.add(_agent_run(uuid.uuid4(), "completed", created=old,
                              started=old, completed=old + timedelta(seconds=30)))
    db_session.flush()

    snapshot = sss.compute_current_state(db_session, force_refresh=True, persist_snapshot=False)

    assert snapshot["avg_execution_time"] == 0.0


def test_snapshot_never_dispatches_a_syscall(db_session, monkeypatch):
    """Liveness for the mechanism, not the number: the refused path was a dispatch.

    A future rewire back to a tenant-scoped syscall would make the counts above pass on
    an empty table and fail only when rows exist — this pins that the snapshot does not
    dispatch at all, so the class is caught even on a database with nothing in it.
    """
    from AINDY.kernel import syscall_dispatcher

    calls: list[str] = []

    def _spy(name, *a, **kw):
        calls.append(name)
        return {"status": "error", "data": None, "error": "spy"}

    monkeypatch.setattr(syscall_dispatcher, "dispatch_syscall", _spy)
    sss.compute_current_state(db_session, force_refresh=True, persist_snapshot=False)
    assert calls == []
