"""`LEASE-FENCE-1` — on Postgres, a fenced job's ``FOR SHARE`` BLOCKS a concurrent takeover.

This is the half the unit suite cannot see: on SQLite ``with_for_update`` is a no-op, so
``tests/unit/test_lease_fence.py`` pins the comparison and the wiring, and THIS pins the lock
semantics that make the comparison a fence rather than a race:

    A leads (fence 1), its lease expires (a GC pause), A's job opens a transaction and passes
    ``assert_lease_fence`` (row still fence 1, now held FOR SHARE).
    B tries to take over → its ``SELECT … FOR UPDATE`` must WAIT while A's transaction is open.
    A commits → B's takeover completes with fence 2 → A's NEXT fenced write is refused.

Two real connections, one per side, on the live database (`testing_session_factory` binds the
production engine under `pytest.integration.ini`). Nothing here is timing-dependent in the
direction that matters: the assertion that B is *still blocked* is made after a generous wait,
and the assertion that B *completes* is made after A commits, joined with a long timeout.
"""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import text

from AINDY.db.models.background_task_lease import BackgroundTaskLease
from AINDY.platform_layer.leadership import (
    LeaseFenceLost,
    assert_lease_fence,
    claim_lease,
    release_lease,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

NAME = "fence-contention-test"


def _cleanup(factory):
    with factory() as s:
        s.query(BackgroundTaskLease).filter(BackgroundTaskLease.name == NAME).delete()
        s.commit()


def test_for_share_blocks_a_takeover_until_the_fenced_job_commits(testing_session_factory):
    _cleanup(testing_session_factory)
    a = testing_session_factory()
    try:
        hold = claim_lease(a, "A", name=NAME, ttl_seconds=1)
        assert hold.fence == 1
        time.sleep(1.2)  # A's lease is now EXPIRED — the window in which a takeover is legal

        # A's job: inside its own transaction, the fence still reads 1 → passes, and the row
        # is now held FOR SHARE by this open transaction.
        assert_lease_fence(a, hold.fence, name=NAME, job="contention-test")

        result: dict = {}

        def _b_takeover():
            b = testing_session_factory()
            try:
                b.execute(text("SET statement_timeout = '15s'"))
                result["started"] = time.monotonic()
                result["hold"] = claim_lease(b, "B", name=NAME, ttl_seconds=60)
                result["finished"] = time.monotonic()
            except Exception as exc:  # noqa: BLE001 — recorded for the assertion
                result["error"] = repr(exc)
            finally:
                b.close()

        t = threading.Thread(target=_b_takeover, daemon=True)
        t.start()
        t.join(timeout=2.0)
        # ★ The fence: while A's transaction holds FOR SHARE, B's FOR UPDATE cannot proceed.
        assert t.is_alive(), f"takeover completed while the fenced job's transaction was open: {result}"
        assert "hold" not in result

        a.commit()  # A's job commits — and only now can B take over
        t.join(timeout=20.0)
        assert not t.is_alive(), "takeover never completed after the fenced job committed"
        assert "error" not in result, result["error"]
        assert result["hold"].fence == 2
        assert result["finished"] - result["started"] >= 1.5, "B was not actually blocked"

        # A's next fenced write is refused — the stale leader is refused, not asked to notice.
        with pytest.raises(LeaseFenceLost):
            assert_lease_fence(a, hold.fence, name=NAME, job="contention-test")
        a.rollback()
    finally:
        a.close()
        with testing_session_factory() as s:
            release_lease(s, "B", name=NAME)
        _cleanup(testing_session_factory)
