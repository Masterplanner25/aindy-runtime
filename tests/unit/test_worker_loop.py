"""ECOGAP-6 Phase 1 — coverage for the distributed worker loop.

`AINDY/worker/worker_loop.py` is the prod-default (distributed) async-job executor
and was previously untested — it is never loaded on the inline/TESTING path, so
nothing exercised it. These tests drive its logic directly with an injected fake
queue backend and patched DB-claim / execute seams (no live DB or Redis), covering:
health/heartbeat state, the concurrency semaphore, signal draining, the DLQ
failure-rate alert window, trace-context round-trip, the core process_one_job
branches (idle / happy / already-claimed / missing / failure→DLQ / shutdown-requeue),
dead-letter drain, stale-recovery, and the single-thread loop.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from AINDY.worker import worker_loop as wl

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _clean_worker_state():
    wl.reset_worker_state()
    yield
    wl.reset_worker_state()


class _FakeJob:
    def __init__(self, job_id="job-1", *, context=None, task_name="op.test"):
        self.job_id = job_id
        self.context = context or {}
        self.idempotency_key = job_id
        self.operation_name = task_name
        self.task_name = task_name


class _FakeQueue:
    """Minimal DistributedQueueBackend: hands out queued jobs, records ack/fail/enqueue."""

    def __init__(self, jobs=None, metrics=None):
        self._jobs = list(jobs or [])
        self._metrics = metrics or {"total_pending_jobs": 3, "max_queue_size": 100}
        self.acked: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.enqueued: list = []

    def dequeue(self, timeout=5):
        return self._jobs.pop(0) if self._jobs else None

    def ack(self, job_id):
        self.acked.append(job_id)

    def fail(self, job_id, error):
        self.failed.append((job_id, error))

    def enqueue(self, job):
        self.enqueued.append(job)

    def get_metrics(self):
        return self._metrics


# --- health / heartbeat state ---


def test_heartbeat_marks_ready_after_first_iteration():
    assert wl.get_worker_health_snapshot()["state"] == "STARTING"
    wl._record_worker_heartbeat(iteration_completed=True, queue_backend=_FakeQueue())
    snap = wl.get_worker_health_snapshot()
    assert snap["state"] == "READY"
    assert snap["first_iteration_complete"] is True
    assert snap["queue_depth"] == 3
    assert snap["queue_capacity"] == 100


def test_heartbeat_does_not_override_draining():
    wl._set_worker_state("DRAINING")
    wl._record_worker_heartbeat(iteration_completed=True, queue_backend=_FakeQueue())
    assert wl.get_worker_health_snapshot()["state"] == "DRAINING"


def test_active_jobs_counter_floors_at_zero():
    wl._increment_active_jobs()
    wl._increment_active_jobs()
    wl._decrement_active_jobs()
    wl._decrement_active_jobs()
    wl._decrement_active_jobs()  # extra decrement must not go negative
    assert wl.get_worker_health_snapshot()["active_jobs"] == 0


# --- concurrency semaphore ---


def test_semaphore_unlimited_when_zero(monkeypatch):
    monkeypatch.setenv("WORKER_MAX_CONCURRENT_JOBS", "0")
    assert wl._get_semaphore() is None


def test_semaphore_singleton_when_bounded(monkeypatch):
    monkeypatch.setenv("WORKER_MAX_CONCURRENT_JOBS", "2")
    sem = wl._get_semaphore()
    assert sem is not None
    assert wl._get_semaphore() is sem  # reused singleton


# --- signal draining ---


def test_signal_sets_draining_and_stop():
    wl._handle_signal(15, None)
    assert wl._STOP.is_set()
    assert wl.get_worker_health_snapshot()["state"] == "DRAINING"


# --- failure-rate alert window ---


def test_failure_window_prunes_and_counts(monkeypatch):
    monkeypatch.setenv("DLQ_ALERT_THRESHOLD", "10")
    for _ in range(3):
        wl._record_job_failure_alert(job_id="j", operation_name="op", error="boom")
    stats = wl.get_failure_rate_stats()
    assert stats["failures_in_window"] == 3
    assert stats["threshold"] == 10


def test_failure_window_evicts_old_entries():
    # Seed an entry far in the past; prune must drop it.
    wl._failure_window.append(0.0)
    wl._prune_failure_window(now=wl._FAILURE_WINDOW_SECONDS + 100)
    assert len(wl._failure_window) == 0


# --- trace context round-trip ---


def test_trace_context_restore_and_reset_no_leak():
    from AINDY.platform_layer.trace_context import get_current_trace_id

    tokens = wl._restore_trace_context({"trace_id": "trace-xyz", "eu_id": "eu-1"})
    assert get_current_trace_id() == "trace-xyz"
    wl._reset_trace_context(tokens)
    # After reset the restored value must not leak into the next job.
    assert get_current_trace_id() != "trace-xyz"


# --- process_one_job branches ---


def _patch_execution(monkeypatch, *, claim=True, job_data=("op.test", {"operation_name": "op.test"})):
    executed: list = []
    monkeypatch.setattr(wl, "_emit_worker_event", lambda *a, **k: None)
    monkeypatch.setattr(wl, "_try_claim_job", lambda job_id: claim)
    monkeypatch.setattr(wl, "_fetch_job_data", lambda job_id: job_data)
    monkeypatch.setattr(
        "AINDY.platform_layer.async_job_service._execute_job",
        lambda job_id, task_name, payload: executed.append((job_id, task_name)),
    )
    return executed


def test_process_one_job_idle_returns_false(monkeypatch):
    _patch_execution(monkeypatch)
    assert wl.process_one_job(_FakeQueue(jobs=[])) is False


def test_process_one_job_happy_path(monkeypatch):
    executed = _patch_execution(monkeypatch)
    q = _FakeQueue(jobs=[_FakeJob("job-9")])
    assert wl.process_one_job(q) is True
    assert executed == [("job-9", "op.test")]
    assert q.acked == ["job-9"]
    assert q.failed == []


def test_process_one_job_already_claimed_skips_execution(monkeypatch):
    executed = _patch_execution(monkeypatch, claim=False)
    q = _FakeQueue(jobs=[_FakeJob("job-dup")])
    assert wl.process_one_job(q) is True
    assert executed == []  # never executed
    assert q.acked == ["job-dup"]  # but acked so it leaves the queue


def test_process_one_job_missing_joblog_acks(monkeypatch):
    executed = _patch_execution(monkeypatch, job_data=None)
    q = _FakeQueue(jobs=[_FakeJob("job-gone")])
    assert wl.process_one_job(q) is True
    assert executed == []
    assert q.acked == ["job-gone"]


def test_process_one_job_failure_goes_to_dlq(monkeypatch):
    _patch_execution(monkeypatch)

    def _boom(job_id, task_name, payload):
        raise RuntimeError("handler blew up")

    monkeypatch.setattr("AINDY.platform_layer.async_job_service._execute_job", _boom)
    q = _FakeQueue(jobs=[_FakeJob("job-bad")])
    assert wl.process_one_job(q) is True
    assert q.failed and q.failed[0][0] == "job-bad"
    assert q.acked == []  # failed jobs are not acked
    assert wl.get_failure_rate_stats()["failures_in_window"] == 1


def test_process_one_job_requeues_on_shutdown_at_capacity(monkeypatch):
    _patch_execution(monkeypatch)
    monkeypatch.setenv("WORKER_MAX_CONCURRENT_JOBS", "1")
    sem = wl._get_semaphore()
    sem.acquire()  # exhaust the single slot
    wl._STOP.set()  # shutdown requested
    q = _FakeQueue(jobs=[_FakeJob("job-park")])
    # Can't get a slot + _STOP set → job is requeued and the call returns False.
    assert wl.process_one_job(q) is False
    assert q.enqueued and q.enqueued[0].job_id == "job-park"
    sem.release()


# --- dead-letter drain ---


class _FakeDLQ:
    def __init__(self, letters):
        self._letters = letters
        self.enqueued: list = []

    def get_dead_letters(self):
        return list(self._letters)

    def enqueue(self, payload):
        self.enqueued.append(payload)


def _patch_dlq(monkeypatch, queue):
    monkeypatch.setattr("AINDY.core.distributed_queue.get_queue", lambda: queue)
    monkeypatch.setattr(
        "AINDY.platform_layer.async_job_service._emit_async_system_event",
        lambda **k: None,
    )


def test_drain_dead_letters_inspect_only(monkeypatch):
    q = _FakeDLQ([{"job_id": "d1", "task_name": "t"}, {"job_id": "d2", "task_name": "t"}])
    _patch_dlq(monkeypatch, q)
    result = wl.drain_dead_letters(db=MagicMock(), max_items=50, requeue=False)
    assert result["inspected"] == 2
    assert result["requeued"] == 0
    assert q.enqueued == []


def test_drain_dead_letters_requeue(monkeypatch):
    class _P:
        task_name = "t"

        def to_json(self):
            return "{}"

    q = _FakeDLQ([{"job_id": "d1", "payload": _P()}])
    _patch_dlq(monkeypatch, q)
    result = wl.drain_dead_letters(db=MagicMock(), max_items=50, requeue=True)
    assert result["inspected"] == 1
    assert result["requeued"] == 1
    assert len(q.enqueued) == 1


def test_drain_dead_letters_zero_max_is_noop(monkeypatch):
    _patch_dlq(monkeypatch, _FakeDLQ([{"job_id": "d1"}]))
    assert wl.drain_dead_letters(db=MagicMock(), max_items=0) == {
        "inspected": 0,
        "requeued": 0,
        "errors": [],
    }


# --- stale recovery + single-thread loop ---


def test_stale_recovery_scans_once_then_stops(monkeypatch):
    monkeypatch.setattr(wl.time, "sleep", lambda s: None)
    calls: list[int] = []

    class _Q:
        def requeue_stale_jobs(self, vt):
            calls.append(vt)
            wl._STOP.set()  # stop after the first scan
            return 2

    wl._run_stale_recovery(_Q(), visibility_timeout=300, check_interval=1)
    assert calls == [300]


def test_single_thread_loop_runs_then_exits(monkeypatch):
    monkeypatch.setattr(wl.time, "sleep", lambda s: None)
    ran: list[int] = []

    def _one(queue_backend=None):
        ran.append(1)
        wl._STOP.set()
        return True

    monkeypatch.setattr(wl, "process_one_job", _one)
    wl._single_thread_loop(_FakeQueue())
    assert ran == [1]
