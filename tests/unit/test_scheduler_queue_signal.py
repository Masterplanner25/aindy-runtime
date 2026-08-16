"""FR-15 — the wait to enter the execution pipeline must be visible.

Between an item entering the scheduler queue and `execution.started` firing, nothing was
emitted. The app team measured **177 seconds** of that silence; a queued request and a hung
process are externally identical inside it.

What these tests pin, in order of how expensive the mistake would be:

1. **The event is not `execution.`-prefixed.** The execution-contract gate raises for any
   `execution.*` event emitted outside a pipeline, and the hottest enqueue callers — the
   event-bus subscriber thread and wait expiry — have none. The app team asked for
   `execution.queued` by name; that name would raise in exactly the paths that matter.
   `test_emitting_outside_a_pipeline_does_not_raise` is the behavioural proof.

2. **`skip_memory_capture=True`.** This fires *on the enqueue path*. RT-MEMTXN-LEAK-1's rule
   is that a memory capture must never enqueue work whose own lifecycle events are capturable
   — capturing this would close that cycle, on the path already under load.

3. **The DB write happens outside the scheduler lock.** Holding it across a session write
   would serialise every enqueue behind a database round-trip, which is the same class of
   defect this signal exists to reveal.
"""
from __future__ import annotations

import threading

import pytest

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def captured(monkeypatch):
    """Capture emit_system_event calls without touching a database."""
    calls: list[dict] = []

    import AINDY.core.system_event_service as ses

    def _fake_emit(**kwargs):
        calls.append(kwargs)
        return None

    class _FakeSession:
        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(ses, "emit_system_event", _fake_emit)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda *a, **k: _FakeSession())
    monkeypatch.delenv("AINDY_SCHEDULER_QUEUE_EVENTS", raising=False)
    return calls


def _emit(**overrides):
    from AINDY.core.scheduler_queue_signal import emit_scheduler_queued

    kwargs = dict(
        execution_unit_id="eu-1",
        tenant_id="t-1",
        priority="normal",
        eu_type="flow",
        queue_depth=7,
    )
    kwargs.update(overrides)
    emit_scheduler_queued(**kwargs)


# --------------------------------------------------------------------------------------
# 1. The name, and why it is not the one that was asked for
# --------------------------------------------------------------------------------------


def test_event_is_not_execution_prefixed():
    """`execution.*` outside a pipeline trips the contract gate. This must not be that."""
    from AINDY.core.system_event_types import SystemEventTypes

    assert SystemEventTypes.SCHEDULER_QUEUED == "scheduler.queued"
    assert not SystemEventTypes.SCHEDULER_QUEUED.startswith("execution."), (
        "an execution.*-prefixed name raises when emitted outside a pipeline, which is "
        "precisely where the scheduler enqueues from"
    )


@pytest.fixture
def _outside_pipeline(monkeypatch):
    """Arrange 'no pipeline active' rather than assuming it, and restore afterwards.

    ★ `pipeline_active` is a ContextVar that an earlier test in the same session can leave
    set. A first draft of the two tests below *asserted* the precondition instead of
    establishing it — they passed in isolation and **failed in CI's full `runtime_only`
    run**, which is precisely the trap CI-MARKER-1 hit with `test_infinity_async_job_loop`.
    Same fix as there: set it, assert it, reset it in a `finally`.

    ★ Gotcha when mutation-checking this fixture: breaking it surfaces as pytest **ERROR**,
    not `FAILED`, because the failure is in setup rather than in a test body. A mutation
    check that greps only `^FAILED` reports zero and reads as "the guard is untested".
    Verified here as `FAILED=0 ERROR=2`. Same family as `exit code 5 is
    EXIT_NOTESTSCOLLECTED, not an error`.
    """
    from AINDY.config import settings
    from AINDY.platform_layer.trace_context import (
        is_pipeline_active,
        reset_pipeline_active,
        set_pipeline_active,
    )

    monkeypatch.setattr(type(settings), "ENFORCE_EXECUTION_CONTRACT", True, raising=False)
    token = set_pipeline_active(False)
    try:
        assert not is_pipeline_active(), "precondition: no pipeline, as on the enqueue path"
        yield
    finally:
        reset_pipeline_active(token)


def test_emitting_outside_a_pipeline_does_not_raise(captured, _outside_pipeline):
    """The behavioural half — asserting the name alone proves nothing about the gate.

    Enforcement is ON. With an `execution.`-prefixed name this raises `RuntimeError`; the
    whole point of the naming choice is that it does not.
    """
    _emit()  # must not raise

    assert len(captured) == 1
    assert captured[0]["event_type"] == "scheduler.queued"


def test_the_real_gate_would_reject_the_name_that_was_requested(_outside_pipeline):
    """Liveness control for the two tests above.

    Without this, both would pass against a gate that had been removed entirely — the
    absence-assertion trap from EVENTBUS-COVERAGE-1. This drives the *real* gate with an
    `execution.`-prefixed name and requires it to object.
    """
    import AINDY.core.system_event_service as ses

    with pytest.raises(RuntimeError, match="outside pipeline"):
        ses.emit_system_event(db=None, event_type="execution.queued", payload={})


# --------------------------------------------------------------------------------------
# 2. The cycle guard
# --------------------------------------------------------------------------------------


def test_memory_capture_is_skipped(captured):
    """RT-MEMTXN-LEAK-1: any capture -> job -> capture edge is a cycle.

    This event fires on the enqueue path, so capturing it would close that loop directly.
    """
    _emit()

    assert captured[0]["skip_memory_capture"] is True, (
        "capturing this event would let a memory capture enqueue work whose own lifecycle "
        "event is capturable — the cycle RT-MEMTXN-LEAK-1 was traced to"
    )


