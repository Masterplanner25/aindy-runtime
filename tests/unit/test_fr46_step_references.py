"""FR-46 — a plan step's argument may take an earlier step's result (DEC-073..075).

The app's first real goal ("research X, then use the research to…") stored the planner's
pre-research sentence as its "findings", and every step said `success`. Two later runs showed the
cost compounding: recall ranked those placeholder notes first (`214ac631`), and a planner that
had recalled the strategy invented a `strategy_id` because the recall's result could not reach
the steps that needed it (`abf834d4`). That last one is the `results.0.id` case below.

What is pinned:
* the resolver and plan-time validation (DEC-073), pure;
* both backends resolve BEFORE `execute_tool` (DEC-074): agent_flow through the real node;
  nodus_vm through a REAL compiled segment run by the real worker (`run_one`), both within one
  segment and across two, where the second segment's guest state is empty;
* the tool receives the value, never the placeholder, so `args_schema` in `enforce` mode passes
  and the idempotency key differs when the found value differs;
* unresolvable fails the step `invalid` and the tool is NOT called (DEC-075);
* flag off is exactly today's behaviour; the catalog line appears only when on.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from AINDY.agents.step_references import (
    PLANNER_REFERENCE_LINE,
    lookup_from_guest_state,
    lookup_from_step_results,
    resolve_step_references,
    validate_plan_references,
)

pytestmark = pytest.mark.runtime_only

REF = "$from_step"


@pytest.fixture
def refs_on(monkeypatch):
    monkeypatch.setenv("AINDY_PLAN_STEP_REFERENCES", "1")


def _lookup(**entries):
    return lookup_from_step_results(
        [{"step_index": int(k[1:]), **v} for k, v in entries.items()]
    )


# ── the resolver (DEC-073 / DEC-075) ─────────────────────────────────────────────────────────


class TestTheResolver:
    def test_whole_result_and_a_path(self):
        lookup = _lookup(s0={"status": "success", "result": {"raw_result": "AEO findings"}})
        resolved, errors = resolve_step_references(
            {"content": {REF: 0, "path": "raw_result"}, "whole": {REF: 0}, "tag": "mkt"}, lookup)
        assert errors == []
        assert resolved == {"content": "AEO findings", "whole": {"raw_result": "AEO findings"}, "tag": "mkt"}

    def test_the_abf834d4_case_a_list_index_into_a_recall(self):
        """The planner recalled the strategy, then invented its id. With a reference it can
        name the recall's first hit."""
        lookup = _lookup(s0={"status": "success", "result": {"results": [{"id": "mp-42"}]}})
        resolved, errors = resolve_step_references(
            {"strategy_id": {REF: 0, "path": "results.0.id"}}, lookup)
        assert errors == [] and resolved == {"strategy_id": "mp-42"}

    def test_references_nested_in_lists_and_dicts(self):
        lookup = _lookup(s0={"status": "success", "result": {"a": 1}},
                         s1={"status": "success", "result": {"b": 2}})
        resolved, errors = resolve_step_references(
            {"items": [{REF: 0, "path": "a"}, {"deep": {REF: 1, "path": "b"}}]}, lookup)
        assert errors == [] and resolved == {"items": [1, {"deep": 2}]}

    def test_a_none_value_is_a_value_not_a_miss(self):
        lookup = _lookup(s0={"status": "success", "result": {"cursor": None}})
        resolved, errors = resolve_step_references({"c": {REF: 0, "path": "cursor"}}, lookup)
        assert errors == [] and resolved == {"c": None}

    @pytest.mark.parametrize("entry, needle", [
        (None, "no recorded result"),
        ({"status": "failed", "result": None}, "failed"),
        ({"status": "skipped", "result": {"authority_gate": "skip"}}, "skipped"),
        ({"status": "success", "result": {"other": 1}}, "has no 'raw_result'"),
    ])
    def test_unresolvable_is_an_error_never_a_guess(self, entry, needle):
        lookup = (lambda i: None) if entry is None else _lookup(s0=entry)
        _resolved, errors = resolve_step_references({"x": {REF: 0, "path": "raw_result"}}, lookup)
        assert errors and needle in errors[0]

    def test_an_unreadable_lookup_fails_rather_than_raises(self):
        def _boom(i):
            raise RuntimeError("db down")

        _resolved, errors = resolve_step_references({"x": {REF: 0}}, _boom)
        assert errors and "could not be read" in errors[0]

    def test_a_dict_without_the_key_is_left_alone(self):
        resolved, errors = resolve_step_references({"filter": {"from_step": 0}}, lambda i: None)
        assert errors == [] and resolved == {"filter": {"from_step": 0}}

    def test_simulation_resolves_from_guest_state(self):
        lookup = lookup_from_guest_state({"__step_0_result": {"success": True, "result": {"v": 7}}})
        resolved, errors = resolve_step_references({"v": {REF: 0, "path": "v"}}, lookup)
        assert errors == [] and resolved == {"v": 7}


