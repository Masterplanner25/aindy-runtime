"""Unit tests for the RTR-1 Phase 2b agent-plan → Nodus workflow compiler."""
from __future__ import annotations

import pytest

from AINDY.runtime.agent_plan_compiler import (
    compile_agent_plan,
    compile_agent_segment,
    split_agent_plan,
    WORKFLOW_NAME,
)
from AINDY.runtime.nodus_flow_compiler import compile_nodus_flow


def test_single_step_plan():
    c = compile_agent_plan({"steps": [{"tool": "search", "args": {"q": "x"}}]})
    assert c["workflow_name"] == WORKFLOW_NAME
    assert c["input_payload"] == {"__step_0_tool": "search", "__step_0_args": {"q": "x"}}
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
    assert c["input_payload"]["__step_1_args"] == {"k": 1}


def test_args_default_to_empty_dict():
    c = compile_agent_plan({"steps": [{"tool": "t"}]})
    assert c["input_payload"]["__step_0_args"] == {}


def test_injection_safe_tool_and_args_never_in_source():
    """Planner-derived values (possibly LLM-authored) must stay data, not code."""
    evil_tool = 'x"); run_workflow(evil); ("'
    evil_arg = 'val"); sys("sys.v1.destroy", {}); ("'
    c = compile_agent_plan({"steps": [{"tool": evil_tool, "args": {"payload": evil_arg}}]})
    # Neither the malicious tool name nor arg value appears in the source.
    assert evil_tool not in c["source"]
    assert evil_arg not in c["source"]
    assert "run_workflow(evil)" not in c["source"]
    # They are carried as data in input_payload instead.
    assert c["input_payload"]["__step_0_tool"] == evil_tool
    assert c["input_payload"]["__step_0_args"] == {"payload": evil_arg}
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
    results = {}

    def call_tool(name, args):
        order.append((name, dict(args) if isinstance(args, dict) else args))
        return {"success": True, "result": {"ran": name}, "error": None}

    rt = NodusRuntime()
    rt.register_function("call_tool", call_tool, arity=2)
    rt.register_function("set_state", lambda k, v: results.__setitem__(k, v), arity=2)

    runnable = c["source"] + f"\nrun_workflow({c['workflow_name']})"
    # Tool names + args are delivered via the input_payload global (the channel the
    # nodus.execute node forwards into the script).
    result = rt.run_source(
        runnable, filename="<agent>",
        initial_globals={"input_payload": dict(c["input_payload"])}, host_globals={},
    )

    assert result.get("ok") is True, result.get("error")
    # Tools called in plan order with the right args.
    assert order == [("search", {"q": "weather"}), ("summarize", {"text": "hi"})]
    # Each step's result captured under its result_key.
    for step in c["steps"]:
        assert results[step["result_key"]] == {"success": True, "result": {"ran": step["tool"]}, "error": None}


# --------------------------------------------------------------------------- #
# RTR-1 Phase 2d — per-step retry + halt-on-first-failure
# --------------------------------------------------------------------------- #

def test_max_attempts_resolved_from_risk_level():
    """low/medium steps get 3 attempts; high (and default) get 1."""
    c = compile_agent_plan({"steps": [
        {"tool": "a", "risk_level": "low"},
        {"tool": "b", "risk_level": "medium"},
        {"tool": "c", "risk_level": "high"},
        {"tool": "d"},  # default risk → high → 1
    ]})
    assert [s["max_attempts"] for s in c["steps"]] == [3, 3, 1, 1]
    # The literal attempt budget is baked into the generated source.
    assert "__attempt_0 < 3" in c["source"]
    assert "__attempt_2 < 1" in c["source"]
    # Every step throws on failure (halt-on-first-failure).
    assert c["source"].count("throw ") == 4
    # Still parses as a native workflow.
    compile_nodus_flow(c["source"])


def _run_compiled(plan, call_tool):
    """Execute a compiled plan in-process (mirrors the nodus_worker host wiring).

    Returns (result, captured_state, call_log). In-process on purpose — the real
    path spawns nodus_worker as a subprocess, which the Windows dev box blocks
    (WinError 4551); the VM semantics are identical either way.
    """
    from nodus.runtime.embedding import NodusRuntime
    from AINDY.core.retry_policy import is_retryable_error

    c = compile_agent_plan(plan)
    state = {}
    log = []

    def _call(name, args):
        log.append(str(name))
        return call_tool(str(name), dict(args) if isinstance(args, dict) else args)

    rt = NodusRuntime()
    rt.register_function("call_tool", _call, arity=2)
    rt.register_function("set_state", lambda k, v: state.__setitem__(k, v), arity=2)
    rt.register_function("is_retryable_error", lambda e: is_retryable_error(e), arity=1)
    runnable = c["source"] + f"\nrun_workflow({c['workflow_name']})"
    result = rt.run_source(
        runnable, filename="<agent>",
        initial_globals={"input_payload": dict(c["input_payload"])}, host_globals={},
    )
    return result, state, log


def test_failed_step_halts_downstream():
    """A step whose tool fails halts the workflow — dependent steps never run."""
    plan = {"steps": [
        {"tool": "boom", "risk_level": "high"},   # 1 attempt, fails
        {"tool": "next", "risk_level": "low"},
    ]}
    result, state, log = _run_compiled(
        plan,
        lambda name, args: {"success": False, "result": None, "error": "kaboom"}
        if name == "boom" else {"success": True, "result": "ok", "error": None},
    )
    assert result.get("ok") is True  # workflow captures the failure; script itself is fine
    assert log == ["boom"]  # step_1 ("next") never called
    assert "__step_0_result" in state          # failing step still recorded its result
    assert "__step_1_result" not in state       # downstream halted


