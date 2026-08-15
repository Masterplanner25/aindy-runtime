"""INFINITY-RUNTIME-1 Gap 4 — post-execution Next-Action engine (record-first).

Covers the NextAction contract, hook coercion, the runtime-default decision, the
NEXT_ACTION_CHOSEN emitter, and the agent completion-path wiring. No database.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from AINDY.core import next_action as na
from AINDY.core.system_event_types import SystemEventTypes

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def captured_events(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        "AINDY.core.execution_signal_helper.queue_system_event",
        lambda **kw: events.append(kw) or "evt-id",
    )
    return events


# --- contract + coercion -----------------------------------------------------


def test_make_next_action_rejects_unknown_verb():
    assert na.make_next_action("frobnicate") is None
    assert na.make_next_action("done")["action"] == "done"


def test_coerce_from_string():
    assert na.coerce_next_action("RETRY")["action"] == "retry"
    assert na.coerce_next_action("nope") is None
    assert na.coerce_next_action(None) is None
    assert na.coerce_next_action("") is None


def test_coerce_from_dict():
    out = na.coerce_next_action({"action": "escalate", "reason": "x", "confidence": 0.5})
    assert out["action"] == "escalate"
    assert out["reason"] == "x"
    assert out["confidence"] == 0.5
    assert out["source"] == "completion_hook"


def test_coerce_from_object():
    obj = SimpleNamespace(action="ask_user", reason="need input")
    out = na.coerce_next_action(obj)
    assert out["action"] == "ask_user" and out["reason"] == "need input"


def test_coerce_invalid_dict_returns_none():
    assert na.coerce_next_action({"action": "bogus"}) is None
    assert na.coerce_next_action({"no_action_key": 1}) is None


def test_select_hook_next_action_picks_first_valid():
    results = [None, "not-a-verb", {"action": "retry"}, "escalate"]
    assert na.select_hook_next_action(results)["action"] == "retry"
    assert na.select_hook_next_action([None, "junk"]) is None
    assert na.select_hook_next_action([]) is None


# --- runtime-default decision ------------------------------------------------


def test_default_completed_is_done():
    assert na.default_next_action(status="completed")["action"] == "done"


def test_default_failed_escalates_without_retries():
    assert na.default_next_action(status="failed", attempts_remaining=False)["action"] == "escalate"


def test_default_failed_retries_when_attempts_remain():
    assert na.default_next_action(status="failed", attempts_remaining=True)["action"] == "retry"


def test_default_non_terminal_recommends():
    assert na.default_next_action(status="executing")["action"] == "recommend"


# --- emitter -----------------------------------------------------------------


def test_emit_payload_shape(captured_events):
    action = na.make_next_action("retry", reason="transient", args={"delay": 5}, confidence=0.8)
    na.emit_next_action_chosen(
        db=None, run_id="run-1", next_action=action, status="failed",
        trace_id="t-1", user_id="u-1",
    )
    call = captured_events[0]
    assert call["event_type"] == SystemEventTypes.NEXT_ACTION_CHOSEN == "next_action.chosen"
    payload = call["payload"]
    assert payload["run_id"] == "run-1"
    assert payload["action"] == "retry"
    assert payload["status"] == "failed"
    assert payload["reason"] == "transient"
    assert payload["args"] == {"delay": 5}
    assert payload["decision_source"] == "runtime_default"
    assert payload["confidence"] == 0.8


def test_emit_noop_on_none(captured_events):
    assert na.emit_next_action_chosen(db=None, run_id="r", next_action=None) is None
    assert captured_events == []


def test_emit_is_best_effort(monkeypatch):
    monkeypatch.setattr(
        "AINDY.core.execution_signal_helper.queue_system_event",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("down")),
    )
    action = na.make_next_action("done")
    assert na.emit_next_action_chosen(db=None, run_id="r", next_action=action) is None


# --- agent completion-path wiring -------------------------------------------


def _fake_run(status):
    return SimpleNamespace(
        id="run-abc", status=status, result={"ok": status == "completed"}, trace_id="trace-abc"
    )


def test_agent_next_action_completed_defaults_done(captured_events):
    from AINDY.agents.agent_runtime.execution import _emit_agent_next_action

    _emit_agent_next_action(_fake_run("completed"), db=None, user_id="u-1")
    assert captured_events[0]["payload"]["action"] == "done"
    assert captured_events[0]["payload"]["decision_source"] == "runtime_default"


def test_agent_next_action_failed_escalates(captured_events):
    from AINDY.agents.agent_runtime.execution import _emit_agent_next_action

    _emit_agent_next_action(_fake_run("failed"), db=None, user_id="u-1")
    assert captured_events[0]["payload"]["action"] == "escalate"


def test_agent_next_action_prefers_hook(captured_events):
    from AINDY.agents.agent_runtime.execution import _emit_agent_next_action

    hook = na.make_next_action("schedule_follow_up", reason="from hook", source="completion_hook")
    _emit_agent_next_action(_fake_run("completed"), db=None, user_id="u-1", hook_action=hook)
    payload = captured_events[0]["payload"]
    assert payload["action"] == "schedule_follow_up"
    assert payload["decision_source"] == "completion_hook"


@pytest.mark.parametrize("status", ["executing", "waiting", "approved", "delegated"])
def test_agent_next_action_skipped_for_non_terminal(captured_events, status):
    from AINDY.agents.agent_runtime.execution import _emit_agent_next_action

    _emit_agent_next_action(_fake_run(status), db=None, user_id="u-1")
    assert captured_events == []


def test_select_completion_hook_wrapper():
    from AINDY.agents.agent_runtime.execution import _select_completion_hook_next_action

    assert _select_completion_hook_next_action([{"action": "retry"}])["action"] == "retry"
    assert _select_completion_hook_next_action([None, "junk"]) is None
    assert _select_completion_hook_next_action(None) is None
