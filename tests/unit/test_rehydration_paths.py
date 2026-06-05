"""
INV-SCHED-002 / INV-EVENT-002 regression tests: wait rehydration lifecycle.

Covers:
- Empty DB returns 0 (nothing to rehydrate)
- EU already registered in the scheduler is skipped (duplicate guard)
- EU with no wait_condition is skipped (cannot determine what to wait for)
- Event-type EU with no event_name is skipped
- Valid event-type EU is registered and returns count 1
- Rehydrated EU resumes correctly when its event fires
- Calling rehydrate_waiting_eus twice is idempotent (duplicate guard on second call)
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from AINDY.kernel.scheduler.engine import SchedulerEngine

pytestmark = pytest.mark.runtime_only


def _make_eu(
    eu_id: str,
    *,
    wait_condition: dict | None = None,
    tenant_id: str | None = "t1",
    user_id: str | None = None,
    priority: str = "normal",
    correlation_id: str | None = None,
    eu_type: str = "flow",
    flow_run_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=eu_id,
        status="waiting",
        wait_condition=wait_condition,
        tenant_id=tenant_id,
        user_id=user_id,
        priority=priority,
        correlation_id=correlation_id,
        type=eu_type,
        flow_run_id=flow_run_id,
    )


def _make_db(waiting_eus: list) -> MagicMock:
    """Mock DB session returning waiting_eus for ExecutionUnit queries."""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = waiting_eus
    return mock_db


# ── empty DB ──────────────────────────────────────────────────────────────────

def test_no_waiting_eus_returns_zero() -> None:
    from AINDY.core.wait_rehydration import rehydrate_waiting_eus

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    with patch("AINDY.core.wait_rehydration.get_scheduler_engine", return_value=eng):
        result = rehydrate_waiting_eus(_make_db([]))
    assert result == 0


# ── duplicate guard ───────────────────────────────────────────────────────────

def test_eu_already_in_scheduler_is_skipped() -> None:
    from AINDY.core.wait_rehydration import rehydrate_waiting_eus

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    eng.register_wait(
        run_id="eu-dup",
        wait_for_event="x.done",
        tenant_id="t1",
        eu_id="eu-dup",
        resume_callback=lambda: None,
    )
    eu = _make_eu("eu-dup", wait_condition={"type": "event", "event_name": "x.done"})
    with patch("AINDY.core.wait_rehydration.get_scheduler_engine", return_value=eng):
        result = rehydrate_waiting_eus(_make_db([eu]))
    assert result == 0


def test_double_rehydration_is_idempotent() -> None:
    from AINDY.core.wait_rehydration import rehydrate_waiting_eus

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    eu = _make_eu("eu-idem", wait_condition={"type": "event", "event_name": "my.event"})
    mock_db = _make_db([eu])
    with patch("AINDY.core.wait_rehydration.get_scheduler_engine", return_value=eng):
        first = rehydrate_waiting_eus(mock_db)
        second = rehydrate_waiting_eus(mock_db)
    assert first == 1
    assert second == 0  # duplicate guard skips the already-registered EU


# ── skip conditions ───────────────────────────────────────────────────────────

def test_eu_with_no_wait_condition_is_skipped() -> None:
    from AINDY.core.wait_rehydration import rehydrate_waiting_eus

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    eu = _make_eu("eu-nowc", wait_condition=None)
    with patch("AINDY.core.wait_rehydration.get_scheduler_engine", return_value=eng):
        result = rehydrate_waiting_eus(_make_db([eu]))
    assert result == 0
    assert eng.waiting_for("eu-nowc") is None


def test_event_eu_without_event_name_is_skipped() -> None:
    from AINDY.core.wait_rehydration import rehydrate_waiting_eus

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    eu = _make_eu("eu-noevent", wait_condition={"type": "event", "event_name": None})
    with patch("AINDY.core.wait_rehydration.get_scheduler_engine", return_value=eng):
        result = rehydrate_waiting_eus(_make_db([eu]))
    assert result == 0
    assert eng.waiting_for("eu-noevent") is None


# ── successful rehydration ────────────────────────────────────────────────────

def test_valid_event_eu_is_registered() -> None:
    from AINDY.core.wait_rehydration import rehydrate_waiting_eus

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    eu = _make_eu("eu-ok", wait_condition={"type": "event", "event_name": "flow.ready"})
    with patch("AINDY.core.wait_rehydration.get_scheduler_engine", return_value=eng):
        result = rehydrate_waiting_eus(_make_db([eu]))
    assert result == 1
    assert eng.waiting_for("eu-ok") == "flow.ready"


def test_rehydrated_eu_resumes_on_matching_event() -> None:
    from AINDY.core.wait_rehydration import rehydrate_waiting_eus

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    eu = _make_eu("eu-notify", wait_condition={"type": "event", "event_name": "flow.ready"})
    with patch("AINDY.core.wait_rehydration.get_scheduler_engine", return_value=eng):
        rehydrate_waiting_eus(_make_db([eu]))

    count = eng.notify_event("flow.ready")
    assert count >= 1
    with eng._lock:
        assert "eu-notify" not in eng._waiting


def test_multiple_waiting_eus_all_rehydrated() -> None:
    from AINDY.core.wait_rehydration import rehydrate_waiting_eus

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    eus = [
        _make_eu("eu-a", wait_condition={"type": "event", "event_name": "evt.a"}),
        _make_eu("eu-b", wait_condition={"type": "event", "event_name": "evt.b"}),
        _make_eu("eu-c", wait_condition={"type": "event", "event_name": "evt.a"}),
    ]
    with patch("AINDY.core.wait_rehydration.get_scheduler_engine", return_value=eng):
        result = rehydrate_waiting_eus(_make_db(eus))
    assert result == 3
    assert eng.waiting_for("eu-a") == "evt.a"
    assert eng.waiting_for("eu-b") == "evt.b"
    assert eng.waiting_for("eu-c") == "evt.a"
