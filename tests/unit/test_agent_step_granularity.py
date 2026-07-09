"""ECOGAP-1 Phase 2a — per-step segment granularity (opt-in).

When AINDY_DURABLE_STEP_GRANULARITY is on, each agent tool step becomes its own
segment, so Phase 2 crash continuation resumes at STEP granularity (completed
steps skip; a crash re-runs only the in-flight step). WAIT semantics and the
global step_index space are unchanged.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import AINDY.runtime.agent_plan_compiler as apc
from AINDY.runtime.agent_plan_compiler import compile_agent_segment, split_agent_plan

pytestmark = pytest.mark.runtime_only


def _stepgran(on: bool):
    return patch.object(apc, "_step_granularity_enabled", return_value=on)


_PLAN = {
    "steps": [
        {"tool": "a", "args": {"x": 1}},
        {"tool": "b", "args": {}},
        {"wait_for": "evt.x"},
        {"tool": "c", "args": {}},
        {"tool": "d", "args": {}},
        {"tool": "e", "args": {}},
    ]
}


def test_default_splits_at_wait_boundaries():
    with _stepgran(False):
        segs = split_agent_plan(_PLAN)
    assert [len(s["tool_steps"]) for s in segs] == [2, 3]
    assert [s["base_index"] for s in segs] == [0, 2]


def test_step_granularity_one_segment_per_step():
    with _stepgran(True):
        segs = split_agent_plan(_PLAN)
    # 5 tool steps → 5 single-step segments
    assert all(len(s["tool_steps"]) == 1 for s in segs)
    assert len(segs) == 5
    # global step_index space preserved and contiguous
    assert [s["base_index"] for s in segs] == [0, 1, 2, 3, 4]
    assert sum(len(s["tool_steps"]) for s in segs) == 5


def test_wait_attaches_to_last_step_before_wait():
    with _stepgran(True):
        segs = split_agent_plan(_PLAN)
    waits = [(s["base_index"], (s["wait"] or {}).get("event_type")) for s in segs if s["wait"]]
    # only step b (index 1) — the last step before the WAIT — carries the wait
    assert waits == [(1, "evt.x")]


def test_single_step_and_bare_wait_pass_through():
    plan = {"steps": [{"tool": "only", "args": {}}]}
    with _stepgran(True):
        segs = split_agent_plan(plan)
    assert len(segs) == 1 and len(segs[0]["tool_steps"]) == 1

    bare = {"steps": [{"wait_for": "evt.y"}, {"tool": "t", "args": {}}]}
    with _stepgran(True):
        segs2 = split_agent_plan(bare)
    # bare-wait segment (empty tool_steps) + one 1-step segment
    assert segs2[0]["tool_steps"] == [] and segs2[0]["wait"]["event_type"] == "evt.y"
    assert len(segs2[1]["tool_steps"]) == 1


def test_each_expanded_segment_compiles_standalone():
    """A 1-step segment is self-contained (input_payload built from its own step)."""
    with _stepgran(True):
        segs = split_agent_plan(_PLAN)
    for seg in segs:
        if not seg["tool_steps"]:
            continue
        compiled = compile_agent_segment(seg["tool_steps"], base_index=seg["base_index"])
        assert len(compiled["steps"]) == 1
        idx = seg["base_index"]
        assert compiled["input_payload"][f"__step_{idx}_tool"] == seg["tool_steps"][0]["tool"]
        assert f"__step_{idx}_result" == compiled["steps"][0]["result_key"]


def test_flag_default_off():
    from AINDY.config import settings

    assert settings.AINDY_DURABLE_STEP_GRANULARITY is False
