"""FR-15 (b) — a slow execution must not stop parked flows from waking.

Wait firing used to run only as a prelude to dispatch, inside `schedule()`. Dispatch is
INLINE by default (FR-15), and the driving APScheduler job is `max_instances=1`, so while
one flow executed the next tick was **skipped entirely** and no time-based wait fired.

That is a correctness problem, not a latency one: a flow parked on a timer stayed parked
because an *unrelated* flow was busy. It is also why `/health` went down for 13 minutes in
the reported incident — the same tick drove wait expiry and stale-wait cleanup.

Two things are pinned here, and the second is the one that is easy to get wrong:

1. **The API split** — `schedule(tick_waits=False)` no longer fires waits, `tick_waits()`
   does, and a direct caller of `schedule()` keeps the historical behaviour.
2. **The structural split** — the wait tick has its own APScheduler *executor*, not merely
   its own job. `max_instances` is per-job but the thread pool is shared: 16 jobs against a
   default pool of 10, several able to block for `DB_POOL_TIMEOUT` (60s). Its own job alone
   is a probabilistic guarantee; its own thread is a real one.

Plus the concurrency assumption that makes the split legal at all: `tick_time_waits` claims
a due wait under the lock and fires it after releasing, so two callers cannot double-fire.
"""
from __future__ import annotations

import threading

import pytest

pytestmark = pytest.mark.runtime_only


def _engine():
    from AINDY.kernel.scheduler_engine import SchedulerEngine

    return SchedulerEngine()


def _register_due_time_wait(engine, run_id: str, fired: list[str]):
    """Register a time-wait whose trigger is already in the past."""
    from datetime import timedelta

    from AINDY.kernel.clock import utcnow

    engine.register_wait(
        run_id=run_id,
        wait_for_event="timer",
        tenant_id="t-1",
        eu_id=f"eu-{run_id}",
        resume_callback=lambda: fired.append(run_id),
        wait_condition={
            "type": "time",
            "trigger_at": (utcnow() - timedelta(seconds=30)).isoformat(),
        },
    )


# --------------------------------------------------------------------------------------
# 1. The API split
# --------------------------------------------------------------------------------------


def test_tick_waits_fires_a_due_time_wait():
    """Liveness control for the whole file.

    Every assertion below is about a wait *not* firing or firing *elsewhere*; if
    `tick_waits()` were broken outright they would all pass vacuously.
    """
    engine = _engine()
    fired: list[str] = []
    _register_due_time_wait(engine, "run-live", fired)

    count = engine.tick_waits()

    assert count >= 1, "tick_waits() fired nothing — the rest of this file is vacuous"
    assert "run-live" not in engine._waiting, "a fired wait must be claimed and removed"


def test_schedule_with_tick_waits_false_does_not_fire_waits():
    """The decoupling itself: dispatch no longer owns wait firing."""
    engine = _engine()
    fired: list[str] = []
    _register_due_time_wait(engine, "run-a", fired)

    engine.schedule(tick_waits=False)

    assert "run-a" in engine._waiting, (
        "schedule(tick_waits=False) fired a wait — dispatch is still coupled to wait "
        "maintenance, which is exactly what FR-15 (b) removes"
    )


def test_schedule_defaults_to_the_historical_behaviour():
    """A direct caller that never heard of this change must be unaffected.

    The default is True deliberately: `schedule()` is reachable from outside this repo, and
    silently dropping wait maintenance from it would be a behaviour change disguised as a
    refactor.
    """
    engine = _engine()
    fired: list[str] = []
    _register_due_time_wait(engine, "run-b", fired)

    engine.schedule()

    assert "run-b" not in engine._waiting, "schedule() must still tick waits by default"


# --------------------------------------------------------------------------------------
# 2. The concurrency assumption that makes the split legal
# --------------------------------------------------------------------------------------


