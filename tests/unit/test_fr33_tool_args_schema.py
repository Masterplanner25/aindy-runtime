"""FR-33 — a tool declares its argument contract; the planner sees it; `execute_tool` checks it.

Filed by the app 2026-09-16: the planner was told a tool's name and one sentence, never its
arguments, so it sent ``{"topic": …}`` to a tool that takes ``file_path`` and the run failed
inside a domain handler, after approval. Every test here drives the real function — the real
`register_tool` decorator, the real catalog builder, the real `execute_tool` — and each mode of
`AINDY_TOOL_ARGS_VALIDATION` is exercised with its liveness control (a VALID call under the
same mode dispatches).
"""
from __future__ import annotations

import uuid

import pytest

from AINDY.agents import tool_registry
from AINDY.agents.tool_registry import (
    ARGS_VALIDATION_ENV,
    args_validation_mode,
    execute_tool,
    register_tool,
    tool_args_schema,
    validate_tool_args,
)

pytestmark = pytest.mark.runtime_only

SCHEMA = {
    "type": "object",
    "properties": {"file_path": {"type": "string"}, "depth": {"type": "int"}},
    "required": ["file_path"],
}


@pytest.fixture
def tool():
    """A real registration through the decorator, removed after the test."""
    name = "__fr33_tool__" + uuid.uuid4().hex[:8]
    calls: list = []

    @register_tool(
        name, risk="low", description="Analyze a file", capability="analysis", required_capability="",
        category="test", egress_scope="", args_schema=SCHEMA,
    )
    def _impl(args, user_id, db):
        calls.append(dict(args))
        return {"ok": True}

    try:
        yield name, calls
    finally:
        tool_registry.TOOL_REGISTRY.pop(name, None)


# ---------------------------------------------------------------------------
# (1) declared at registration, surfaced on the tool dict
# ---------------------------------------------------------------------------

def test_the_schema_is_stored_and_surfaced(tool):
    name, _ = tool
    assert tool_args_schema(name) == SCHEMA
    assert tool_registry.TOOL_REGISTRY[name]["args_schema"] == SCHEMA
    from AINDY.platform_layer.runtime_agent_defaults import get_tools_for_run

    mine = [t for t in get_tools_for_run({}) if t["name"] == name]
    assert mine and mine[0]["args_schema"] == SCHEMA


def test_a_tool_that_declares_nothing_is_unchanged():
    name = "__fr33_plain__" + uuid.uuid4().hex[:8]
    register_tool(name, risk="low", description="d", capability="c", required_capability="", category="t", egress_scope="")(lambda args, user_id, db: None)
    try:
        assert tool_args_schema(name) is None
        assert tool_registry.TOOL_REGISTRY[name]["args_schema"] is None
        assert validate_tool_args(name, {"anything": 1}) == []
    finally:
        tool_registry.TOOL_REGISTRY.pop(name, None)


@pytest.mark.parametrize("bad, why", [
    ("not a dict", "must be a dict"),
    ({"properties": []}, "properties"),
    ({"required": "file_path"}, "required"),
    ({"properties": {"a": {}}, "required": ["b"]}, "does not declare"),
])
def test_a_malformed_schema_is_refused_at_registration(bad, why):
    with pytest.raises(ValueError, match=why):
        register_tool("__fr33_bad__", risk="low", description="d", capability="c", required_capability="", category="t", egress_scope="", args_schema=bad)


# ---------------------------------------------------------------------------
# (2) the planner's catalog renders it — from the dict, or from the registry when omitted
# ---------------------------------------------------------------------------

def test_the_catalog_line_carries_the_contract(tool):
    from AINDY.agents.agent_runtime.planning import _catalog_line

    name, _ = tool
    with_key = _catalog_line({"name": name, "description": "Analyze a file", "risk": "low", "args_schema": SCHEMA})
    without_key = _catalog_line({"name": name, "description": "Analyze a file", "risk": "low"})  # an app provider's dict
    assert with_key == without_key, "the registry is the contract's home; a provider that omits the key must not hide it"
    assert 'args={"properties":{"depth":{"type":"int"},"file_path":{"type":"string"}},"required":["file_path"],"type":"object"}' in with_key
    plain = _catalog_line({"name": "__no_such_fr33_tool__", "description": "d", "risk": "low"})
    assert "args=" not in plain


# ---------------------------------------------------------------------------
# (3) execute_tool checks BEFORE dispatch — three modes, each with its control
# ---------------------------------------------------------------------------

def _call(name, args):
    return execute_tool(name, args, str(uuid.uuid4()), None)


