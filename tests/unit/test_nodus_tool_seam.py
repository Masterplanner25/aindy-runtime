"""Unit tests for the RTR-1 Phase 2a Nodus tool-calling seam.

Covers `run_agent_tool` (the capability-enforced bridge from the Nodus VM to
AINDY's execute_tool) and the threading of run_id + execution_token from
NodusExecutionContext into the worker subprocess payload. Both run in-process
(no subprocess) so they pass on every platform.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from AINDY.runtime import nodus_worker as worker
from AINDY.runtime import nodus_runtime_adapter as adapter_mod
from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext, NodusRuntimeAdapter

pytestmark = pytest.mark.runtime_only


# --------------------------------------------------------------------------- #
# run_agent_tool — capability-enforced tool bridge
# --------------------------------------------------------------------------- #

def test_fail_closed_without_token(monkeypatch):
    """No capability token → refused before reaching execute_tool."""
    called = {"n": 0}

    def _never(**kwargs):
        called["n"] += 1
        return {"success": True, "result": None, "error": None}

    monkeypatch.setattr("AINDY.agents.tool_registry.execute_tool", _never)
    r = worker.run_agent_tool(
        "send_email", {"to": "x"}, user_id="u1", run_id="r1", execution_token=None
    )
    assert r == {
        "success": False,
        "result": None,
        "error": "tool execution requires a capability token",
    }
    assert called["n"] == 0  # execute_tool never invoked


def test_with_token_invokes_execute_tool(monkeypatch):
    captured = {}

    def _exec(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": {"sent": True}, "error": None}

    monkeypatch.setattr("AINDY.agents.tool_registry.execute_tool", _exec)
    db = MagicMock()
    r = worker.run_agent_tool(
        "send_email", {"to": "x"},
        user_id="u1", run_id="r1", execution_token={"tok": 1},
        session_factory=lambda: db,
    )
    assert r == {"success": True, "result": {"sent": True}, "error": None}
    # Token + run_id + user threaded through to execute_tool.
    assert captured["tool_name"] == "send_email"
    assert captured["args"] == {"to": "x"}
    assert captured["user_id"] == "u1"
    assert captured["run_id"] == "r1"
    assert captured["execution_token"] == {"tok": 1}
    assert db.close.called


def test_non_dict_args_coerced(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "AINDY.agents.tool_registry.execute_tool",
        lambda **kw: captured.update(kw) or {"success": True, "result": None, "error": None},
    )
    worker.run_agent_tool(
        "t", "not-a-dict", user_id="u", run_id="r", execution_token={"tok": 1},
        session_factory=lambda: MagicMock(),
    )
    assert captured["args"] == {}


def test_execute_tool_exception_is_caught(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("tool blew up")

    monkeypatch.setattr("AINDY.agents.tool_registry.execute_tool", _boom)
    db = MagicMock()
    r = worker.run_agent_tool(
        "t", {}, user_id="u", run_id="r", execution_token={"tok": 1},
        session_factory=lambda: db,
    )
    assert r["success"] is False
    assert "tool blew up" in r["error"]
    assert db.close.called  # session closed even on failure


def test_result_is_json_safe(monkeypatch):
    class Weird:
        def __str__(self):
            return "weird-value"

    monkeypatch.setattr(
        "AINDY.agents.tool_registry.execute_tool",
        lambda **kw: {"success": True, "result": {"obj": Weird()}, "error": None},
    )
    r = worker.run_agent_tool(
        "t", {}, user_id="u", run_id="r", execution_token={"tok": 1},
        session_factory=lambda: MagicMock(),
    )
    # Non-serialisable values are stringified so the Nodus script gets clean data.
    assert r["result"] == {"obj": "weird-value"}
    json.dumps(r)  # must be JSON-serialisable


# --------------------------------------------------------------------------- #
# Threading: NodusExecutionContext → worker payload context
# --------------------------------------------------------------------------- #

def test_execute_threads_run_id_and_token_into_worker_payload(monkeypatch):
    # ★ Pins the FRESH-SUBPROCESS path. `AINDY_NODUS_WARM_POOL` defaults ON since
    # 2026-08-19, so without this the adapter takes the warm path, `subprocess.run` is
    # never called, and the capture below reads an empty dict. This test is about that
    # path specifically; the payload itself is built once and shared by both.
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "0")
    captured = {}

    class _Proc:
        returncode = 0
        stdout = json.dumps(
            {"status": "success", "output_state": {}, "emitted_events": [], "memory_writes": []}
        )
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["payload"] = json.loads(kwargs["input"])
        return _Proc()

    monkeypatch.setattr(adapter_mod.subprocess, "run", _fake_run)

    adapter = NodusRuntimeAdapter(db=MagicMock())
    ctx = NodusExecutionContext(
        user_id="u1",
        execution_unit_id="eu1",
        run_id="agent-run-9",
        execution_token={"token_hash": "abc", "granted_tools": ["t"]},
    )
    adapter.run_script("let x = 1", ctx)

    worker_ctx = captured["payload"]["context"]
    assert worker_ctx["run_id"] == "agent-run-9"
    assert worker_ctx["execution_token"] == {"token_hash": "abc", "granted_tools": ["t"]}


def test_execute_defaults_run_id_to_eu_and_token_none(monkeypatch):
    # ★ Pins the FRESH-SUBPROCESS path. `AINDY_NODUS_WARM_POOL` defaults ON since
    # 2026-08-19, so without this the adapter takes the warm path, `subprocess.run` is
    # never called, and the capture below reads an empty dict. This test is about that
    # path specifically; the payload itself is built once and shared by both.
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "0")
    captured = {}

    class _Proc:
        returncode = 0
        stdout = json.dumps(
            {"status": "success", "output_state": {}, "emitted_events": [], "memory_writes": []}
        )
        stderr = ""

    monkeypatch.setattr(
        adapter_mod.subprocess, "run",
        lambda cmd, **kw: captured.update(payload=json.loads(kw["input"])) or _Proc(),
    )
    adapter = NodusRuntimeAdapter(db=MagicMock())
    ctx = NodusExecutionContext(user_id="u1", execution_unit_id="eu1")
    adapter.run_script("let x = 1", ctx)

    worker_ctx = captured["payload"]["context"]
    assert worker_ctx["run_id"] == "eu1"          # falls back to execution_unit_id
    assert worker_ctx["execution_token"] is None  # no token → fail-closed downstream
