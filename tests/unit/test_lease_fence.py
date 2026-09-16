"""`LEASE-FENCE-1` — the background lease carries a fence; a stale leader is REFUSED, not asked.

Design: ``docs/design/LEASE_FENCE_DESIGN.md`` §3 (the mechanism), §8 (these tests).

★ The negative control comes first (green-check variant 9): a fence that has never been seen to
refuse anything is a check that is green because there was nothing to catch. The Postgres-only
half — that ``FOR SHARE`` BLOCKS a concurrent takeover until the job commits — lives in
``tests/integration/test_lease_fence_contention.py``; on SQLite the lock clause is a no-op and
what these tests pin is the comparison and the wiring.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from AINDY.db.models.background_task_lease import BackgroundTaskLease
from AINDY.platform_layer import leadership
from AINDY.platform_layer.leadership import (
    LeaseFenceLost,
    LeaseHold,
    assert_lease_fence,
    claim_lease,
    try_acquire_lease,
)

pytestmark = pytest.mark.runtime_only

NAME = "fence-test"


def _session(factory):
    return factory()


def _expire(db, *, name=NAME):
    row = db.query(BackgroundTaskLease).filter(BackgroundTaskLease.name == name).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()


def _row_fence(db, *, name=NAME) -> int:
    return int(db.query(BackgroundTaskLease).filter(BackgroundTaskLease.name == name).one().fence)


# ---------------------------------------------------------------------------
# The fence moves ONLY on takeover
# ---------------------------------------------------------------------------

def test_first_claim_reads_fence_1_renew_keeps_it_takeover_increments(testing_session_factory):
    with _session(testing_session_factory) as s:
        hold = claim_lease(s, "A", name=NAME)
        assert isinstance(hold, LeaseHold) and hold.fence == 1
        assert claim_lease(s, "A", name=NAME).fence == 1          # renew: unchanged
        assert claim_lease(s, "A", name=NAME).fence == 1
        assert claim_lease(s, "B", name=NAME) is None              # live lease: not acquired
        _expire(s)
        took = claim_lease(s, "B", name=NAME)
        assert took is not None and took.fence == 2                # takeover: +1
        assert claim_lease(s, "B", name=NAME).fence == 2           # B's renew: unchanged
        _expire(s)
        assert claim_lease(s, "A", name=NAME).fence == 3           # takeover back: +1
        assert _row_fence(s) == 3
        leadership.release_lease(s, "A", name=NAME)


def test_boolean_form_is_unchanged(testing_session_factory):
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "A", name=NAME) is True
        assert try_acquire_lease(s, "B", name=NAME) is False
        leadership.release_lease(s, "A", name=NAME)


def test_a_row_predating_the_column_takes_over_to_1(testing_session_factory):
    """Alembic 0019's DEFAULT 0 / a NULL read as 0 — the next takeover makes it 1."""
    with _session(testing_session_factory) as s:
        claim_lease(s, "A", name=NAME)
        row = s.query(BackgroundTaskLease).filter(BackgroundTaskLease.name == NAME).one()
        row.fence = 0
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()
        assert claim_lease(s, "B", name=NAME).fence == 1
        leadership.release_lease(s, "B", name=NAME)


# ---------------------------------------------------------------------------
# ★ The negative control — the fence REFUSES after a takeover
# ---------------------------------------------------------------------------

def test_fence_refuses_a_job_whose_leadership_was_taken_over_mid_job(testing_session_factory):
    from AINDY.platform_layer.metrics import REGISTRY

    with _session(testing_session_factory) as s:
        hold = claim_lease(s, "A", name=NAME)
        assert_lease_fence(s, hold.fence, name=NAME, job="t")       # still leader: passes
        _expire(s)
        assert claim_lease(s, "B", name=NAME).fence == 2            # takeover happened
        before = REGISTRY.get_sample_value("aindy_lease_fence_refusals_total", {"job": "t"}) or 0.0
        with pytest.raises(LeaseFenceLost):
            assert_lease_fence(s, hold.fence, name=NAME, job="t")   # A's write is refused
        assert REGISTRY.get_sample_value("aindy_lease_fence_refusals_total", {"job": "t"}) == before + 1
        assert_lease_fence(s, 2, name=NAME, job="t")                # B's own check passes
        leadership.release_lease(s, "B", name=NAME)


