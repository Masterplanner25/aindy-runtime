"""AGENT-HARDEN-4 — shadow call_tool seam (effect simulation, PR1).

Pins the core invariant: in simulate mode a tool call produces a predicted result
and a "would-write" intent with ZERO real execution, and the simulate flag +
collected effects thread through the subprocess adapter.
"""
from __future__ import annotations

import json

import pytest

from AINDY.runtime.tool_simulation import simulate_agent_tool

pytestmark = pytest.mark.runtime_only


class _FakeSession:
    def close(self):
        pass


def _sf():
    return _FakeSession()


# --------------------------------------------------------------------------- #
# simulate_agent_tool — zero-execution shadow
# --------------------------------------------------------------------------- #

def test_no_token_fails_closed():
    out = simulate_agent_tool("send_email", {"to": "x"}, user_id="u", run_id="r", execution_token=None)
    assert out["call_result"]["success"] is False
    assert out["would_write"]["executed"] is False
    assert out["would_write"]["capability_ok"] is False


def test_capability_ok_predicts_without_executing(monkeypatch):
    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability", lambda **kw: {"ok": True}
    )
    # Hard proof of zero side effects: the real tool executor must never be called.
    monkeypatch.setattr(
        "AINDY.agents.tool_registry.execute_tool",
        lambda **kw: pytest.fail("simulate must not execute the real tool"),
    )

    out = simulate_agent_tool(
        "send_email", {"to": "x"}, user_id="u", run_id="r",
        execution_token={"token_hash": "h"}, session_factory=_sf,
    )
    assert out["call_result"]["success"] is True
    assert out["call_result"]["result"]["simulated"] is True
    ww = out["would_write"]
    assert ww["executed"] is False
    assert ww["capability_ok"] is True
    assert ww["tool"] == "send_email" and ww["args"] == {"to": "x"}
    assert ww["predicted_result"] is not None


def test_capability_denied_reports_and_blocks(monkeypatch):
    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability",
        lambda **kw: {"ok": False, "error": "tool 'send_email' not granted by capability token"},
    )
    out = simulate_agent_tool(
        "send_email", {}, user_id="u", run_id="r",
        execution_token={"token_hash": "h"}, session_factory=_sf,
    )
    assert out["call_result"]["success"] is False
    assert "not granted" in out["call_result"]["error"]
    assert out["would_write"]["capability_ok"] is False
    assert out["would_write"]["predicted_result"] is None
    assert out["would_write"]["executed"] is False


def test_capability_check_error_fails_closed(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("registry down")

    monkeypatch.setattr("AINDY.agents.capability_service.check_tool_capability", _boom)
    out = simulate_agent_tool(
        "send_email", {}, user_id="u", run_id="r",
        execution_token={"token_hash": "h"}, session_factory=_sf,
    )
    assert out["would_write"]["capability_ok"] is False
    assert out["call_result"]["success"] is False


# --------------------------------------------------------------------------- #
# Adapter threading — simulate flag out, simulated_effects back
# --------------------------------------------------------------------------- #

def test_adapter_threads_simulate_and_parses_effects(monkeypatch):
    from AINDY.runtime import nodus_runtime_adapter as adp

    captured = {}

    class _Proc:
        returncode = 0
        stdout = json.dumps({
            "status": "success", "output_state": {}, "emitted_events": [],
            "memory_writes": [],
            "simulated_effects": [{"tool": "send_email", "executed": False, "capability_ok": True}],
        })
        stderr = ""

    def _fake_run(cmd, input=None, **kw):
        captured["input"] = input
        return _Proc()

    monkeypatch.setattr(adp.subprocess, "run", _fake_run)

    ctx = adp.NodusExecutionContext(user_id="u", execution_unit_id="eu", simulate=True)
    result = adp.NodusRuntimeAdapter(db=object()).run_script("let x = 1", ctx)

    payload = json.loads(captured["input"])
    assert payload["context"]["simulate"] is True  # flag threaded to the worker
    assert result.simulated_effects == [
        {"tool": "send_email", "executed": False, "capability_ok": True}
    ]


def test_adapter_default_not_simulate(monkeypatch):
    from AINDY.runtime import nodus_runtime_adapter as adp

    captured = {}

    class _Proc:
        returncode = 0
        stdout = json.dumps({"status": "success", "output_state": {}, "emitted_events": [], "memory_writes": []})
        stderr = ""

    monkeypatch.setattr(adp.subprocess, "run", lambda cmd, input=None, **kw: captured.update(input=input) or _Proc())

    ctx = adp.NodusExecutionContext(user_id="u", execution_unit_id="eu")
    result = adp.NodusRuntimeAdapter(db=object()).run_script("let x = 1", ctx)

    assert json.loads(captured["input"])["context"]["simulate"] is False
    assert result.simulated_effects == []