class TestPlanValidation:
    def _plan(self, *steps):
        return {"steps": list(steps), "overall_risk": "low"}

    def test_a_valid_plan_has_no_errors(self):
        plan = self._plan({"tool": "research.query", "args": {"q": "seo"}},
                          {"tool": "memory.write", "args": {"content": {REF: 0, "path": "raw_result"}}})
        assert validate_plan_references(plan) == []

    @pytest.mark.parametrize("ref, needle", [
        ({REF: 1}, "not an earlier step"),                  # itself
        ({REF: 5}, "not an earlier step"),                  # the future
        ({REF: -1}, "non-negative"),
        ({REF: True}, "non-negative"),                      # a bool is not an index
        ({REF: 0, "path": "a..b"}, "dot path"),
        ({REF: 0, "path": ""}, "dot path"),
        ({REF: 0, "path": "a", "default": 1}, "only"),
    ])
    def test_refused_shapes(self, ref, needle):
        plan = self._plan({"tool": "a", "args": {}}, {"tool": "b", "args": {"x": ref}})
        errors = validate_plan_references(plan)
        assert errors and needle in " ".join(errors), errors

    def test_wait_steps_take_no_index(self):
        """Step 1 here is `send` (the WAIT is not counted), the same ordinal the compiler,
        `agent_steps` and the verifier use."""
        plan = self._plan({"tool": "search", "args": {}}, {"wait_for": "approval.received"},
                          {"tool": "send", "args": {}},
                          {"tool": "log", "args": {"x": {REF: 1}}})
        assert validate_plan_references(plan) == []
        plan["steps"][3]["args"]["x"] = {REF: 2}
        assert validate_plan_references(plan), "step 2 is `log` itself"


# ── the planner contract (DEC-075) ───────────────────────────────────────────────────────────


def test_the_catalog_carries_the_line_only_when_on(monkeypatch):
    from AINDY.agents.agent_runtime.planning import _build_planner_prompt

    kwargs = dict(system_prompt="base", planner_context={}, tools=[{"name": "t", "description": "d"}])
    monkeypatch.delenv("AINDY_PLAN_STEP_REFERENCES", raising=False)
    assert PLANNER_REFERENCE_LINE not in _build_planner_prompt(**kwargs)
    monkeypatch.setenv("AINDY_PLAN_STEP_REFERENCES", "1")
    assert PLANNER_REFERENCE_LINE in _build_planner_prompt(**kwargs)


# ── agent_flow: the real node (DEC-074) ──────────────────────────────────────────────────────


def _drive_agent_flow(monkeypatch, *, step_results, args):
    from AINDY.runtime import nodus_adapter
    from tests.unit.test_retry_classification import _FakeDB

    called: list = []

    def _execute_tool(**kw):
        called.append(kw)
        return {"success": True, "result": {"stored": True}, "error": None}

    monkeypatch.setattr(nodus_adapter, "execute_tool", _execute_tool)
    monkeypatch.setattr(nodus_adapter, "record_agent_event", lambda **kw: "evt")
    monkeypatch.setattr(nodus_adapter, "queue_system_event", lambda **kw: "sys")
    monkeypatch.setattr(nodus_adapter, "emit_system_event", lambda **kw: "sys")
    monkeypatch.setattr(nodus_adapter, "check_tool_capability",
                        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []})
    monkeypatch.setattr("AINDY.memory.memory_helpers.enrich_context", lambda ctx: ctx)
    db = _FakeDB()
    state = {
        "agent_run_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "00000000-0000-0000-0000-000000000002",
        "capability_token": {"sig": "x"}, "correlation_id": "run_t",
        "steps": [{"tool": "research.query", "args": {}, "risk_level": "low", "description": "r"},
                  {"tool": "memory.write", "args": args, "risk_level": "low", "description": "w"}],
        "current_step_index": 1, "step_results": step_results,
    }
    out = nodus_adapter.agent_execute_step(state, {"db": db, "trace_id": "t"})
    return out, called, db


def test_agent_flow_the_tool_receives_the_value(monkeypatch, refs_on):
    out, called, db = _drive_agent_flow(
        monkeypatch,
        step_results=[{"step_index": 0, "tool": "research.query", "status": "success",
                       "result": {"raw_result": "AEO findings"}, "error": None}],
        args={"content": {REF: 0, "path": "raw_result"}},
    )
    assert out["status"] == "SUCCESS"
    assert called[0]["args"] == {"content": "AEO findings"}
    step_row = next(o for o in db.added if getattr(o, "step_index", None) == 1)
    assert step_row.tool_args == {"content": "AEO findings"}, "the row records the call, not the plan"