def test_fence_refuses_when_the_row_is_gone(testing_session_factory):
    with _session(testing_session_factory) as s:
        hold = claim_lease(s, "A", name=NAME)
        leadership.release_lease(s, "A", name=NAME)
        with pytest.raises(LeaseFenceLost):
            assert_lease_fence(s, hold.fence, name=NAME, job="t")


def test_no_fence_means_no_check(testing_session_factory):
    """The in-process profile holds no lease: None skips the check, never refuses."""
    with _session(testing_session_factory) as s:
        assert_lease_fence(s, None, name=NAME, job="t")  # no row, no raise


def test_background_leader_fence_reads_the_elector_hold(monkeypatch):
    leadership.reset_background_elector()
    assert leadership.background_leader_fence() is None
    elector = leadership.get_background_elector(db_factory=lambda: None, owner_id="X", enabled=True)
    monkeypatch.setattr(
        leadership, "claim_lease",
        lambda db, owner_id, **kw: LeaseHold(owner_id, 7, datetime.now(timezone.utc)),
    )
    monkeypatch.setattr(elector, "_db_factory", lambda: type("Db", (), {"close": lambda self: None})())
    assert elector.elect_once() is True
    assert leadership.background_leader_fence() == 7
    monkeypatch.setattr(leadership, "claim_lease", lambda db, owner_id, **kw: None)
    assert elector.elect_once() is False
    assert leadership.background_leader_fence() is None  # lost: no fence to write under
    leadership.reset_background_elector()


def test_a_failed_claim_attempt_exposes_no_fence(monkeypatch):
    """★ `elect_once` swallows a claim EXCEPTION into "not leader" — but `_hold` keeps the last
    good value. The fence property must consult leadership, not just the stale hold, or a
    process whose database blipped would keep writing under a fence it no longer holds."""
    leadership.reset_background_elector()
    elector = leadership.get_background_elector(db_factory=lambda: None, owner_id="X", enabled=True)
    monkeypatch.setattr(elector, "_db_factory", lambda: type("Db", (), {"close": lambda self: None})())
    monkeypatch.setattr(
        leadership, "claim_lease",
        lambda db, owner_id, **kw: LeaseHold(owner_id, 7, datetime.now(timezone.utc)),
    )
    assert elector.elect_once() is True and leadership.background_leader_fence() == 7

    def _boom(db, owner_id, **kw):
        raise RuntimeError("database blipped")

    monkeypatch.setattr(leadership, "claim_lease", _boom)
    assert elector.elect_once() is False
    assert leadership.background_leader_fence() is None
    leadership.reset_background_elector()


# ---------------------------------------------------------------------------
# ★ Wiring — the least-idempotent job refuses to dispatch through the REAL entry point
# ---------------------------------------------------------------------------

class _SyncThread:
    def __init__(self, **kwargs):
        self._target = kwargs["target"]

    def start(self):
        self._target()


def _approved_orphan(db):
    from AINDY.db.models import AgentRun
    from AINDY.platform_layer.scheduler_service import ORPHANED_APPROVED_THRESHOLD_MINUTES

    run = AgentRun(
        user_id=uuid.uuid4(), goal="fence test", plan={"steps": []}, executive_summary="x",
        overall_risk="low", status="approved", steps_total=0,
        approved_at=datetime.now(timezone.utc) - timedelta(minutes=ORPHANED_APPROVED_THRESHOLD_MINUTES + 5),
    )
    db.add(run)
    db.commit()
    return run


