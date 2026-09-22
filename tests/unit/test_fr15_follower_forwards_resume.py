"""FR-15 silent losses #5 and #6 — found on the evidence topology (DEC-072), 2026-09-22.

#5 — a FOLLOWER api enqueued a woken resume into a queue only a leader drains. Under
`EXECUTION_MODE=distributed` the background lease is contended; an api that lost it runs no
scheduler heartbeat, so `schedule()` is never called in that process — yet it still registers
waits (it ran the flow) and still answers the resume route, whose `notify_event` put the woken
resume into the process-local queue. Observed: `woken: true`, the run `waiting` for 20+ minutes,
`aindy_execution_dispatch_total` absent, `aindy_async_queue_enqueue_total` absent, DLQ flat.
Now: with no local drainer and distributed mode, `_enqueue_resume` dispatches the item NOW through
the same call `schedule()` makes; a leader keeps the queue.

#6 — the worker never marked its scheduler's rehydration complete (the api's lifespan does that),
so every bus event it received was buffered and, at 1000, dropped. A worker that became leader
held rehydrated wait registrations nothing could wake. Now the worker rehydrates and opens its
scheduler at startup.

Mutations: revert `_enqueue_resume` to plain `enqueue` → the forwarding test fails (the item
sits in the queue, `_dispatch_item_now` never called); drop `_rehydrate_and_open_scheduler` from
the worker's main → the worker test fails on `_rehydration_complete`.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from AINDY.kernel.scheduler.engine import SchedulerEngine

pytestmark = pytest.mark.runtime_only


def _engine() -> SchedulerEngine:
    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    return eng


def _reg(engine: SchedulerEngine, cb) -> None:
    engine.register_wait(run_id="run-1", wait_for_event="review.approved", tenant_id="t1",
                         eu_id="eu-1", resume_callback=cb, eu_type="flow")


class _NotRunning:
    running = False


class _Running:
    running = True


def test_a_follower_under_distributed_mode_forwards_the_woken_resume_now(monkeypatch):
    """No heartbeat in this process + distributed mode → the item goes to the dispatcher at
    once (the async hint routes it to the durable queue), not into the local queue."""
    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.scheduler_service._scheduler", _NotRunning(), raising=False)
    engine = _engine()
    cb = MagicMock(name="resume_callback")
    _reg(engine, cb)
    dispatched: list = []
    with patch.object(engine, "_dispatch_item_now", side_effect=lambda item: dispatched.append(item)):
        woken = engine.notify_event("review.approved")
    assert woken == 1
    assert len(dispatched) == 1 and dispatched[0].run_id == "run-1" and dispatched[0].run_callback is cb
    assert engine.dequeue_next() is None, "the item must NOT sit in a queue nothing drains"


def test_a_leader_with_a_heartbeat_keeps_the_queue(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.scheduler_service._scheduler", _Running(), raising=False)
    engine = _engine()
    _reg(engine, MagicMock())
    with patch.object(engine, "_dispatch_item_now") as now:
        engine.notify_event("review.approved")
    now.assert_not_called()
    assert engine.dequeue_next() is not None, "the leader's own schedule() drains it, as before"


def test_thread_mode_keeps_the_queue_even_without_a_heartbeat(monkeypatch):
    """Single-instance / thread mode: the local boolean makes every process the leader; the
    forward is a distributed-mode rule only (that is where `_enqueue_distributed` exists)."""
    monkeypatch.setenv("EXECUTION_MODE", "thread")
    monkeypatch.setattr("AINDY.platform_layer.scheduler_service._scheduler", _NotRunning(), raising=False)
    engine = _engine()
    _reg(engine, MagicMock())
    with patch.object(engine, "_dispatch_item_now") as now:
        engine.notify_event("review.approved")
    now.assert_not_called()
    assert engine.dequeue_next() is not None


def test_a_failed_forward_falls_back_to_the_queue_and_says_so(monkeypatch, caplog):
    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.scheduler_service._scheduler", None, raising=False)
    engine = _engine()
    _reg(engine, MagicMock())
    with patch.object(engine, "_dispatch_item_now", side_effect=RuntimeError("redis down")):
        with caplog.at_level("ERROR", logger="AINDY.kernel.scheduler.common"):
            engine.notify_event("review.approved")
    assert engine.dequeue_next() is not None, "never lost silently: queued, and the log says where"
    assert any("no heartbeat drains it" in r.getMessage() for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_dispatch_item_now_makes_the_same_call_schedule_makes(monkeypatch):
    """The forward is `schedule()`'s per-item dispatch, not a second path: same stub, the resume
    descriptor under RESUME_CONTEXT_KEY, the async hint when the flag is on."""
    from AINDY.core.resume_reconstruction import RESUME_CONTEXT_KEY
    from AINDY.kernel.scheduler.common import ScheduledItem

    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "1")
    engine = _engine()
    cb = MagicMock()
    item = ScheduledItem(execution_unit_id="eu-9", tenant_id="t1", priority="normal",
                         run_callback=cb, run_id="run-9", eu_type="flow")
    with patch("AINDY.core.execution_dispatcher.dispatch") as dispatch:
        engine._dispatch_item_now(item)
    (stub, callback, context), _ = dispatch.call_args
    assert callback is cb and stub.id == "eu-9" and stub.extra == {"async_hint": True}
    assert context["source"] == "scheduler.resume" and context["run_id"] == "run-9"
    assert context[RESUME_CONTEXT_KEY]["run_id"] == "run-9" and context[RESUME_CONTEXT_KEY]["eu_type"] == "flow"


def test_the_forward_is_counted():
    from AINDY.platform_layer.metrics import REGISTRY

    from AINDY.kernel.scheduler import core

    before = REGISTRY.get_sample_value("aindy_scheduler_resume_forwarded_total") or 0.0
    core._count_forwarded_resume()
    assert REGISTRY.get_sample_value("aindy_scheduler_resume_forwarded_total") == before + 1


# ── #6 — the worker opens its scheduler ─────────────────────────────────────────────────

def test_the_worker_rehydrates_and_marks_rehydration_complete(monkeypatch):
    from AINDY.kernel.scheduler_engine import get_scheduler_engine
    from AINDY.worker import __main__ as worker_main

    engine = get_scheduler_engine()
    engine._rehydration_complete.clear()
    calls: list = []
    monkeypatch.setattr("AINDY.core.flow_run_rehydration.rehydrate_waiting_flow_runs",
                        lambda db: calls.append("rehydrated") or 2, raising=True)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: MagicMock(), raising=True)
    bus = MagicMock()
    bus.drain_buffered_events.return_value = 0
    monkeypatch.setattr("AINDY.kernel.event_bus.get_event_bus", lambda: bus, raising=True)

    worker_main._rehydrate_and_open_scheduler(MagicMock())

    assert calls == ["rehydrated"]
    assert engine._rehydration_complete.is_set(), "the worker's scheduler must open, or it drops every bus event"
    bus.drain_buffered_events.assert_called_once()


def test_the_worker_opens_its_scheduler_even_if_rehydration_fails(monkeypatch):
    from AINDY.kernel.scheduler_engine import get_scheduler_engine
    from AINDY.worker import __main__ as worker_main

    engine = get_scheduler_engine()
    engine._rehydration_complete.clear()
    monkeypatch.setattr("AINDY.core.flow_run_rehydration.rehydrate_waiting_flow_runs",
                        lambda db: (_ for _ in ()).throw(RuntimeError("db down")), raising=True)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: MagicMock(), raising=True)
    monkeypatch.setattr("AINDY.kernel.event_bus.get_event_bus", lambda: MagicMock(), raising=True)
    worker_main._rehydrate_and_open_scheduler(MagicMock())
    assert engine._rehydration_complete.is_set()
