"""
INV-SCHED-001 / INV-SCHED-002 regression tests: scheduler wait/resume lifecycle.

Covers:
- register_wait stores entry with correct fields
- duplicate register_wait overwrites cleanly
- notify_event enqueues callback and removes from _waiting
- notify_event skips on event-type mismatch
- notify_event skips when correlation_id mismatches
- notify_event resumes when emit has no corr_id (entry corr is not an exclusion filter)
- notify_event resumes when entry has no corr_id (emit corr is not required)
- peek_matching_run_ids returns matches without consuming
- tick_time_waits fires past-due triggers and skips future ones
- pre-rehydration events are buffered and replayed on mark_rehydration_complete
- pre-rehydration buffer overflow drops the excess event
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from AINDY.kernel.scheduler.common import _MAX_PRE_REHYDRATION_BUFFER
from AINDY.kernel.scheduler.engine import SchedulerEngine

pytestmark = pytest.mark.runtime_only


@pytest.fixture()
def engine() -> SchedulerEngine:
    """Fresh SchedulerEngine with rehydration already marked complete."""
    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    return eng


@pytest.fixture()
def pre_engine() -> SchedulerEngine:
    """Fresh SchedulerEngine with rehydration NOT yet complete."""
    return SchedulerEngine()


def _noop() -> None:
    pass


def _reg(
    engine: SchedulerEngine,
    run_id: str = "run-1",
    event: str = "flow.complete",
    *,
    corr: str | None = None,
    cb=None,
) -> None:
    engine.register_wait(
        run_id=run_id,
        wait_for_event=event,
        tenant_id="t1",
        eu_id="eu-1",
        resume_callback=cb or _noop,
        correlation_id=corr,
    )


# ── register_wait ─────────────────────────────────────────────────────────────

def test_register_wait_stores_entry(engine: SchedulerEngine) -> None:
    _reg(engine, corr="c1")
    with engine._lock:
        entry = engine._waiting["run-1"]
    assert entry["wait_for"] == "flow.complete"
    assert entry["eu_id"] == "eu-1"
    assert entry["correlation_id"] == "c1"
    assert entry["tenant_id"] == "t1"


def test_duplicate_register_overwrites_previous_callback(engine: SchedulerEngine) -> None:
    cb1, cb2 = MagicMock(), MagicMock()
    _reg(engine, "run-dup", cb=cb1)
    _reg(engine, "run-dup", cb=cb2)
    with engine._lock:
        assert engine._waiting["run-dup"]["callback"] is cb2


# ── notify_event ─────────────────────────────────────────────────────────────

def test_notify_event_enqueues_resume_item(engine: SchedulerEngine) -> None:
    _reg(engine)
    engine.notify_event("flow.complete")
    item = engine.dequeue_next()
    assert item is not None
    assert item.run_callback is _noop


def test_notify_event_removes_entry_from_waiting(engine: SchedulerEngine) -> None:
    _reg(engine)
    engine.notify_event("flow.complete")
    with engine._lock:
        assert "run-1" not in engine._waiting


def test_notify_event_skips_on_event_type_mismatch(engine: SchedulerEngine) -> None:
    _reg(engine, event="flow.complete")
    engine.notify_event("flow.failed")
    with engine._lock:
        assert "run-1" in engine._waiting


def test_notify_event_skips_when_corr_ids_both_set_and_differ(engine: SchedulerEngine) -> None:
    _reg(engine, corr="X")
    count = engine.notify_event("flow.complete", correlation_id="Y")
    assert count == 0
    with engine._lock:
        assert "run-1" in engine._waiting


def test_notify_event_resumes_when_emit_carries_no_corr(engine: SchedulerEngine) -> None:
    """Entry with a corr_id should still resume when the emitted event has no corr_id."""
    _reg(engine, corr="X")
    count = engine.notify_event("flow.complete")
    assert count >= 1
    with engine._lock:
        assert "run-1" not in engine._waiting


def test_notify_event_resumes_when_entry_has_no_corr(engine: SchedulerEngine) -> None:
    """Entry with no corr_id should still resume when the emitted event carries a corr_id."""
    _reg(engine, corr=None)
    count = engine.notify_event("flow.complete", correlation_id="X")
    assert count >= 1
    with engine._lock:
        assert "run-1" not in engine._waiting


def test_notify_event_resumes_all_matching_waits(engine: SchedulerEngine) -> None:
    _reg(engine, "r1", "my.event")
    _reg(engine, "r2", "my.event")
    _reg(engine, "r3", "other.event")
    count = engine.notify_event("my.event")
    assert count >= 2
    with engine._lock:
        assert "r1" not in engine._waiting
        assert "r2" not in engine._waiting
        assert "r3" in engine._waiting


# ── peek_matching_run_ids ─────────────────────────────────────────────────────

def test_peek_returns_matching_run_id(engine: SchedulerEngine) -> None:
    _reg(engine, event="my.event")
    assert engine.peek_matching_run_ids("my.event") == ["run-1"]


def test_peek_does_not_consume_the_wait(engine: SchedulerEngine) -> None:
    _reg(engine, event="my.event")
    engine.peek_matching_run_ids("my.event")
    with engine._lock:
        assert "run-1" in engine._waiting


def test_peek_returns_empty_on_no_match(engine: SchedulerEngine) -> None:
    _reg(engine, event="my.event")
    assert engine.peek_matching_run_ids("other.event") == []


def test_peek_respects_correlation_id_filter(engine: SchedulerEngine) -> None:
    _reg(engine, corr="A")
    assert engine.peek_matching_run_ids("flow.complete", correlation_id="B") == []
    assert engine.peek_matching_run_ids("flow.complete", correlation_id="A") == ["run-1"]


# ── tick_time_waits ───────────────────────────────────────────────────────────

def test_tick_fires_past_due_time_wait(engine: SchedulerEngine) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    engine.register_wait(
        run_id="rt",
        wait_for_event="__time_wait__",
        tenant_id="t1",
        eu_id="eu-t",
        resume_callback=_noop,
        wait_condition={"type": "time", "trigger_at": past, "event_name": None},
    )
    fired = engine.tick_time_waits()
    assert fired >= 1
    with engine._lock:
        assert "rt" not in engine._waiting


def test_tick_does_not_fire_future_time_wait(engine: SchedulerEngine) -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    engine.register_wait(
        run_id="rf",
        wait_for_event="__time_wait__",
        tenant_id="t1",
        eu_id="eu-f",
        resume_callback=_noop,
        wait_condition={"type": "time", "trigger_at": future, "event_name": None},
    )
    fired = engine.tick_time_waits()
    assert fired == 0
    with engine._lock:
        assert "rf" in engine._waiting


# ── pre-rehydration buffering ─────────────────────────────────────────────────

def test_notify_before_rehydration_returns_zero(pre_engine: SchedulerEngine) -> None:
    assert not pre_engine.is_rehydrated()
    count = pre_engine.notify_event("any.event")
    assert count == 0


def test_notify_before_rehydration_buffers_event(pre_engine: SchedulerEngine) -> None:
    pre_engine.notify_event("buffered.event", correlation_id="c1")
    with pre_engine._lock:
        assert pre_engine._pre_rehydration_buffer == [("buffered.event", "c1")]


def test_pre_rehydration_buffer_overflow_drops_excess(pre_engine: SchedulerEngine) -> None:
    with pre_engine._lock:
        for i in range(_MAX_PRE_REHYDRATION_BUFFER):
            pre_engine._pre_rehydration_buffer.append((f"ev.{i}", None))
    count = pre_engine.notify_event("overflow.event")
    assert count == 0
    with pre_engine._lock:
        assert len(pre_engine._pre_rehydration_buffer) == _MAX_PRE_REHYDRATION_BUFFER


def test_mark_rehydration_complete_replays_buffered_events(pre_engine: SchedulerEngine) -> None:
    pre_engine.register_wait(
        run_id="buf-run",
        wait_for_event="flow.complete",
        tenant_id="t1",
        eu_id="eu-buf",
        resume_callback=_noop,
    )
    # Buffer the matching event before rehydration is complete
    pre_engine.notify_event("flow.complete")
    assert not pre_engine.is_rehydrated()
    with pre_engine._lock:
        assert any(ev == "flow.complete" for ev, _ in pre_engine._pre_rehydration_buffer)

    pre_engine.mark_rehydration_complete()

    assert pre_engine.is_rehydrated()
    with pre_engine._lock:
        assert pre_engine._pre_rehydration_buffer == []
        assert "buf-run" not in pre_engine._waiting
