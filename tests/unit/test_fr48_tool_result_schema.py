"""FR-48 — a tool may declare what it RETURNS, and a step reference's path is checked against it
at plan time (DEC-077, DEC-078).

The app's first FR-46 evidence run (`615b67ea…`) planned
`memory.write {"content": {"$from_step": 0, "path": "results"}}` where step 0 was
`research.query`, which returns `{raw_result}`. The path was well-formed, so planning accepted it;
step 0 ran and was paid for, then step 2 failed `invalid` and three steps never ran. The planner
had been shown every tool's arguments and no tool's result, so the path was a guess.

What is pinned:
* `register_tool(result_schema=)` is stored, surfaced by the default tool list, and malformed
  shapes are refused at registration at every depth;
* the catalog renders `returns=…`, from the tool dict or the registry (an app provider's dict
  may omit the key);
* the path check (DEC-077): a node with `properties` is closed unless `additionalProperties`; a
  node that says nothing is open; lists are indexed by number; a scalar has no fields;
* every path the check accepts on a conforming result is one `resolve_path` actually finds, and
  every path it refuses is one it does not;
* `validate_plan_references` refuses the `615b67ea` plan and accepts the `19dcf508` one; a tool
  that declares nothing is checked for form only, as before;
* through the real `generate_plan`: the refusal fails the plan (no re-plan, DEC-078), and flag
  off checks nothing.
"""
from __future__ import annotations

import uuid

import pytest

from AINDY.agents import tool_registry
from AINDY.agents.step_references import (
    PLANNER_REFERENCE_LINE,
    result_path_error,
    validate_plan_references,
)
from AINDY.agents.tool_registry import register_tool, tool_result_schema
from AINDY.core.result_path import MISSING, resolve_path

pytestmark = pytest.mark.runtime_only

REF = "$from_step"

RESEARCH_RESULT = {"type": "object", "properties": {"raw_result": {"type": "string"}}}
SEARCH_RESULT = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {"type": "object", "properties": {"id": {"type": "string"}, "title": {"type": "string"}}},
        },
        "meta": {"type": "object"},  # declares nothing below: open
    },
}


def _register(name, result_schema=None):
    register_tool(
        name, risk="low", description="d", capability="c", required_capability="",
        category="test", egress_scope="", result_schema=result_schema,
    )(lambda args, user_id, db: None)


@pytest.fixture
def tools():
    """Two real registrations shaped like the app's `research.query` / `search.query`, plus one
    that declares nothing."""
    suffix = uuid.uuid4().hex[:8]
    names = {
        "research": f"__fr48_research_{suffix}",
        "search": f"__fr48_search_{suffix}",
        "bare": f"__fr48_bare_{suffix}",
        "write": f"__fr48_write_{suffix}",
    }
    _register(names["research"], RESEARCH_RESULT)
    _register(names["search"], SEARCH_RESULT)
    _register(names["bare"])
    _register(names["write"])
    try:
        yield names
    finally:
        for name in names.values():
            tool_registry.TOOL_REGISTRY.pop(name, None)


# ── registration ─────────────────────────────────────────────────────────────────────────────


def test_the_schema_is_stored_and_surfaced(tools):
    from AINDY.platform_layer.runtime_agent_defaults import get_tools_for_run

    assert tool_registry.TOOL_REGISTRY[tools["research"]]["result_schema"] == RESEARCH_RESULT
    assert tool_result_schema(tools["research"]) == RESEARCH_RESULT
    assert tool_result_schema(tools["bare"]) is None
    listed = {t["name"]: t for t in get_tools_for_run({})}
    assert listed[tools["search"]]["result_schema"] == SEARCH_RESULT
    assert listed[tools["bare"]]["result_schema"] is None


@pytest.mark.parametrize("bad, why", [
    ("raw_result", "must be a dict"),
    ({"properties": ["raw_result"]}, "'properties'] must be a dict"),
    ({"properties": {"raw_result": "string"}}, "result_schema.properties.raw_result must be a dict"),
    ({"items": [{"type": "string"}]}, "result_schema.items must be a dict"),
    ({"properties": {"a": {"items": {"properties": 3}}}}, "result_schema.properties.a.items['properties']"),
    ({"required": "raw_result"}, "'required'] must be a list"),
    ({"additionalProperties": "yes"}, "'additionalProperties'] must be a bool or a schema"),
])
def test_a_malformed_schema_is_refused_at_registration(bad, why):
    name = "__fr48_bad_" + uuid.uuid4().hex[:8]
    with pytest.raises(ValueError) as exc:
        _register(name, bad)
    assert why in str(exc.value)
    assert name not in tool_registry.TOOL_REGISTRY


