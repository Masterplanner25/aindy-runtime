"""INFINITY-RUNTIME-1 Gap 3 — per-execution SCORE_COMPUTED record.

Verifies the scalar scorer, the emitter payload shape, and the agent
completion-path wiring. No database required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from AINDY.core.execution_score import compute_execution_score, emit_execution_score
from AINDY.core.system_event_types import SystemEventTypes

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def captured_events(monkeypatch):
    """Capture queue_system_event calls at the source module."""
    events: list[dict] = []

    def _fake(**kwargs):
        events.append(kwargs)
        return "evt-id"

    monkeypatch.setattr(
        "AINDY.core.execution_signal_helper.queue_system_event", _fake
    )
    return events


# --- compute_execution_score -------------------------------------------------


def test_score_failure_status_floors_to_zero():
    assert compute_execution_score(status="failed", result={"ok": True}) == 0.0
    assert compute_execution_score(status="cancelled", result=None) == 0.0
    assert compute_execution_score(status="verify_failed", result={"ok": True}) == 0.0


def test_score_completed_ok_uses_heuristic():
    assert compute_execution_score(status="completed", result={"ok": True}) == 0.9


def test_score_completed_ambiguous_result_holds_success_floor():
    # evaluate_result(None) == 0.1, but a completed run must not read as failure.
    assert compute_execution_score(status="completed", result=None) == 0.6


def test_score_explicit_success_score_passthrough():
    assert compute_execution_score(status="completed", result={"success_score": 0.72}) == 0.72


def test_score_partial_status_non_terminal_uses_raw_heuristic():
    assert compute_execution_score(status="running", result={"status": "partial"}) == 0.5


# --- emit_execution_score ----------------------------------------------------


def test_emit_score_payload_shape(captured_events):
    emit_execution_score(
        db=None,
        run_id="run-123",
        score=0.9123456,
        status="completed",
        trace_id="trace-9",
        user_id="user-1",
        duration_ms=1234.7,
        dimensions={"steps_completed": 3, "steps_total": 3},
        source="agent",
    )
    assert len(captured_events) == 1
    call = captured_events[0]
    assert call["event_type"] == SystemEventTypes.SCORE_COMPUTED
    assert call["event_type"] == "score.computed"
    assert call["source"] == "agent"
    assert call["trace_id"] == "trace-9"
    assert call["user_id"] == "user-1"
    payload = call["payload"]
    assert payload["run_id"] == "run-123"
    assert payload["score"] == 0.9123  # rounded to 4 dp
    assert payload["status"] == "completed"
    assert payload["duration_ms"] == 1234  # int-coerced
    assert payload["dimensions"] == {"steps_completed": 3, "steps_total": 3}


def test_emit_score_omits_duration_when_absent(captured_events):
    emit_execution_score(db=None, run_id="r", score=0.5, trace_id="t")
    assert "duration_ms" not in captured_events[0]["payload"]


def test_emit_score_is_best_effort(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("event bus down")

    monkeypatch.setattr(
        "AINDY.core.execution_signal_helper.queue_system_event", _boom
    )
    # Must not raise into the caller's completion path.
    assert emit_execution_score(db=None, run_id="r", score=0.5) is None


# --- agent completion-path wiring -------------------------------------------


def _fake_run(status, **overrides):
    started = datetime.now(timezone.utc)
    defaults = dict(
        id="run-abc",
        status=status,
        result={"ok": status == "completed"},
        trace_id="trace-abc",
        started_at=started,
        completed_at=started + timedelta(milliseconds=500),
        steps_completed=2,
        steps_total=2,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_agent_run_score_emitted_for_terminal_states(captured_events, status):
    from AINDY.agents.agent_runtime.execution import _emit_agent_run_score

    _emit_agent_run_score(_fake_run(status), db=None, user_id="user-1")
    assert len(captured_events) == 1
    payload = captured_events[0]["payload"]
    assert payload["run_id"] == "run-abc"
    assert payload["status"] == status
    assert payload["duration_ms"] == 500
    if status == "failed":
        assert payload["score"] == 0.0
    else:
        assert payload["score"] == 0.9


@pytest.mark.parametrize("status", ["executing", "waiting", "approved", "delegated"])
def test_agent_run_score_skipped_for_non_terminal_states(captured_events, status):
    from AINDY.agents.agent_runtime.execution import _emit_agent_run_score

    _emit_agent_run_score(_fake_run(status), db=None, user_id="user-1")
    assert captured_events == []


def test_agent_run_score_no_duration_without_timestamps(captured_events):
    from AINDY.agents.agent_runtime.execution import _emit_agent_run_score

    run = _fake_run("completed", started_at=None, completed_at=None)
    _emit_agent_run_score(run, db=None, user_id="user-1")
    assert "duration_ms" not in captured_events[0]["payload"]


def test_new_event_constants_registered():
    assert SystemEventTypes.RECALL_USED == "recall.used"
    assert SystemEventTypes.SCORE_COMPUTED == "score.computed"
    assert SystemEventTypes.NEXT_ACTION_CHOSEN == "next_action.chosen"
