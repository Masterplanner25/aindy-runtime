"""`RETRY-CLASSIFY-1` phase 1 — a failure carries a CLASS set at the raising site; the substring
table is a recorded fallback, never the decision.

Design: ``docs/design/RETRY_CLASSIFICATION_AND_CONTEXT_DESIGN.md`` §3–§5.

Written against the unfixed code first. Red before the fix: the census (no ``failure_class`` on
any `execute_tool` refusal), the three own-string misclassifications (§3 of the design —
cancelled / missing token / enforcement crashed all read RETRY), and the dict-accepting
classifier. The substring-fallback tests are green on both sides on purpose: they pin that the
fallback did not change, so the default flip changes nothing for an un-classed string.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

from AINDY.core import retry_policy
from AINDY.core.retry_policy import (
    FAILURE_CLASSES,
    FailureRecord,
    classify_failure,
    is_retryable_error,
)

pytestmark = pytest.mark.runtime_only

_TOOL_REGISTRY_PATH = pathlib.Path(retry_policy.__file__).resolve().parents[1] / "agents" / "tool_registry.py"


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------

def test_failure_classes_are_the_designed_six():
    assert FAILURE_CLASSES == frozenset({"transient", "cancelled", "permission", "not_found", "invalid", "fatal"})


def test_site_class_wins_over_the_substring_table():
    """A site that says `transient` is believed even when the text matches the table."""
    rec = classify_failure({"success": False, "error": "HTTP 404 from upstream", "failure_class": "transient"}, site="t")
    assert rec.failure_class == "transient"
    assert rec.classified_by == "site"
    assert is_retryable_error({"error": "HTTP 404 from upstream", "failure_class": "transient"}) is True


def test_unknown_site_class_falls_back_and_says_so():
    rec = classify_failure({"error": "boom", "failure_class": "made_up"}, site="t")
    assert rec.classified_by == "default"
    assert rec.failure_class == "transient"


@pytest.mark.parametrize("text, expected", [
    ("HTTP 404 not found", "not_found"),
    ("permission denied", "permission"),
    ("unauthorized", "permission"),
    ("Input validation failed: invalid", "invalid"),
    ("blocked by policy", "permission"),
])
def test_substring_fallback_is_unchanged_and_recorded(text, expected):
    """The table still decides un-classed strings — and the record says the table did."""
    rec = classify_failure(text, site="t")
    assert rec.failure_class == expected
    assert rec.classified_by == "substring"
    assert is_retryable_error(text) is False


def test_unmatched_string_defaults_to_transient():
    rec = classify_failure("connection timeout", site="t")
    assert (rec.failure_class, rec.classified_by) == ("transient", "default")
    assert is_retryable_error("connection timeout") is True
    assert is_retryable_error(None) is True
    assert is_retryable_error({}) is True


def test_record_is_frozen_and_carries_site_and_attempt():
    rec = classify_failure("x", site="flow_node", attempt=2)
    assert isinstance(rec, FailureRecord)
    assert (rec.site, rec.attempt, rec.error) == ("flow_node", 2, "x")
    with pytest.raises(Exception):
        rec.failure_class = "fatal"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ★ §3 — the runtime's OWN refusals, through the real entry point
# ---------------------------------------------------------------------------

def _refusal(**kwargs):
    from AINDY.agents.tool_registry import execute_tool
    return execute_tool(db=None, **kwargs)


def test_tool_not_in_registry_is_not_found_by_site_not_by_phrase():
    res = _refusal(tool_name="no-such-tool-" + uuid.uuid4().hex, args={}, user_id=str(uuid.uuid4()))
    assert res["success"] is False
    assert res["failure_class"] == "not_found"
    rec = classify_failure(res, site="tool_step")
    assert rec.classified_by == "site"
    assert is_retryable_error(res) is False


def test_missing_capability_token_is_permission_and_not_retried():
    """Design §3: today this string classifies RETRY and the adapter re-attempts it 3×."""
    from AINDY.agents import tool_registry

    name = "__retry_test_tool__" + uuid.uuid4().hex[:8]
    tool_registry.TOOL_REGISTRY[name] = {"fn": lambda args, ctx: {"ok": True}, "risk": "low"}
    try:
        res = _refusal(tool_name=name, args={}, user_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()))
    finally:
        tool_registry.TOOL_REGISTRY.pop(name, None)
    assert res["success"] is False
    assert "token" in res["error"]
    assert res["failure_class"] == "permission"
    assert is_retryable_error(res) is False


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **kw):
        return self

    def first(self):
        return self._result


class _FakeDB:
    """Enough Session for `agent_execute_step` (scaffolding from test_authority_negotiation_behaviour)."""

    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    def query(self, *a, **kw):
        return _FakeQuery(None)

    def commit(self):
        self.commits += 1


def _drive_adapter(monkeypatch, tool_result: dict) -> list[str]:
    """Run the REAL `agent_execute_step` retry loop with `execute_tool` returning `tool_result`."""
    from AINDY.runtime import nodus_adapter

    executed: list[str] = []

    def _execute_tool(*, tool_name, args, user_id, db, run_id, execution_token):
        executed.append(tool_name)
        return dict(tool_result)

    monkeypatch.setattr(nodus_adapter, "execute_tool", _execute_tool, raising=True)
    monkeypatch.setattr(nodus_adapter, "record_agent_event", lambda **kw: "evt", raising=True)
    monkeypatch.setattr(nodus_adapter, "queue_system_event", lambda **kw: "sys", raising=True)
    monkeypatch.setattr(nodus_adapter, "emit_system_event", lambda **kw: "sys", raising=True)
    monkeypatch.setattr(
        nodus_adapter, "check_tool_capability",
        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []},
        raising=True,
    )
    monkeypatch.setattr("AINDY.memory.memory_helpers.enrich_context", lambda ctx: ctx, raising=True)
    state = {
        "agent_run_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "00000000-0000-0000-0000-000000000002",
        "capability_token": {"sig": "x"},
        "correlation_id": "run_test",
        "steps": [{"tool": "t", "args": {}, "risk_level": "low", "description": "d"}],
        "current_step_index": 0,
        "step_results": [],
    }
    nodus_adapter.agent_execute_step(state, {"db": _FakeDB(), "trace_id": "t"})
    return executed


_CANCELLED_TEXT = "run r was cancelled; tool 't' not executed"


def test_adapter_stops_on_a_site_classified_cancellation(monkeypatch):
    """★ Design §3 — the live defect: a cancelled run's tool was re-attempted 3× (low risk).
    With the class on the dict, the real loop makes ONE attempt."""
    executed = _drive_adapter(monkeypatch, {
        "success": False, "result": None, "error": _CANCELLED_TEXT,
        "cancelled": True, "failure_class": "cancelled",
    })
    assert executed == ["t"], f"a cancelled run's tool was re-attempted: {executed}"


def test_adapter_control_the_string_alone_still_retries(monkeypatch):
    """Liveness control for the test above: the SAME text without a class falls back to the
    substring table, matches nothing, and is retried — proving the class, not the phrase,
    is what stopped the loop. (This is the fallback's honest behaviour, kept by design.)"""
    executed = _drive_adapter(monkeypatch, {
        "success": False, "result": None, "error": _CANCELLED_TEXT,
    })
    assert executed == ["t", "t", "t"]


def test_adapter_still_retries_a_transient_site_class(monkeypatch):
    executed = _drive_adapter(monkeypatch, {
        "success": False, "result": None, "error": "worker exceeded 30s", "failure_class": "transient",
    })
    assert executed == ["t", "t", "t"]


# ---------------------------------------------------------------------------
# ★ The census — derived from the source, non-empty asserted (variant 12)
# ---------------------------------------------------------------------------

def _failure_dict_literals(path: pathlib.Path) -> list[tuple[int, set[str]]]:
    """Every dict literal in the file carrying ``"success": False`` → (line, its string keys)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys: dict[str, ast.AST] = {}
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys[k.value] = v
        success = keys.get("success")
        if isinstance(success, ast.Constant) and success.value is False:
            found.append((node.lineno, set(keys)))
    return found


def test_every_execute_tool_refusal_declares_a_failure_class():
    literals = _failure_dict_literals(_TOOL_REGISTRY_PATH)
    assert len(literals) >= 10, "census is empty or implausibly small — the derivation broke"
    missing = [line for line, keys in literals if "failure_class" not in keys]
    assert not missing, f"`success: False` returns in tool_registry.py without `failure_class` at lines {missing}"


def test_every_declared_failure_class_in_tool_registry_is_a_known_class():
    tree = ast.parse(_TOOL_REGISTRY_PATH.read_text(encoding="utf-8"))
    declared = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "failure_class" and isinstance(v, ast.Constant):
                    declared.add(v.value)
    assert declared, "no failure_class literals found — census empty"
    assert declared <= FAILURE_CLASSES, declared - FAILURE_CLASSES


# ---------------------------------------------------------------------------
# The dispatcher envelope carries the class too
# ---------------------------------------------------------------------------

def test_dispatcher_error_envelope_carries_failure_class():
    from AINDY.kernel import syscall_registry
    from AINDY.kernel.syscall_dispatcher import get_dispatcher

    ctx = syscall_registry.SyscallContext(
        execution_unit_id="eu-retry-1", user_id=str(uuid.uuid4()), capabilities=[], trace_id="t",
    )
    env = get_dispatcher().dispatch("sys.v1.no.such_syscall", {}, ctx)
    assert env["status"] == "error"
    assert env["failure_class"] == "not_found"
    # and a refusal the dispatcher did not declare a class for is decided by the fallback
    from AINDY.core.retry_policy import classify_failure as _cf
    assert _cf(env, site="syscall").classified_by == "site"


# ---------------------------------------------------------------------------
# The operator signal
# ---------------------------------------------------------------------------

def test_classification_counter_moves_per_site_class_and_decision():
    from AINDY.platform_layer.metrics import REGISTRY, retry_classifications_total  # noqa: F401
    from AINDY.core.retry_policy import record_retry_classification

    rec = classify_failure({"error": "x", "failure_class": "cancelled"}, site="tool_step")
    before = REGISTRY.get_sample_value(
        "aindy_retry_classifications_total",
        {"site": "tool_step", "failure_class": "cancelled", "classified_by": "site", "decision": "stop"},
    ) or 0.0
    record_retry_classification(rec, decision="stop")
    after = REGISTRY.get_sample_value(
        "aindy_retry_classifications_total",
        {"site": "tool_step", "failure_class": "cancelled", "classified_by": "site", "decision": "stop"},
    )
    assert after == before + 1


# ---------------------------------------------------------------------------
# DEC: the unrunnable twin is gone
# ---------------------------------------------------------------------------

def test_execute_with_retry_is_deleted_not_taught():
    assert not hasattr(retry_policy, "execute_with_retry")
    assert not hasattr(retry_policy, "_execute_with_retry")
