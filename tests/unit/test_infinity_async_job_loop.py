"""INFINITY-RUNTIME-1 Gap 5 — async jobs join the loop (flag-gated).

Covers the loop-closure flag gate and the per-job SCORE_COMPUTED emitter. The
full context-activation → auto-capture path is exercised by integration tests
(needs a live DB); here we unit-test the flag gate and score emission. No DB.
"""

from __future__ import annotations

import pytest

from AINDY.config import settings
from AINDY.core.system_event_types import SystemEventTypes
from AINDY.platform_layer import async_job_service as ajs


@pytest.fixture
def captured_events(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        "AINDY.core.execution_signal_helper.queue_system_event",
        lambda **kw: events.append(kw) or "evt-id",
    )
    return events


class _FakeQuery:
    def __init__(self, log):
        self._log = log

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._log


class _FakeDB:
    def __init__(self, log):
        self._log = log

    def query(self, _model):
        return _FakeQuery(self._log)


class _FakeLog:
    def __init__(self, user_id="user-1"):
        self.user_id = user_id


# --- flag gate ---------------------------------------------------------------


def test_loop_closure_flag_reflects_setting(monkeypatch):
    monkeypatch.setattr(settings, "AINDY_ASYNC_JOB_LOOP_CLOSURE", False)
    assert ajs._async_job_loop_closure_enabled() is False
    monkeypatch.setattr(settings, "AINDY_ASYNC_JOB_LOOP_CLOSURE", True)
    assert ajs._async_job_loop_closure_enabled() is True


def test_score_noop_when_flag_off(monkeypatch, captured_events):
    monkeypatch.setattr(settings, "AINDY_ASYNC_JOB_LOOP_CLOSURE", False)
    ajs._emit_async_job_score(
        db=_FakeDB(_FakeLog()),
        log_id="job-1",
        task_name="embed",
        status="success",
        result={"ok": True},
        duration_ms=100,
    )
    assert captured_events == []


# --- score emission when enabled --------------------------------------------


def test_score_emitted_on_success(monkeypatch, captured_events):
    monkeypatch.setattr(settings, "AINDY_ASYNC_JOB_LOOP_CLOSURE", True)
    ajs._emit_async_job_score(
        db=_FakeDB(_FakeLog("user-9")),
        log_id="job-42",
        task_name="ingest",
        status="success",
        result={"ok": True},
        duration_ms=250,
    )
    assert len(captured_events) == 1
    call = captured_events[0]
    assert call["event_type"] == SystemEventTypes.SCORE_COMPUTED
    assert call["source"] == "async_job"
    assert call["trace_id"] == "job-42"
    assert call["user_id"] == "user-9"
    payload = call["payload"]
    assert payload["run_id"] == "job-42"
    assert payload["score"] == 0.9
    assert payload["status"] == "success"
    assert payload["duration_ms"] == 250
    assert payload["dimensions"] == {"task_name": "ingest", "execution_mode": "async_job"}


def test_score_zero_on_failure(monkeypatch, captured_events):
    monkeypatch.setattr(settings, "AINDY_ASYNC_JOB_LOOP_CLOSURE", True)
    ajs._emit_async_job_score(
        db=_FakeDB(_FakeLog()),
        log_id="job-x",
        task_name="ingest",
        status="failed",
        result={"error": "boom"},
        duration_ms=10,
    )
    assert captured_events[0]["payload"]["score"] == 0.0
    assert captured_events[0]["payload"]["status"] == "failed"


def test_score_is_best_effort(monkeypatch, captured_events):
    monkeypatch.setattr(settings, "AINDY_ASYNC_JOB_LOOP_CLOSURE", True)

    class _BoomDB:
        def query(self, _m):
            raise RuntimeError("db gone")

    # Must not raise even if the JobLog lookup explodes.
    ajs._emit_async_job_score(
        db=_BoomDB(),
        log_id="job-err",
        task_name="ingest",
        status="success",
        result={"ok": True},
        duration_ms=None,
    )
    assert captured_events == []


# --- mechanism: activation unblocks EXECUTION_* persistence ------------------


def test_async_context_lets_execution_events_past_contract_gate(monkeypatch):
    """The crux of Gap 5's memory half: activating the async context (what the
    flag does in _execute_job_inline) lets EXECUTION_COMPLETED past the
    execution-contract gate that otherwise raises and is swallowed."""
    import uuid as _uuid

    from AINDY.core import system_event_service as ses
    from AINDY.platform_layer.async_execution_context import (
        activate_async_execution_context,
        deactivate_async_execution_context,
    )

    monkeypatch.setattr(settings, "ENFORCE_EXECUTION_CONTRACT", True)
    monkeypatch.setattr(ses, "_persist_system_event", lambda **kw: _uuid.uuid4())

    class _NoneQuery:
        def filter(self, *a, **k):
            return self

        def first(self):
            return None

    class _DB:
        def query(self, *a, **k):
            return _NoneQuery()

    db = _DB()

    # Outside pipeline/async context, the execution.* event is a contract violation.
    with pytest.raises(RuntimeError):
        ses.emit_system_event(
            db=db, event_type=SystemEventTypes.EXECUTION_COMPLETED, required=True
        )

    # With the async context active (as the Gap-5 flag arranges), it persists.
    token = activate_async_execution_context()
    try:
        assert (
            ses.emit_system_event(
                db=db, event_type=SystemEventTypes.EXECUTION_COMPLETED, required=True
            )
            is not None
        )
    finally:
        deactivate_async_execution_context(token)
