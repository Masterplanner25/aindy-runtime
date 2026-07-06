"""AGENT-HARDEN-6 — post-condition verifier logic (extract + evaluate)."""
from __future__ import annotations

import pytest

from AINDY.core.verifier import extract_post_conditions, verify_post_conditions

pytestmark = pytest.mark.runtime_only


def _results(*pairs):
    """pairs of (step_index, result_payload) → reconstructed step-result dicts."""
    return [
        {"step_index": i, "tool": "t", "status": "success", "result": r, "error": None}
        for i, r in pairs
    ]


# --------------------------------------------------------------------------- #
# extract_post_conditions — keys by tool-step ordinal, skips WAIT steps
# --------------------------------------------------------------------------- #

def test_extract_keys_by_tool_ordinal_skipping_waits():
    plan = {"steps": [
        {"tool": "a", "args": {}},                                   # ordinal 0 — no expects
        {"wait_for": "approval.received"},                            # WAIT — skipped, no index
        {"tool": "b", "args": {}, "expects": {"field": "ok", "op": "truthy"}},  # ordinal 1
    ]}
    pc = extract_post_conditions(plan)
    assert set(pc.keys()) == {1}
    assert pc[1] == [{"field": "ok", "op": "truthy"}]


def test_extract_normalizes_single_condition_to_list():
    plan = {"steps": [{"tool": "a", "expects": {"status": "success"}}]}
    assert extract_post_conditions(plan) == {0: [{"status": "success"}]}


def test_extract_empty_when_no_expects():
    assert extract_post_conditions({"steps": [{"tool": "a"}, {"tool": "b"}]}) == {}
    assert extract_post_conditions(None) == {}


# --------------------------------------------------------------------------- #
# verify_post_conditions — vacuous pass, ops, failures
# --------------------------------------------------------------------------- #

def test_no_conditions_is_vacuously_ok():
    v = verify_post_conditions({}, _results((0, {"x": 1})))
    assert v["ok"] is True and v["checked"] == 0 and v["failures"] == []


def test_status_shorthand():
    ok = verify_post_conditions({0: [{"status": "success"}]}, _results((0, {})))
    assert ok["ok"] is True and ok["checked"] == 1
    bad = verify_post_conditions(
        {0: [{"status": "success"}]},
        [{"step_index": 0, "status": "failed", "result": None, "error": "x"}],
    )
    assert bad["ok"] is False


@pytest.mark.parametrize("cond,payload,expected", [
    ({"field": "ok", "op": "truthy"}, {"ok": True}, True),
    ({"field": "ok", "op": "truthy"}, {"ok": 0}, False),
    ({"field": "ok", "op": "falsy"}, {"ok": 0}, True),
    ({"field": "n", "op": "eq", "value": 3}, {"n": 3}, True),
    ({"field": "n", "op": "eq", "value": 3}, {"n": 4}, False),
    ({"field": "n", "op": "ne", "value": 3}, {"n": 4}, True),
    ({"field": "n", "op": "gt", "value": 2}, {"n": 3}, True),
    ({"field": "n", "op": "gte", "value": 3}, {"n": 3}, True),
    ({"field": "n", "op": "lt", "value": 3}, {"n": 2}, True),
    ({"field": "n", "op": "lte", "value": 3}, {"n": 3}, True),
    ({"field": "msg", "op": "contains", "value": "ok"}, {"msg": "all ok"}, True),
    ({"field": "msg", "op": "not_contains", "value": "err"}, {"msg": "all ok"}, True),
    ({"field": "id", "op": "exists"}, {"id": "x"}, True),
    ({"field": "id", "op": "exists"}, {}, False),
    ({"field": "id", "op": "not_exists"}, {}, True),
    ({"field": "a.b", "op": "eq", "value": 1}, {"a": {"b": 1}}, True),  # dot path
])
def test_field_ops(cond, payload, expected):
    v = verify_post_conditions({0: [cond]}, _results((0, payload)))
    assert v["ok"] is expected


def test_failure_carries_step_index_and_reason():
    v = verify_post_conditions({0: [{"field": "n", "op": "eq", "value": 5}]}, _results((0, {"n": 1})))
    assert v["ok"] is False
    f = v["failures"][0]
    assert f["step_index"] == 0 and f["condition"]["op"] == "eq" and "!=" in f["reason"]


def test_unknown_op_fails_closed():
    v = verify_post_conditions({0: [{"field": "n", "op": "bogus"}]}, _results((0, {"n": 1})))
    assert v["ok"] is False and "unknown op" in v["failures"][0]["reason"]


def test_missing_step_result_fails():
    v = verify_post_conditions({2: [{"status": "success"}]}, _results((0, {})))
    assert v["ok"] is False and "did not run" in v["failures"][0]["reason"]


def test_type_incompatible_comparison_fails_closed():
    v = verify_post_conditions({0: [{"field": "s", "op": "gt", "value": 5}]}, _results((0, {"s": "abc"})))
    assert v["ok"] is False
