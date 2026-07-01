"""Unit tests for the RTR-1 Phase 2b agent-plan → Nodus workflow compiler."""
from __future__ import annotations

import pytest

from AINDY.runtime.agent_plan_compiler import compile_agent_plan, WORKFLOW_NAME
from AINDY.runtime.nodus_flow_compiler import compile_nodus_flow


def test_single_step_plan():
    c = compile_agent_plan({"steps": [{"tool": "search", "args": {"q": "x"}}]})
    assert c["workflow_name"] == WORKFLOW_NAME
    assert c["state_inputs"] == {"__step_0_tool": "search", "__step_0_args": {"q": "x"}}
    assert c["steps"][0]["result_key"] == "__step_0_result"
    # The generated source parses as a native workflow with one step.
    graph = compile_nodus_flow(c["source"])
    assert graph["steps"] == ["step_0"]
    assert graph["start"] == ["step_0"]
    assert graph["end"] == ["step_0"]


def test_multi_step_sequential_deps():
    plan = {
        "steps": [
            {"tool": "a", "args": {}, "risk_level": "low", "description": "d0"},
            {"tool": "b", "args": {"k": 1}, "risk_level": "high", "description": "d1"},
            {"tool": "c", "args": {}},
        ]
    }
    c = compile_agent_plan(plan)
    graph = compile_nodus_flow(c["source"])
    assert graph["steps"] == ["step_0", "step_1", "step_2"]
    # Sequential chain: step_0 → step_1 → step_2.
    assert graph["start"] == ["step_0"]
    assert graph["end"] == ["step_2"]
    assert graph["edges"]["step_0"] == ["step_1"]
    assert graph["edges"]["step_1"] == ["step_2"]
    # Per-step metadata preserved.
    assert [s["tool"] for s in c["steps"]] == ["a", "b", "c"]
    assert c["steps"][0]["risk_level"] == "low"
    assert c["steps"][1]["risk_level"] == "high"
    assert c["steps"][2]["risk_level"] == "high"  # default
    assert c["state_inputs"]["__step_1_args"] == {"k": 1}


def test_args_default_to_empty_dict():
    c = compile_agent_plan({"steps": [{"tool": "t"}]})
    assert c["state_inputs"]["__step_0_args"] == {}


def test_injection_safe_tool_and_args_never_in_source():
    """Planner-derived values (possibly LLM-authored) must stay data, not code."""
    evil_tool = 'x"); run_workflow(evil); ("'
    evil_arg = 'val"); sys("sys.v1.destroy", {}); ("'
    c = compile_agent_plan({"steps": [{"tool": evil_tool, "args": {"payload": evil_arg}}]})
    # Neither the malicious tool name nor arg value appears in the source.
    assert evil_tool not in c["source"]
    assert evil_arg not in c["source"]
    assert "run_workflow(evil)" not in c["source"]
    # They are carried as data in state_inputs instead.
    assert c["state_inputs"]["__step_0_tool"] == evil_tool
    assert c["state_inputs"]["__step_0_args"] == {"payload": evil_arg}
    # And the source still parses cleanly.
    compile_nodus_flow(c["source"])


@pytest.mark.parametrize(
    "plan,match",
    [
        ({"steps": []}, "no steps"),
        ({}, "no steps"),
        ({"steps": [{"args": {}}]}, "no tool name"),
        ({"steps": [{"tool": ""}]}, "no tool name"),
        ({"steps": [{"tool": "t", "args": [1, 2]}]}, "args must be an object"),
        ({"steps": ["not-an-object"]}, "not an object"),
    ],
)
def test_invalid_plans_rejected(plan, match):
    with pytest.raises(ValueError, match=match):
        compile_agent_plan(plan)


def test_compiled_workflow_executes_and_captures_results():
    """End-to-end: the generated workflow runs its steps in order and each
    step's call_tool result lands under its result_key."""
    from nodus.runtime.embedding import NodusRuntime

    plan = {
        "steps": [
            {"tool": "search", "args": {"q": "weather"}},
            {"tool": "summarize", "args": {"text": "hi"}},
        ]
    }
    c = compile_agent_plan(plan)

    order = []
    store = dict(c["state_inputs"])

    def call_tool(name, args):
        order.append((name, dict(args) if isinstance(args, dict) else args))
        return {"success": True, "result": {"ran": name}, "error": None}

    rt = NodusRuntime()
    rt.register_function("call_tool", call_tool, arity=2)
    rt.register_function("set_state", lambda k, v: store.__setitem__(k, v), arity=2)
    rt.register_function("get_state", store.get, arity=1)

    runnable = c["source"] + f"\nrun_workflow({c['workflow_name']})"
    result = rt.run_source(runnable, filename="<agent>", initial_globals={}, host_globals={})

    assert result.get("ok") is True, result.get("error")
    # Tools called in plan order with the right args.
    assert order == [("search", {"q": "weather"}), ("summarize", {"text": "hi"})]
    # Each step's result captured under its result_key.
    for step in c["steps"]:
        assert store[step["result_key"]] == {"success": True, "result": {"ran": step["tool"]}, "error": None}