def test_payload_carries_the_depth_that_distinguishes_the_two_causes(captured):
    """Depth separates 'queued behind 40 things' from 'queued alone, dispatcher wedged'.

    Those have the same external symptom and completely different causes, which is why the
    number is worth a column rather than being inferable from the event's existence.
    """
    _emit(queue_depth=42, priority="high", eu_type="agent", run_id="run-9")

    payload = captured[0]["payload"]
    assert payload["queue_depth"] == 42
    assert payload["priority"] == "high"
    assert payload["eu_type"] == "agent"
    assert payload["run_id"] == "run-9"


# --------------------------------------------------------------------------------------
# 3. It must never break the path it observes
# --------------------------------------------------------------------------------------


def test_emit_never_raises_when_the_database_is_unavailable(monkeypatch):
    """Observability must not break the path under load when the signal matters most."""
    def _boom(*a, **k):
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr("AINDY.db.database.SessionLocal", _boom)
    monkeypatch.delenv("AINDY_SCHEDULER_QUEUE_EVENTS", raising=False)

    _emit()  # must not raise


def test_env_flag_disables_the_write(captured, monkeypatch):
    monkeypatch.setenv("AINDY_SCHEDULER_QUEUE_EVENTS", "false")

    _emit()

    assert captured == []


def test_flag_is_resolved_per_call_not_at_import(monkeypatch):
    """Import-time env reads are invisible to behavioural tests — the standing rule."""
    from AINDY.core.scheduler_queue_signal import scheduler_queue_events_enabled

    monkeypatch.setenv("AINDY_SCHEDULER_QUEUE_EVENTS", "false")
    assert scheduler_queue_events_enabled() is False
    monkeypatch.setenv("AINDY_SCHEDULER_QUEUE_EVENTS", "true")
    assert scheduler_queue_events_enabled() is True


# --------------------------------------------------------------------------------------
# 4. Wiring: the scheduler actually emits, and does so outside its lock
# --------------------------------------------------------------------------------------


def _engine():
    from AINDY.kernel.scheduler_engine import SchedulerEngine

    return SchedulerEngine()


def _item(**overrides):
    from AINDY.kernel.scheduler.common import ScheduledItem

    kwargs = dict(
        execution_unit_id="eu-wire",
        tenant_id="t-wire",
        priority="normal",
        run_callback=lambda: None,
        eu_type="flow",
    )
    kwargs.update(overrides)
    return ScheduledItem(**kwargs)


def test_enqueue_emits_the_signal(captured):
    engine = _engine()

    engine.enqueue(_item())

    assert len(captured) == 1, "enqueue did not emit the queued signal"
    assert captured[0]["payload"]["execution_unit_id"] == "eu-wire"
    assert captured[0]["payload"]["queue_depth"] == 1


def test_enqueue_stamps_a_monotonic_timestamp(captured):
    """The wait histogram depends on this being set; 0.0 is treated as 'unknown'."""
    engine = _engine()
    item = _item()

    engine.enqueue(item)

    assert item.enqueued_at_monotonic > 0.0


def test_emit_happens_outside_the_scheduler_lock(monkeypatch):
    """Holding the lock across a DB write would serialise every enqueue behind it.

    That is the same class of defect FR-15 is about, so it is asserted rather than assumed:
    the emit re-enters the engine's own lock, which deadlocks if the caller still holds it
    (`threading.Lock` is not reentrant).
    """
    engine = _engine()
    observed: dict = {}

    def _fake_emit(**kwargs):
        # If enqueue() still held the lock, this blocks forever and the test times out.
        acquired = engine._lock.acquire(timeout=2.0)
        observed["reentered"] = acquired
        if acquired:
            engine._lock.release()

    monkeypatch.setattr(
        "AINDY.core.scheduler_queue_signal.emit_scheduler_queued", _fake_emit
    )

    thread = threading.Thread(target=lambda: engine.enqueue(_item()), daemon=True)
    thread.start()
    thread.join(timeout=10.0)

    assert not thread.is_alive(), "enqueue deadlocked — the emit is inside the lock"
    assert observed.get("reentered") is True, (
        "the scheduler lock was still held during the emit; a DB write there serialises "
        "every enqueue behind it"
    )


def test_dispatch_observes_the_queue_wait(monkeypatch):
    """The duration signal — the number that was missing when a request waited 177s."""
    seen: list[tuple[float, str]] = []

    monkeypatch.setattr(
        "AINDY.core.scheduler_queue_signal.observe_queue_wait",
        lambda seconds, *, priority: seen.append((seconds, priority)),
    )
    monkeypatch.setenv("AINDY_SCHEDULER_QUEUE_EVENTS", "false")  # isolate from the event path

    engine = _engine()
    engine.enqueue(_item(priority="high"))
    engine.schedule()

    assert seen, "dispatch recorded no queue-wait observation"
    seconds, priority = seen[0]
    assert priority == "high"
    assert seconds >= 0.0


def test_histogram_buckets_reach_the_observed_pathological_waits():
    """22s / 48s / 184s were real samples. A histogram topping out near 10s shows nothing."""
    from AINDY.platform_layer.metrics import scheduler_queue_wait_seconds

    buckets = [
        float(b)
        for b in scheduler_queue_wait_seconds._upper_bounds  # type: ignore[attr-defined]
        if b != float("inf")
    ]
    assert max(buckets) >= 300.0, (
        f"top bucket {max(buckets)}s cannot distinguish a 184s wait from a 3000s one"
    )
