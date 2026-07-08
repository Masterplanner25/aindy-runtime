"""RTR-5 — runtime-driven autonomous execute-window.

`run_execute_window` composes evaluate_live_trigger → create_run → execute_run in
a bounded loop, opt-in behind AINDY_AUTONOMOUS_EXECUTE_WINDOW (default off). Policy
stays app-owned (the evaluator); the runtime owns the bounded window + guardrails.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from AINDY.agents import autonomous_window

pytestmark = pytest.mark.runtime_only


@contextmanager
def _window(monkeypatch, *, enabled=True, max_iter=3, max_active=1, cooldown=0,
            decision="execute", create_status="approved", exec_status="completed",
            active=0):
    monkeypatch.setattr(
        autonomous_window, "_window_settings",
        lambda: (enabled, max_iter, max_active, cooldown),
    )
    create_ret = {"run_id": "run-1", "status": create_status}
    with (
        patch("AINDY.agents.autonomous_controller.count_active_executions", return_value=active) as cae,
        patch("AINDY.agents.autonomous_controller.evaluate_live_trigger",
              return_value={"decision": decision, "reason": "r", "priority": 0.9}) as ev,
        patch("AINDY.agents.autonomous_controller.record_decision", return_value={}) as rd,
        patch("AINDY.agents.agent_runtime.create_run", return_value=create_ret) as cr,
        patch("AINDY.agents.agent_runtime.execute_run", return_value={"status": exec_status}) as ex,
        patch("AINDY.core.execution_signal_helper.queue_system_event", return_value="evt"),
    ):
        yield {"count_active": cae, "evaluate": ev, "record": rd, "create": cr, "execute": ex}


def test_disabled_is_noop(monkeypatch):
    with _window(monkeypatch, enabled=False) as m:
        out = autonomous_window.run_execute_window(MagicMock(), user_id="u", objective="do it")
    assert out["enabled"] is False
    m["create"].assert_not_called()
    m["evaluate"].assert_not_called()


def test_execute_runs_bounded_iterations(monkeypatch):
    with _window(monkeypatch, max_iter=2) as m:
        out = autonomous_window.run_execute_window(MagicMock(), user_id="u", objective="do it")
    assert out["enabled"] is True
    assert out["count"] == 2
    assert out["stop_reason"] == "max_iterations"
    assert m["execute"].call_count == 2
    assert all(it["status"] == "completed" for it in out["iterations"])


def test_defer_decision_ends_window_without_creating(monkeypatch):
    with _window(monkeypatch, decision="defer") as m:
        out = autonomous_window.run_execute_window(MagicMock(), user_id="u", objective="do it")
    assert out["stop_reason"] == "defer"
    assert out["count"] == 1
    m["create"].assert_not_called()
    m["execute"].assert_not_called()


def test_pending_approval_stops_window(monkeypatch):
    with _window(monkeypatch, create_status="pending_approval") as m:
        out = autonomous_window.run_execute_window(MagicMock(), user_id="u", objective="do it")
    assert out["stop_reason"] == "approval_required"
    m["execute"].assert_not_called()  # window respects human approval, never force-executes


def test_active_run_cap_blocks_start(monkeypatch):
    with _window(monkeypatch, active=5, max_active=1) as m:
        out = autonomous_window.run_execute_window(MagicMock(), user_id="u", objective="do it")
    assert out["stop_reason"] == "active_run_cap"
    assert out["count"] == 0
    m["evaluate"].assert_not_called()  # never even evaluates when saturated


def test_run_failure_stops_window(monkeypatch):
    with _window(monkeypatch, max_iter=3, exec_status="failed") as m:
        out = autonomous_window.run_execute_window(MagicMock(), user_id="u", objective="do it")
    assert out["stop_reason"] == "run_failed"
    assert out["count"] == 1
    assert m["execute"].call_count == 1


def test_no_objective_is_noop_when_enabled(monkeypatch):
    with _window(monkeypatch) as m:
        out = autonomous_window.run_execute_window(MagicMock(), user_id="u", objective="")
    assert out["stop_reason"] == "no_objective"
    m["create"].assert_not_called()


def test_job_handler_registered_and_delegates():
    from AINDY.platform_layer.async_job_service import _JOB_REGISTRY

    assert autonomous_window.AUTONOMOUS_WINDOW_JOB_NAME in _JOB_REGISTRY
    with patch.object(autonomous_window, "run_execute_window", return_value={"ok": True}) as rw:
        out = autonomous_window._autonomous_window_job(
            {"user_id": "u", "objective": "obj", "trigger": {"trigger_type": "t"}}, MagicMock()
        )
    assert out == {"ok": True}
    assert rw.call_args.kwargs["objective"] == "obj"
    assert rw.call_args.kwargs["user_id"] == "u"