# ── the catalog ──────────────────────────────────────────────────────────────────────────────


def test_the_catalog_line_carries_returns(tools):
    from AINDY.agents.agent_runtime.planning import _catalog_line

    name = tools["research"]
    with_key = _catalog_line({"name": name, "description": "d", "risk": "low", "result_schema": RESEARCH_RESULT})
    without_key = _catalog_line({"name": name, "description": "d", "risk": "low"})  # an app provider's dict
    assert with_key == without_key, "the registry is the contract's home; a provider that omits the key must not hide it"
    assert 'returns={"properties":{"raw_result":{"type":"string"}},"type":"object"}' in with_key
    assert "returns=" not in _catalog_line({"name": tools["bare"], "description": "d", "risk": "low"})


def test_the_planner_line_says_to_follow_returns():
    assert "returns=" in PLANNER_REFERENCE_LINE


# ── the path check (DEC-077) ─────────────────────────────────────────────────────────────────

OPEN_TAIL = {"type": "object", "properties": {"a": {"type": "object", "additionalProperties": True}}}
TYPED_EXTRA = {"type": "object", "properties": {}, "additionalProperties": {"type": "object", "properties": {"x": {"type": "integer"}}}}

# (schema, path, a conforming result, the path should be accepted)
CASES = [
    (RESEARCH_RESULT, "raw_result", {"raw_result": "findings"}, True),
    (RESEARCH_RESULT, "results", {"raw_result": "findings"}, False),                  # the 615b67ea guess
    (RESEARCH_RESULT, "raw_result.text", {"raw_result": "findings"}, False),          # a string has no fields
    (SEARCH_RESULT, "results.0.id", {"results": [{"id": "s1", "title": "t"}], "meta": {}}, True),
    (SEARCH_RESULT, "results.-1.title", {"results": [{"id": "s1", "title": "t"}], "meta": {}}, True),
    (SEARCH_RESULT, "results.id", {"results": [{"id": "s1", "title": "t"}], "meta": {}}, False),  # a list, by name
    (SEARCH_RESULT, "results.0.url", {"results": [{"id": "s1", "title": "t"}], "meta": {}}, False),
    (SEARCH_RESULT, "meta.anything", {"results": [], "meta": {"anything": 1}}, True),  # says nothing: open
    (OPEN_TAIL, "a.whatever", {"a": {"whatever": 1}}, True),
    (TYPED_EXTRA, "k.x", {"k": {"x": 1}}, True),
    (TYPED_EXTRA, "k.y", {"k": {"x": 1, "y": 2}}, False),
]


@pytest.mark.parametrize("schema, path, value, accepted", CASES)
def test_the_check(schema, path, value, accepted):
    reason = result_path_error(schema, path)
    assert (reason is None) is accepted, reason


@pytest.mark.parametrize("schema, path, value, accepted", [c for c in CASES if c[3]])
def test_an_accepted_path_is_one_the_resolver_finds(schema, path, value, accepted):
    """The check and the run-time resolver share one grammar (`core/result_path.py`); a path the
    check accepts must be one `resolve_path` finds on a result that conforms."""
    assert resolve_path(value, path) is not MISSING


def test_the_refusal_names_what_the_tool_returns():
    assert result_path_error(RESEARCH_RESULT, "results") == "the result has no 'results'; it returns {raw_result}"
    assert "is a list; index it by number" in result_path_error(SEARCH_RESULT, "results.id")
    assert result_path_error({"type": ["string", "null"]}, "x") is None, "a union type says nothing checkable"


# ── validate_plan_references ─────────────────────────────────────────────────────────────────


def _plan(*steps):
    return {"steps": list(steps), "overall_risk": "low"}


def test_the_615b67ea_plan_is_refused_before_step_0_runs(tools):
    plan = _plan(
        {"tool": tools["research"], "args": {"query": "seo"}},
        {"tool": tools["search"], "args": {"query": "seo"}},
        {"tool": tools["write"], "args": {"content": {REF: 0, "path": "results"}}},
    )
    errors = validate_plan_references(plan)
    assert len(errors) == 1, errors
    assert "step 2 args.content: path 'results' does not exist in step 0" in errors[0]
    assert tools["research"] in errors[0] and "{raw_result}" in errors[0]


