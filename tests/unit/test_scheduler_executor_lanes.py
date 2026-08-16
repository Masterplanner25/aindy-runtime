"""SYSMAX-5 — scheduler lanes, sized against the DB pool rather than the job count.

The scheduler ran ~33 jobs (12 runtime + ~21 app-registered) against a single pool of 10
workers, with two unbounded holders: `scheduler_heartbeat_tick` occupies a worker for the whole
duration of an INLINE execution (~13 minutes in the FR-15 incident), and DB-heavy jobs can block
for `DB_POOL_TIMEOUT` (60s) under connection-pool exhaustion.

The failure mode is a **maintenance brownout**: the pool saturates and the remaining jobs
silently stop running — including the recovery jobs whose whole purpose is cleaning up after the
condition that saturated it. Nothing raises.

**The fix is isolation, not capacity**, and `test_total_scheduler_threads_leave_db_headroom`
is why: raising `default` until it exceeds the job count would trade this exhaustion for a worse
one, since all 30 DB connections are shared with request handling. That is the RT-MEMTXN-LEAK-1
shape, where a login took 42 seconds.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

#: Jobs whose value peaks exactly when the scheduler is saturated.
RECOVERY_JOBS = {
    "deferred_async_job_retry",
    "queue_backend_reconnect",
    "expire_timed_out_waits",
    "expire_timed_out_wait_flows",
    "recover_stuck_flow_runs",
    "recover_orphaned_approved_runs",
}


@pytest.fixture
def scheduler():
    from AINDY.platform_layer import scheduler_service as ss

    ss.start()
    try:
        yield ss._scheduler
    finally:
        ss.stop(timeout_seconds=2)


def _jobs_by_executor(scheduler) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for job in scheduler.get_jobs():
        out.setdefault(getattr(job, "executor", "default"), set()).add(job.id)
    return out


# --------------------------------------------------------------------------------------
# The lanes
# --------------------------------------------------------------------------------------


def test_three_lanes_exist_with_their_intended_sizes(scheduler):
    executors = scheduler._executors

    assert set(executors) == {"default", "recovery", "waits"}
    assert executors["default"]._pool._max_workers == 10
    assert executors["recovery"]._pool._max_workers == 2
    assert executors["waits"]._pool._max_workers == 1


def test_every_recovery_job_is_on_the_recovery_lane(scheduler):
    """Asserted per-name. A count would pass on the wrong six."""
    by_executor = _jobs_by_executor(scheduler)
    registered = {j.id for j in scheduler.get_jobs()}
    expected = RECOVERY_JOBS & registered

    assert expected, "no recovery jobs registered — the scan is broken, not the code"
    missing = expected - by_executor.get("recovery", set())
    assert not missing, (
        f"these recovery jobs share the default pool: {sorted(missing)}. They are the ones "
        f"that must still run when it is saturated."
    )


def test_the_unbounded_holder_is_not_on_the_recovery_lane(scheduler):
    """★ The point of the split.

    `scheduler_heartbeat_tick` can occupy a worker for the entire duration of an INLINE
    execution. Putting it on the recovery lane would let one slow flow consume half of a
    two-worker pool and starve exactly the jobs the lane exists to protect.
    """
    by_executor = _jobs_by_executor(scheduler)

    assert "scheduler_heartbeat_tick" not in by_executor.get("recovery", set())
    assert "scheduler_heartbeat_tick" in by_executor.get("default", set())


def test_wait_tick_keeps_its_own_lane(scheduler):
    """FR-15 (b) must survive this change — time-wait firing is a correctness guarantee."""
    by_executor = _jobs_by_executor(scheduler)

    assert by_executor.get("waits") == {"scheduler_wait_tick"}


def test_queue_backend_reconnect_is_protected(scheduler):
    """The sharpest case, called out on its own.

    If the queue backend is down *and* the pool is saturated, the job that would reconnect it
    cannot run. That is a self-sustaining outage, not merely a delayed cleanup.
    """
    by_executor = _jobs_by_executor(scheduler)

    assert "queue_backend_reconnect" in by_executor.get("recovery", set())


# --------------------------------------------------------------------------------------
# Why the fix is isolation and not a bigger number
# --------------------------------------------------------------------------------------


def test_total_scheduler_threads_leave_db_headroom(scheduler):
    """★ The constraint that makes "just raise it" wrong.

    Every scheduler thread can hold a DB session, and `DB_POOL_SIZE + DB_MAX_OVERFLOW` is the
    *shared* budget with request handling. If the scheduler's own lanes could consume most of
    it, the fix for a scheduler brownout would produce an API brownout — which is exactly what
    RT-MEMTXN-LEAK-1 traced when a login took 42s.

    Asserted as a ratio rather than a magic number so it keeps meaning something if either
    side is retuned.
    """
    from AINDY.config import settings

    total_threads = sum(e._pool._max_workers for e in scheduler._executors.values())
    db_capacity = settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW

    assert total_threads <= db_capacity // 2, (
        f"scheduler lanes total {total_threads} threads against {db_capacity} shared DB "
        f"connections. Every one can hold a session, so the scheduler must not be able to "
        f"starve request handling — that trades a scheduler brownout for an API one."
    )


# --------------------------------------------------------------------------------------
# Starvation is now a signal, not just a log line
# --------------------------------------------------------------------------------------


def test_starvation_listener_is_registered(scheduler):
    assert getattr(scheduler, "_listeners", None), (
        "no starvation listener registered — APScheduler reports saturation only as a per-job "
        "log line, which is what made the FR-15 incident invisible as a signal"
    )


@pytest.mark.parametrize(
    "code_name,expected_reason",
    [("EVENT_JOB_MAX_INSTANCES", "max_instances"), ("EVENT_JOB_MISSED", "missed")],
)
def test_listener_counts_each_starvation_reason(code_name, expected_reason, monkeypatch):
    """Drives the real listener body, and checks the label — the two causes differ.

    `max_instances` means the previous run is still going; `missed` means no worker was free.
    Collapsing them would hide which of the two is happening, and they have different fixes.
    """
    from apscheduler import events as ap_events

    from AINDY.platform_layer import scheduler_service as ss

    seen: list[tuple[str, str]] = []

    class _FakeMetric:
        def labels(self, job_id, reason):
            seen.append((job_id, reason))
            return self

        def inc(self):
            pass

    monkeypatch.setattr(
        "AINDY.platform_layer.metrics.scheduler_job_starved_total", _FakeMetric()
    )

    captured = {}

    class _CapturingScheduler:
        def add_listener(self, callback, mask=None):
            captured["callback"] = callback

    ss._install_starvation_listener(_CapturingScheduler())
    assert "callback" in captured, "listener was never registered"

    event = ap_events.JobExecutionEvent(
        getattr(ap_events, code_name), job_id="recover_stuck_flow_runs"
    )
    captured["callback"](event)

    assert seen == [("recover_stuck_flow_runs", expected_reason)]


def test_listener_never_breaks_the_scheduler(monkeypatch):
    """Observability must not prevent the scheduler from starting or a job from being skipped."""
    from apscheduler import events as ap_events

    from AINDY.platform_layer import scheduler_service as ss

    class _Boom:
        def labels(self, **kwargs):
            raise RuntimeError("metrics backend down")

    monkeypatch.setattr("AINDY.platform_layer.metrics.scheduler_job_starved_total", _Boom())

    captured = {}

    class _CapturingScheduler:
        def add_listener(self, callback, mask=None):
            captured["callback"] = callback

    ss._install_starvation_listener(_CapturingScheduler())
    # Must not raise.
    captured["callback"](
        ap_events.JobExecutionEvent(ap_events.EVENT_JOB_MAX_INSTANCES, job_id="x")
    )