def _run_orphan_job(mock_db, *, held_fence):
    from AINDY.platform_layer.scheduler_service import _recover_orphaned_approved_runs

    calls: list = []
    with (
        patch("AINDY.db.database.SessionLocal", return_value=mock_db),
        patch("AINDY.platform_layer.scheduler_service.threading.Thread", _SyncThread),
        patch("AINDY.agents.agent_runtime.execute_run", side_effect=lambda **kw: calls.append(kw)),
        patch("AINDY.platform_layer.leadership.background_leader_fence", return_value=held_fence),
    ):
        _recover_orphaned_approved_runs()
    return calls


def test_orphan_recovery_dispatches_under_its_own_fence_and_refuses_under_a_stale_one(mock_db):
    _approved_orphan(mock_db)
    hold = claim_lease(mock_db, "A")                      # this process leads under fence 1
    assert _run_orphan_job(mock_db, held_fence=hold.fence) != []   # control: dispatched
    _approved_orphan(mock_db)
    _expire(mock_db, name=leadership.LEASE_NAME)
    assert claim_lease(mock_db, "B").fence == 2           # someone else took over
    assert _run_orphan_job(mock_db, held_fence=hold.fence) == [], "a stale leader re-dispatched an orphan"
    leadership.release_lease(mock_db, "B")


def test_orphan_recovery_is_unfenced_on_the_in_process_profile(mock_db):
    _approved_orphan(mock_db)
    assert _run_orphan_job(mock_db, held_fence=None) != []


# ---------------------------------------------------------------------------
# ★ Wiring — the second consumer: a deferred job is not re-dispatched under a stale fence
# ---------------------------------------------------------------------------

def _deferred_job(db):
    from AINDY.db.models.job_log import JobLog

    log = JobLog(
        id=str(uuid.uuid4()), source="test", task_name="fence.job", payload={}, status="deferred",
        scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(log)
    db.commit()
    return log


def _run_deferred(mock_db, *, held_fence):
    from AINDY.platform_layer import async_job_service as ajs

    executed: list = []
    with (
        patch.object(ajs, "SessionLocal", return_value=mock_db),
        patch.object(ajs, "evaluate_live_trigger", return_value={"decision": "execute"}),
        patch.object(ajs, "record_decision", return_value=None),
        patch.object(ajs, "_execute_job", side_effect=lambda *a, **kw: executed.append(a)),
        patch("AINDY.platform_layer.leadership.background_leader_fence", return_value=held_fence),
    ):
        ajs.process_deferred_jobs()
    return executed


@pytest.fixture
def private_db():
    """A private SQLite engine, NOT the shared fixture: the code under test calls `db.rollback()`
    in its refusal branch, and under the shared fixture's outer transaction that rollback erases
    the test's own inserted row as well (the `ASYNC-JOB-UNREGISTERED-STORM-1` lesson)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import tests.fixtures.db  # noqa: F401  — registers the JSONB/UUID/Vector SQLite compilers
    import AINDY.db.model_registry  # noqa: F401
    from AINDY.db.database import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_deferred_job_retry_dispatches_under_its_own_fence_and_refuses_under_a_stale_one(private_db):
    from AINDY.db.models.job_log import JobLog

    mock_db = private_db

    def _status(log_id):
        # the job closes the session it was handed; re-read by id rather than touch the instance
        return mock_db.query(JobLog.status).filter(JobLog.id == log_id).scalar()

    first_id = _deferred_job(mock_db).id
    hold = claim_lease(mock_db, "A")
    assert _run_deferred(mock_db, held_fence=hold.fence) != []            # control: dispatched
    assert _status(first_id) == "pending"

    stale_id = _deferred_job(mock_db).id
    _expire(mock_db, name=leadership.LEASE_NAME)
    assert claim_lease(mock_db, "B").fence == 2                            # takeover
    assert _run_deferred(mock_db, held_fence=hold.fence) == [], "a stale leader re-dispatched a deferred job"
    assert _status(stale_id) == "deferred"                                 # the write was rolled back
    leadership.release_lease(mock_db, "B")