def test_agent_flow_unresolvable_fails_invalid_and_the_tool_never_runs(monkeypatch, refs_on):
    out, called, _db = _drive_agent_flow(
        monkeypatch,
        step_results=[{"step_index": 0, "tool": "research.query", "status": "failed",
                       "result": None, "error": "boom"}],
        args={"content": {REF: 0, "path": "raw_result"}},
    )
    assert called == [], "the tool ran on a placeholder"
    assert out["status"] == "FAILURE" and "could not be resolved" in out["error"]


def test_agent_flow_flag_off_is_todays_behaviour(monkeypatch):
    monkeypatch.delenv("AINDY_PLAN_STEP_REFERENCES", raising=False)
    placeholder = {"content": {REF: 0, "path": "raw_result"}}
    _out, called, _db = _drive_agent_flow(
        monkeypatch,
        step_results=[{"step_index": 0, "status": "success", "result": {"raw_result": "x"}}],
        args=placeholder,
    )
    assert called[0]["args"] == placeholder


# ── nodus_vm: a REAL compiled segment through the real worker (DEC-074) ──────────────────────


@pytest.fixture
def vm(monkeypatch):
    """The real worker (`run_one`) running a real compiled segment; `agent_steps` on SQLite
    through the `SessionLocal` the worker seam opens; tools registered for real."""
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.agents import tool_registry as tr
    from AINDY.db.database import Base
    from AINDY.db.models import AgentRun, AgentStep  # noqa: F401 — registers the tables

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.tables["agent_runs"].create(bind=engine)
    Base.metadata.tables["agent_steps"].create(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", factory)
    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability",
        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []},
    )
    received: list = []
    suffix = uuid.uuid4().hex[:8]
    research, write = f"__fr46_research_{suffix}", f"__fr46_write_{suffix}"
    tr.TOOL_REGISTRY[research] = {
        "fn": lambda args, user_id, db: {"raw_result": "AEO: answer-first copy, JSON-LD"},
        "risk": "low", "isolation": None, "execution_guarantee": "AT_LEAST_ONCE",
        "egress_scope": None, "capability": None, "args_schema": None,
    }
    tr.TOOL_REGISTRY[write] = {
        "fn": lambda args, user_id, db: received.append(dict(args)) or {"stored": True},
        "risk": "low", "isolation": None, "execution_guarantee": "AT_LEAST_ONCE",
        "egress_scope": None, "capability": None,
        # FR-33 — the write takes a STRING; the placeholder is a dict and would be refused
        "args_schema": {"type": "object", "required": ["content"], "properties": {"content": {"type": "string"}}},
    }
    run_id = str(uuid.uuid4())
    try:
        yield {"research": research, "write": write, "received": received, "run_id": run_id,
               "factory": factory}
    finally:
        tr.TOOL_REGISTRY.pop(research, None)
        tr.TOOL_REGISTRY.pop(write, None)
        engine.dispose()


def _run_segment(vm, tool_steps, base_index):
    from AINDY.runtime import nodus_worker
    from AINDY.runtime.agent_plan_compiler import compile_agent_segment

    compiled = compile_agent_segment(tool_steps, base_index=base_index)
    return nodus_worker.run_one({
        "script": compiled["source"] + f"\nrun_workflow({compiled['workflow_name']})\n",
        "filename": "fr46.nd", "state": {}, "memory_context": {},
        "input_payload": compiled["input_payload"],
        "context": {"user_id": str(uuid.uuid4()), "execution_unit_id": "eu-fr46", "trace_id": "t-fr46",
                    "run_id": vm["run_id"], "execution_token": {"sig": "x"}},
    })


def _rows(vm):
    from AINDY.db.models import AgentStep

    db = vm["factory"]()
    try:
        return {r.step_index: r for r in db.query(AgentStep).all()}
    finally:
        db.close()


def _steps(vm, ref):
    return [{"tool": vm["research"], "args": {"q": "seo"}, "risk_level": "low", "description": "r"},
            {"tool": vm["write"], "args": {"content": ref}, "risk_level": "low", "description": "w"}]


def test_nodus_vm_within_one_segment(vm, refs_on, monkeypatch):
    monkeypatch.setenv("AINDY_TOOL_ARGS_VALIDATION", "enforce")
    _run_segment(vm, _steps(vm, {REF: 0, "path": "raw_result"}), base_index=0)
    assert vm["received"] == [{"content": "AEO: answer-first copy, JSON-LD"}], (
        "the tool did not receive the value (under `enforce` the placeholder would be refused)")
    assert _rows(vm)[1].tool_args == {"content": "AEO: answer-first copy, JSON-LD"}


