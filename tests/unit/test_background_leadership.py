"""Unit tests for background-task lease election (LEASE-1).

Covers the atomic claim/renew/takeover/release semantics of the
``background_task_leases`` lease and the ``BackgroundLeadershipElector``
acquire/lose transition callbacks.

Note on concurrency: the cross-instance mutual-exclusion guarantee relies on
``SELECT ... FOR UPDATE`` row locking, which is a PostgreSQL behaviour. These
unit tests run on the SQLite harness and exercise the single-threaded state
machine (who-holds-the-lease-after-each-call); true concurrent contention is
covered by the production PostgreSQL deployment.
"""
from contextlib import contextmanager
from datetime import timedelta

from AINDY.db.database import utcnow
from AINDY.platform_layer import leadership
from AINDY.platform_layer.leadership import (
    BackgroundLeadershipElector,
    current_lease,
    release_lease,
    try_acquire_lease,
)


@contextmanager
def _session(factory):
    """One short-lived session per call — mirrors production, where the elector
    opens and closes a fresh session for every acquisition attempt."""
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_first_instance_acquires_lease(testing_session_factory):
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-A") is True
    with _session(testing_session_factory) as s:
        row = current_lease(s)
        assert row is not None
        assert row.owner_id == "owner-A"


def test_second_instance_does_not_acquire_live_lease(testing_session_factory):
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-A") is True
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-B") is False
    with _session(testing_session_factory) as s:
        assert current_lease(s).owner_id == "owner-A"


def test_same_owner_renew_extends_expiry(testing_session_factory):
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-A", ttl_seconds=10) is True
        first_expiry = current_lease(s).expires_at
    # Renew with a longer TTL — the new expiry must be strictly later regardless
    # of how little wall-clock elapsed between the two calls.
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-A", ttl_seconds=600) is True
        second_expiry = current_lease(s).expires_at
    assert second_expiry > first_expiry


def test_takeover_after_expiry(testing_session_factory):
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-A", ttl_seconds=60) is True
    # Simulate owner-A dying: force its lease into the past.
    with _session(testing_session_factory) as s:
        row = current_lease(s)
        row.expires_at = utcnow() - timedelta(seconds=5)
        s.commit()
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-B") is True
    with _session(testing_session_factory) as s:
        assert current_lease(s).owner_id == "owner-B"


def test_release_allows_reacquire_by_other(testing_session_factory):
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-A") is True
    with _session(testing_session_factory) as s:
        release_lease(s, "owner-A")
    with _session(testing_session_factory) as s:
        assert current_lease(s) is None
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-B") is True


def test_release_is_noop_for_non_owner(testing_session_factory):
    with _session(testing_session_factory) as s:
        assert try_acquire_lease(s, "owner-A") is True
    # owner-B does not hold the lease; release must not remove owner-A's row.
    with _session(testing_session_factory) as s:
        release_lease(s, "owner-B")
    with _session(testing_session_factory) as s:
        assert current_lease(s).owner_id == "owner-A"


class _FakeDB:
    closed = False

    def close(self):
        self.closed = True


def _elector(monkeypatch, *, results, enabled=True):
    """Build an elector whose acquisition outcome is scripted by ``results``."""
    calls = {"acquire": 0, "lose": 0}

    def on_acquire():
        calls["acquire"] += 1

    def on_lose():
        calls["lose"] += 1

    outcomes = iter(results)

    def fake_acquire(db, owner_id, **kwargs):
        return next(outcomes)

    monkeypatch.setattr(leadership, "try_acquire_lease", fake_acquire)
    elector = BackgroundLeadershipElector(
        db_factory=_FakeDB,
        owner_id="owner-X",
        on_acquire=on_acquire,
        on_lose=on_lose,
        enabled=enabled,
    )
    return elector, calls


def test_elector_invokes_on_acquire_once_on_election(monkeypatch):
    elector, calls = _elector(monkeypatch, results=[True, True])
    assert elector.elect_once() is True
    assert elector.is_leader is True
    # A second winning tick must not re-fire on_acquire.
    assert elector.elect_once() is True
    assert calls["acquire"] == 1
    assert calls["lose"] == 0


def test_elector_stands_down_on_lease_loss(monkeypatch):
    elector, calls = _elector(monkeypatch, results=[True, False])
    assert elector.elect_once() is True
    assert elector.elect_once() is False
    assert elector.is_leader is False
    assert calls["acquire"] == 1
    assert calls["lose"] == 1


def test_elector_disabled_never_leads(monkeypatch):
    elector, calls = _elector(monkeypatch, results=[True], enabled=False)
    assert elector.elect_once() is False
    assert elector.is_leader is False
    assert calls["acquire"] == 0


def test_elector_acquire_exception_is_not_leader(monkeypatch):
    def boom(db, owner_id, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(leadership, "try_acquire_lease", boom)
    elector = BackgroundLeadershipElector(
        db_factory=_FakeDB, owner_id="owner-X"
    )
    assert elector.elect_once() is False
    assert elector.is_leader is False