def test_the_19dcf508_plan_is_accepted(tools):
    plan = _plan(
        {"tool": tools["research"], "args": {"query": "seo"}},
        {"tool": tools["search"], "args": {"query": "seo"}},
        {"tool": tools["write"], "args": {"content": {REF: 0, "path": "raw_result"},
                                          "tags": [{REF: 1, "path": "results.0.title"}]}},
    )
    assert validate_plan_references(plan) == []


def test_a_tool_that_declares_nothing_is_checked_for_form_only(tools):
    plan = _plan({"tool": tools["bare"], "args": {}},
                 {"tool": tools["write"], "args": {"content": {REF: 0, "path": "anything.at.all"}}})
    assert validate_plan_references(plan) == []
    plan["steps"][1]["args"]["content"] = {REF: 1, "path": "x"}
    assert validate_plan_references(plan), "the DEC-073 form check still runs"


def test_a_whole_result_reference_needs_no_path_check(tools):
    plan = _plan({"tool": tools["research"], "args": {}},
                 {"tool": tools["write"], "args": {"content": {REF: 0}}})
    assert validate_plan_references(plan) == []


def test_wait_steps_take_no_index_for_the_schema_lookup_either(tools):
    """Ordinal 1 is `research` (the WAIT is not counted); a wrong ordinal would look up `search`
    and accept `results`."""
    plan = _plan({"tool": tools["search"], "args": {}}, {"wait_for": "approval.received"},
                 {"tool": tools["research"], "args": {}},
                 {"tool": tools["write"], "args": {"content": {REF: 1, "path": "results"}}})
    assert validate_plan_references(plan), "ordinal 1 is research, which has no 'results'"
    plan["steps"][3]["args"]["content"] = {REF: 1, "path": "raw_result"}
    assert validate_plan_references(plan) == []


# ── through the real `generate_plan` (DEC-078) ───────────────────────────────────────────────

from tests.unit.test_agent_planning_contract import clean_agent_planner_registry  # noqa: E402,F401


def _generate(monkeypatch, plan):
    from AINDY.agents.agent_runtime.planning import generate_plan
    from AINDY.config import settings
    from AINDY.platform_layer import registry

    seen: dict = {}

    def backend(request):
        seen["system_prompt"] = request.system_prompt
        seen["calls"] = seen.get("calls", 0) + 1
        return plan

    registry.register_agent_planner_backend("fr48_backend", backend)
    monkeypatch.setattr(settings, "AINDY_AGENT_PLANNER_BACKEND", "fr48_backend")
    return generate_plan(objective="research then use it", user_id="user-1", db=object()), seen


def _planned(tools, path):
    return {"executive_summary": "s", "overall_risk": "low", "steps": [
        {"tool": tools["research"], "args": {"q": "seo"}, "risk_level": "low", "description": "r"},
        {"tool": tools["write"], "args": {"content": {REF: 0, "path": path}}, "risk_level": "low", "description": "w"},
    ]}


def test_generate_plan_refuses_a_path_the_tool_does_not_return(
    clean_agent_planner_registry, monkeypatch, tools,  # noqa: F811
):
    from AINDY.agents.agent_runtime.shared import get_runtime_compat_module

    monkeypatch.setenv("AINDY_PLAN_STEP_REFERENCES", "1")
    plan, seen = _generate(monkeypatch, _planned(tools, "results"))
    assert plan is None, "a path into a key the tool does not return was accepted"
    assert "does not exist in step 0" in str(get_runtime_compat_module()._plan_failure.reason)
    assert seen["calls"] == 1, "DEC-078: a refused plan fails; it is not re-planned"


def test_generate_plan_accepts_the_declared_path(clean_agent_planner_registry, monkeypatch, tools):  # noqa: F811
    monkeypatch.setenv("AINDY_PLAN_STEP_REFERENCES", "1")
    plan, _seen = _generate(monkeypatch, _planned(tools, "raw_result"))
    assert plan is not None and plan["steps"][1]["args"]["content"] == {REF: 0, "path": "raw_result"}


def test_generate_plan_flag_off_checks_nothing(clean_agent_planner_registry, monkeypatch, tools):  # noqa: F811
    monkeypatch.setenv("AINDY_PLAN_STEP_REFERENCES", "0")
    plan, _seen = _generate(monkeypatch, _planned(tools, "results"))
    assert plan is not None, "flag off must be today's behaviour"