def test_nodus_vm_across_segments_where_guest_state_is_empty(vm, refs_on):
    """Two segments, as after a WAIT or under durable step granularity: the second segment's
    guest state starts empty, so only the `agent_steps` row can carry step 0's result."""
    steps = _steps(vm, {REF: 0, "path": "raw_result"})
    _run_segment(vm, steps[:1], base_index=0)
    assert 0 in _rows(vm), "liveness: segment 0 recorded its row"
    _run_segment(vm, steps[1:], base_index=1)
    assert vm["received"] == [{"content": "AEO: answer-first copy, JSON-LD"}]


def test_nodus_vm_unresolvable_fails_the_step_and_the_tool_never_runs(vm, refs_on):
    _run_segment(vm, _steps(vm, {REF: 0, "path": "no_such_field"}), base_index=0)
    assert vm["received"] == [], "the tool ran on a placeholder or a guess"
    row = _rows(vm)[1]
    assert row.status == "failed" and "could not be resolved" in (row.error_message or "")


def test_nodus_vm_flag_off_passes_the_literal(vm, monkeypatch):
    monkeypatch.delenv("AINDY_PLAN_STEP_REFERENCES", raising=False)
    monkeypatch.setenv("AINDY_TOOL_ARGS_VALIDATION", "off")
    ref = {REF: 0, "path": "raw_result"}
    _run_segment(vm, _steps(vm, ref), base_index=0)
    assert vm["received"] == [{"content": ref}], "today's behaviour: the literal reaches the tool"


def test_the_key_sees_the_resolved_value():
    """DEC-074: two runs whose step 0 found different things key step 1 differently. Over the
    placeholder they would collide and the second run would replay the first's write."""
    from AINDY.agents.tool_registry import tool_effect_scope
    from AINDY.core.execution_gate import compute_action_id

    def _key(found):
        lookup = _lookup(s0={"status": "success", "result": {"raw_result": found}})
        args, _ = resolve_step_references({"content": {REF: 0, "path": "raw_result"}}, lookup)
        return compute_action_id(action_type="memory.write", input_payload=args,
                                 scope=tool_effect_scope("run-1", 1))

    assert _key("finding A") != _key("finding B")


# ── plan time, through the real `generate_plan` (DEC-073) ────────────────────────────────────

from tests.unit.test_agent_planning_contract import clean_agent_planner_registry  # noqa: E402,F401


def _plan_with(ref):
    return {"executive_summary": "s", "overall_risk": "low", "steps": [
        {"tool": "research.query", "args": {"q": "seo"}, "risk_level": "low", "description": "r"},
        {"tool": "memory.write", "args": {"content": ref}, "risk_level": "low", "description": "w"},
    ]}


def _generate(monkeypatch, plan):
    from AINDY.agents.agent_runtime.planning import generate_plan
    from AINDY.config import settings
    from AINDY.platform_layer import registry

    seen: dict = {}

    def backend(request):
        seen["system_prompt"] = request.system_prompt
        return plan

    registry.register_agent_planner_backend("fr46_backend", backend)
    monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_BACKEND", "fr46_backend")
    return generate_plan(objective="research then use it", user_id="user-1", db=object()), seen


def test_generate_plan_refuses_a_forward_reference(clean_agent_planner_registry, monkeypatch, refs_on):  # noqa: F811
    from AINDY.agents.agent_runtime.shared import get_runtime_compat_module

    plan, seen = _generate(monkeypatch, _plan_with({REF: 1}))
    assert plan is None, "a plan naming a step that has not run yet was accepted"
    assert "invalid step reference" in str(get_runtime_compat_module()._plan_failure.reason)
    assert PLANNER_REFERENCE_LINE in seen["system_prompt"], "the planner was never told the form"


def test_generate_plan_accepts_a_valid_reference(clean_agent_planner_registry, monkeypatch, refs_on):  # noqa: F811
    plan, _seen = _generate(monkeypatch, _plan_with({REF: 0, "path": "raw_result"}))
    assert plan is not None and plan["steps"][1]["args"]["content"] == {REF: 0, "path": "raw_result"}


def test_generate_plan_flag_off_validates_nothing(clean_agent_planner_registry, monkeypatch):  # noqa: F811
    monkeypatch.delenv("AINDY_PLAN_STEP_REFERENCES", raising=False)
    plan, seen = _generate(monkeypatch, _plan_with({REF: 1}))
    assert plan is not None, "flag off must be today's behaviour"
    assert PLANNER_REFERENCE_LINE not in seen["system_prompt"]