def test_concurrent_ticks_fire_each_wait_exactly_once():
    """Two jobs now drive wait firing paths concurrently; double-firing would be a defect.

    `tick_time_waits` claims a wait by deleting it from `_waiting` **under the lock** and
    fires it only after releasing, so the claim is atomic. This asserts that rather than
    trusting the reading — a resumed flow running twice is a duplicated side effect.
    """
    engine = _engine()

    from datetime import timedelta

    from AINDY.kernel.clock import utcnow

    for i in range(25):
        engine.register_wait(
            run_id=f"run-{i}",
            wait_for_event="timer",
            tenant_id="t-1",
            eu_id=f"eu-{i}",
            resume_callback=lambda: None,
            wait_condition={
                "type": "time",
                "trigger_at": (utcnow() - timedelta(seconds=30)).isoformat(),
            },
        )

    barrier = threading.Barrier(8)

    def _tick():
        barrier.wait()
        engine.tick_time_waits()

    threads = [threading.Thread(target=_tick, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15.0)

    assert not any(t.is_alive() for t in threads), "a concurrent tick deadlocked"
    # `tick_time_waits` ENQUEUES the resume callback; it does not invoke it (dispatch does).
    # So the observable is the enqueue count, not callback invocations — asserting on the
    # latter would have failed for the wrong reason and hidden a genuine double-fire.
    assert engine._total_enqueued == 25, (
        f"25 due waits produced {engine._total_enqueued} enqueues across 8 concurrent "
        f"ticks — a duplicate means a resumed flow runs twice"
    )
    assert engine._waiting == {}, "every due wait must be claimed and removed"


# --------------------------------------------------------------------------------------
# 3. The structural split — its own executor, not merely its own job
# --------------------------------------------------------------------------------------


@pytest.fixture
def _started_scheduler():
    from AINDY.platform_layer import scheduler_service as ss

    ss.start()
    try:
        yield ss._scheduler
    finally:
        ss.stop(timeout_seconds=2)


def test_wait_tick_runs_on_a_dedicated_executor(_started_scheduler):
    """Its own job is not enough — `max_instances` is per-job, the thread pool is shared."""
    jobs = {j.id: j for j in _started_scheduler.get_jobs()}

    assert "scheduler_wait_tick" in jobs, "the wait tick job is not registered"
    assert jobs["scheduler_wait_tick"].executor == "waits", (
        "the wait tick shares the default pool; with 16 jobs against 10 workers, several "
        "able to block for DB_POOL_TIMEOUT, that is a probabilistic guarantee not a real one"
    )
    assert jobs["scheduler_heartbeat_tick"].executor == "default"


def test_wait_executor_cannot_be_consumed_by_other_jobs(_started_scheduler):
    """The dedicated pool exists and is separate from the shared one."""
    executors = _started_scheduler._executors

    assert "waits" in executors and "default" in executors
    assert executors["waits"] is not executors["default"]
    assert executors["waits"]._pool._max_workers == 1
    assert executors["default"]._pool._max_workers == 10


def test_both_ticks_are_still_single_instance(_started_scheduler):
    """Coalescing protection must survive the split — neither tick may stack."""
    jobs = {j.id: j for j in _started_scheduler.get_jobs()}

    for job_id in ("scheduler_heartbeat_tick", "scheduler_wait_tick"):
        assert jobs[job_id].max_instances == 1, f"{job_id} may stack overlapping runs"


def test_heartbeat_no_longer_ticks_waits(monkeypatch):
    """The service-level half: the dispatch tick must pass tick_waits=False.

    Asserted behaviourally by capturing what the tick asks of the engine, because passing
    True here would silently reinstate the coupling while every other test still passed.
    """
    from AINDY.platform_layer import scheduler_service as ss

    seen: dict = {}

    class _FakeEngine:
        def schedule(self, *, tick_waits=True):
            seen["tick_waits"] = tick_waits
            return 0

    monkeypatch.setattr(
        "AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: _FakeEngine()
    )

    ss._scheduler_heartbeat_tick()

    assert seen.get("tick_waits") is False, (
        "the dispatch tick still drives wait maintenance — the coupling is back"
    )