def test_default_mode_is_warn(monkeypatch):
    monkeypatch.delenv(ARGS_VALIDATION_ENV, raising=False)
    assert args_validation_mode() == "warn"
    monkeypatch.setenv(ARGS_VALIDATION_ENV, "nonsense")
    assert args_validation_mode() == "warn"  # a typo never refuses


def test_enforce_refuses_a_bad_plan_before_the_handler_runs(monkeypatch, tool):
    """The filed case: `{"topic": …}` to a tool that requires `file_path`, refused as `invalid`
    at the seam — the handler never sees it — and a valid call under the SAME mode dispatches."""
    from AINDY.core.retry_policy import is_retryable_error

    monkeypatch.setenv(ARGS_VALIDATION_ENV, "enforce")
    name, calls = tool
    res = _call(name, {"topic": "cost governor"})
    assert res["success"] is False
    assert res["failure_class"] == "invalid"
    assert "file_path" in res["error"]
    assert calls == [], "the handler ran on a call that violated the declared contract"
    assert is_retryable_error(res) is False  # composes with RETRY-CLASSIFY-1: never re-attempted
    ok = _call(name, {"file_path": "apps/arm/agents/tools.py"})           # control
    assert ok["success"] is True and calls == [{"file_path": "apps/arm/agents/tools.py"}]


def test_enforce_checks_types_too(monkeypatch, tool):
    monkeypatch.setenv(ARGS_VALIDATION_ENV, "enforce")
    name, calls = tool
    res = _call(name, {"file_path": "x", "depth": "three"})
    assert res["success"] is False and "depth" in res["error"]
    assert calls == []


def test_warn_dispatches_anyway_and_counts(monkeypatch, tool):
    """The shipped default: the mismatch is COUNTED (the number to read before flipping to
    enforce) and logged, and the call still dispatches — nothing changes for a deployment
    that has not opted in."""
    from AINDY.platform_layer.metrics import REGISTRY

    monkeypatch.setenv(ARGS_VALIDATION_ENV, "warn")
    name, calls = tool
    before = REGISTRY.get_sample_value("aindy_tool_args_validation_total", {"tool": name, "outcome": "invalid", "mode": "warn"}) or 0.0
    res = _call(name, {"topic": "cost governor"})
    assert res["success"] is True and calls == [{"topic": "cost governor"}]
    assert REGISTRY.get_sample_value("aindy_tool_args_validation_total", {"tool": name, "outcome": "invalid", "mode": "warn"}) == before + 1
    ok_before = REGISTRY.get_sample_value("aindy_tool_args_validation_total", {"tool": name, "outcome": "valid", "mode": "warn"}) or 0.0
    _call(name, {"file_path": "x"})
    assert REGISTRY.get_sample_value("aindy_tool_args_validation_total", {"tool": name, "outcome": "valid", "mode": "warn"}) == ok_before + 1


def test_off_neither_refuses_nor_counts(monkeypatch, tool):
    from AINDY.platform_layer.metrics import REGISTRY

    monkeypatch.setenv(ARGS_VALIDATION_ENV, "off")
    name, calls = tool
    before = REGISTRY.get_sample_value("aindy_tool_args_validation_total", {"tool": name, "outcome": "invalid", "mode": "off"}) or 0.0
    res = _call(name, {"topic": "x"})
    assert res["success"] is True and len(calls) == 1
    # `off` still counts (the count is how an operator learns what enforce would refuse)
    assert REGISTRY.get_sample_value("aindy_tool_args_validation_total", {"tool": name, "outcome": "invalid", "mode": "off"}) == before + 1


def test_the_check_runs_before_any_capability_event(monkeypatch, tool):
    """A malformed call with a token must be refused BEFORE `check_tool_capability` runs — no
    `capability.denied` / `capability.allowed` event for a call that was never well-formed."""
    monkeypatch.setenv(ARGS_VALIDATION_ENV, "enforce")
    name, calls = tool
    touched = []
    monkeypatch.setattr("AINDY.agents.capability_service.check_tool_capability", lambda **kw: touched.append(kw) or {"ok": True, "error": None}, raising=True)
    res = execute_tool(name, {"topic": "x"}, str(uuid.uuid4()), None, run_id="run-1", execution_token={"sig": "x"})
    assert res["failure_class"] == "invalid"
    assert touched == [] and calls == []


def test_every_refusal_still_declares_a_failure_class():
    """The RETRY-CLASSIFY-1 census must still cover the new refusal — run its walker here."""
    import ast
    import pathlib

    src = pathlib.Path(tool_registry.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if "success" in keys and any(
                isinstance(v, ast.Constant) and v.value is False
                for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value == "success"
            ):
                assert "failure_class" in keys, f"refusal at line {node.lineno} lacks failure_class"