def test_low_risk_step_retries_up_to_max_attempts():
    """A transient failure on a low-risk step is retried 3× before halting."""
    plan = {"steps": [{"tool": "flaky", "risk_level": "low"}]}
    result, state, log = _run_compiled(
        plan,
        lambda name, args: {"success": False, "result": None, "error": "timeout transient"},
    )
    assert log == ["flaky", "flaky", "flaky"]  # 1 initial + 2 retries
    assert state["__step_0_result"]["success"] is False


def test_retry_recovers_and_proceeds():
    """A step that succeeds on its 2nd attempt records success and lets the plan continue."""
    calls = {"n": 0}

    def call_tool(name, args):
        if name == "flaky":
            calls["n"] += 1
            if calls["n"] >= 2:
                return {"success": True, "result": "recovered", "error": None}
            return {"success": False, "result": None, "error": "timeout"}
        return {"success": True, "result": "ok", "error": None}

    plan = {"steps": [
        {"tool": "flaky", "risk_level": "medium"},
        {"tool": "after", "risk_level": "low"},
    ]}
    result, state, log = _run_compiled(plan, call_tool)
    assert log == ["flaky", "flaky", "after"]  # retried once, then proceeded
    assert state["__step_0_result"]["success"] is True
    assert "__step_1_result" in state


def test_non_retryable_error_short_circuits_retry():
    """A non-transient error (e.g. 'permission') is not retried even on a low-risk step."""
    plan = {"steps": [{"tool": "denied", "risk_level": "low"}]}
    result, state, log = _run_compiled(
        plan,
        lambda name, args: {"success": False, "result": None, "error": "permission denied"},
    )
    assert log == ["denied"]  # single attempt — is_retryable_error() broke the loop
    assert state["__step_0_result"]["success"] is False


def test_throw_message_is_structural_not_planner_data():
    """The throw message is keyed by step index only — no planner/LLM value in code."""
    evil = 'x"); run_workflow(evil); throw("'
    c = compile_agent_plan({"steps": [{"tool": evil, "risk_level": "high"}]})
    assert evil not in c["source"]
    assert 'throw "agent step step_0 failed"' in c["source"]
    compile_nodus_flow(c["source"])


# --------------------------------------------------------------------------- #
# RTR-1 Phase 2e — plan segmentation at WAIT boundaries
# --------------------------------------------------------------------------- #

def test_split_plan_no_wait_is_single_segment():
    plan = {"steps": [{"tool": "a"}, {"tool": "b"}]}
    segs = split_agent_plan(plan)
    assert len(segs) == 1
    assert [s["tool"] for s in segs[0]["tool_steps"]] == ["a", "b"]
    assert segs[0]["base_index"] == 0
    assert segs[0]["wait"] is None


def test_split_plan_at_wait_boundary():
    plan = {"steps": [
        {"tool": "search", "risk_level": "low"},
        {"tool": "draft", "risk_level": "medium"},
        {"wait_for": "approval.received", "correlation_key": "run-42"},
        {"tool": "send", "risk_level": "high"},
    ]}
    segs = split_agent_plan(plan)
    assert len(segs) == 2
    # Segment 0: the two pre-wait tool steps, followed by the wait.
    assert [s["tool"] for s in segs[0]["tool_steps"]] == ["search", "draft"]
    assert segs[0]["base_index"] == 0
    assert segs[0]["wait"] == {"event_type": "approval.received", "correlation_key": "run-42"}
    # Segment 1: the post-wait tool step, indices continue globally from 2.
    assert [s["tool"] for s in segs[1]["tool_steps"]] == ["send"]
    assert segs[1]["base_index"] == 2
    assert segs[1]["wait"] is None


def test_split_preserves_global_step_indices_across_segments():
    plan = {"steps": [
        {"tool": "a"}, {"tool": "b"},
        {"wait_for": "e1"},
        {"tool": "c"},
    ]}
    segs = split_agent_plan(plan)
    c0 = compile_agent_segment(segs[0]["tool_steps"], base_index=segs[0]["base_index"], workflow_name="seg0")
    c1 = compile_agent_segment(segs[1]["tool_steps"], base_index=segs[1]["base_index"], workflow_name="seg1")
    assert [m["index"] for m in c0["steps"]] == [0, 1]
    assert [m["index"] for m in c1["steps"]] == [2]
    # Segment 1's first (and only) step has no `after` dependency — it opens a workflow.
    assert "after" not in c1["source"]
    assert "__step_2_result" in c1["source"]
    compile_nodus_flow(c1["source"])


def test_split_leading_and_consecutive_waits():
    # A plan may open with a wait (empty first segment) and have back-to-back waits.
    plan = {"steps": [
        {"wait_for": "start"},
        {"tool": "a"},
        {"wait_for": "mid"},
        {"wait_for": "mid2"},
        {"tool": "b"},
    ]}
    segs = split_agent_plan(plan)
    assert [([t["tool"] for t in s["tool_steps"]], s["base_index"],
             s["wait"]["event_type"] if s["wait"] else None) for s in segs] == [
        ([], 0, "start"),
        (["a"], 0, "mid"),
        ([], 1, "mid2"),
        (["b"], 1, None),
    ]


def test_split_rejects_empty_wait_for():
    with pytest.raises(ValueError, match="wait_for must be a non-empty string"):
        split_agent_plan({"steps": [{"tool": "a"}, {"wait_for": ""}]})
