"""IDEM-14 / DEC-076 — the tool seam's idempotency key is per STEP, not per run.

`execute_tool` keyed an effect on `(tool, args, run_id)`. Two steps of one plan that call the same
`EXACTLY_ONCE` tool with identical args therefore shared an `action_id`: the second step REPLAYED
the first step's result and its own effect never happened. A deliberate second reminder, a
second identical message after a WAIT: silently absorbed, reported as `success`.

The owner's decision: idempotent per tool call / individual step. A retry of ONE step (same run,
same step, same args) is still one key and still replays; that is the at-most-once the gate is
for. Callers without a step (syscalls, MCP, extensions, direct calls) keep the run scope.

These use the REAL `compute_action_id` (the `test_tool_idempotency.py` suite patches it to a
constant, so it cannot see a key change). The ledger is a dict keyed on the `action_id`: the
ledger's own dedup is covered elsewhere; the question here is only which calls share a key.
"""
from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from AINDY.agents import tool_registry as tr

pytestmark = pytest.mark.runtime_only

RUN = "00000000-0000-0000-0000-00000000aaaa"


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(tr.TOOL_REGISTRY)
    yield
    tr.TOOL_REGISTRY.clear()
    tr.TOOL_REGISTRY.update(saved)


@pytest.fixture
def ledger():
    """A dict-backed effect ledger: resolve says "already done" iff the action_id completed."""
    done: dict = {}
    keys: list = []

    def _resolve(db, action_id, tool_name, args, **kw):
        keys.append(action_id)
        return (action_id in done, done.get(action_id))

    def _finalize(db, action_id, status, result, tool_name):
        if status == "success":
            done[action_id] = {"result": result}

    with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []},
    ), patch.object(tr, "queue_system_event", lambda **k: None), patch(
        "AINDY.platform_layer.secret_broker.capability_scope", lambda caps: contextlib.nullcontext(),
    ), patch("AINDY.kernel.effect_ledger.resolve_effect_record", _resolve), patch.object(
        tr, "_finalize_tool_effect", _finalize,
    ), patch.object(tr, "_tool_idempotency_enabled", return_value=True):
        yield keys


def _send_tool(runs):
    def _fn(args, user_id, db):
        runs.append(dict(args))
        return {"sent": args["text"]}

    tr.register_tool(
        name="notify.send", risk="low", description="t", capability="c", required_capability="c",
        category="test", egress_scope="none", execution_guarantee="EXACTLY_ONCE",
    )(_fn)


def _call(step_index=None, text="remind me"):
    return tr.execute_tool("notify.send", {"text": text}, "user-1", MagicMock(), run_id=RUN,
                           execution_token={"t": 1}, step_index=step_index)


def test_the_scope_is_the_run_narrowed_to_the_step():
    assert tr.tool_effect_scope(RUN) == RUN
    assert tr.tool_effect_scope(RUN, 0) == f"{RUN}#step:0"
    assert tr.tool_effect_scope(RUN, 0) != tr.tool_effect_scope(RUN, 1)


def test_two_steps_with_identical_args_both_run(ledger):
    """★ The defect: step 4 replayed step 1 and never sent."""
    runs: list = []
    _send_tool(runs)
    first = _call(step_index=1)
    second = _call(step_index=4)
    assert len(runs) == 2, "the second step's effect was absorbed by the first step's key"
    assert not second.get("idempotent_replay")
    assert first["success"] and second["success"]
    assert ledger[0] != ledger[1]


def test_the_same_step_retried_still_replays(ledger):
    """The at-most-once the gate is for: a re-attempt of ONE step is one effect."""
    runs: list = []
    _send_tool(runs)
    _call(step_index=2)
    again = _call(step_index=2)
    assert len(runs) == 1
    assert again.get("idempotent_replay") is True


def test_a_caller_without_a_step_keeps_the_run_scope(ledger):
    """Liveness control for the first test, and the unchanged contract for syscalls / MCP:
    with no step index, identical args in one run ARE one effect."""
    runs: list = []
    _send_tool(runs)
    _call()
    again = _call()
    assert len(runs) == 1 and again.get("idempotent_replay") is True


def test_the_worker_seam_passes_its_step_index():
    """nodus_vm: `run_agent_tool` is the seam; its step index must reach the key."""
    from AINDY.runtime import nodus_worker

    seen: dict = {}

    def _execute_tool(**kw):
        seen.update(kw)
        return {"success": True, "result": {}, "error": None}

    factory = MagicMock()
    with patch("AINDY.agents.tool_registry.execute_tool", _execute_tool), patch.object(
        nodus_worker, "_record_step", lambda *a, **k: None,
    ), patch("AINDY.agents.authority_negotiation.authority_negotiation_enabled", return_value=False):
        nodus_worker.run_agent_tool("notify.send", {"text": "x"}, user_id="u", run_id=RUN,
                                    execution_token={"t": 1}, session_factory=factory, step_index=3)
    assert seen.get("step_index") == 3


def test_the_agent_flow_node_passes_its_step_index(monkeypatch):
    """agent_flow: `agent_execute_step` is the seam; `current_step_index` must reach the key."""
    from AINDY.runtime import nodus_adapter
    from tests.unit.test_retry_classification import _FakeDB

    seen: dict = {}

    def _execute_tool(**kw):
        seen.update(kw)
        return {"success": True, "result": {}, "error": None}

    monkeypatch.setattr(nodus_adapter, "execute_tool", _execute_tool)
    monkeypatch.setattr(nodus_adapter, "record_agent_event", lambda **kw: "evt")
    monkeypatch.setattr(nodus_adapter, "queue_system_event", lambda **kw: "sys")
    monkeypatch.setattr(nodus_adapter, "emit_system_event", lambda **kw: "sys")
    monkeypatch.setattr(nodus_adapter, "check_tool_capability",
                        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []})
    monkeypatch.setattr("AINDY.memory.memory_helpers.enrich_context", lambda ctx: ctx)
    steps = [{"tool": "t", "args": {}, "risk_level": "low", "description": "d"}] * 3
    state = {
        "agent_run_id": RUN, "user_id": "00000000-0000-0000-0000-000000000002",
        "capability_token": {"sig": "x"}, "correlation_id": "run_test",
        "steps": steps, "current_step_index": 2, "step_results": [],
    }
    nodus_adapter.agent_execute_step(state, {"db": _FakeDB(), "trace_id": "t"})
    assert seen.get("step_index") == 2
