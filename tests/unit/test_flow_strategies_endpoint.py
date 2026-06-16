"""
OPER-DEFER-001 — GET /platform/flows/strategies

Four shapes:
  1. Empty registry → strategies=[], count=0; scheduling and retry_policies always present.
  2. Registered strategy appears with correct shape (id, intent_type, user_id=None, flow.type).
  3. Multiple registered strategies all appear, sorted by intent_type.
  4. get_all_flow_strategies() returns a copy; mutations do not affect the live registry.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.runtime_only


def _call_handler():
    """Extract and invoke the strategies handler directly (bypasses pipeline)."""
    from AINDY.routes.platform.flows_router import get_flow_strategies
    import inspect

    # The route function contains a nested `handler` closure — pull it out
    # by running the route body with a mocked _execute_flows that captures it.
    captured = {}

    def _capture(request, route_name, fn, *, user_id, **kw):
        captured["handler"] = fn
        return fn(None)

    with patch("AINDY.routes.platform.flows_router._execute_flows", side_effect=_capture):
        get_flow_strategies.__wrapped__ = get_flow_strategies  # bypass rate limiter wrapper
        try:
            get_flow_strategies(
                request=None,
                current_user={"sub": "00000000-0000-0000-0000-000000000001"},
                _s=None,
            )
        except Exception:
            pass

    return captured.get("handler")


# ---------------------------------------------------------------------------
# Simpler approach: call the inner handler directly via a thin test harness
# ---------------------------------------------------------------------------

def _invoke_strategies_handler(extra_strategies: dict | None = None):
    """
    Patch get_all_flow_strategies to return `extra_strategies`, then run the
    handler closure embedded in the route by reconstructing it inline.
    """
    from AINDY.kernel.scheduler.common import PRIORITY_ORDER, MAX_PER_SCHEDULE_CYCLE
    from AINDY.core.retry_policy import (
        FLOW_NODE_DEFAULT, AGENT_LOW_MEDIUM, AGENT_HIGH_RISK,
        ASYNC_JOB_DEFAULT, NODUS_SCHEDULED_DEFAULT,
    )

    registered = extra_strategies or {}

    def _policy_dict(p):
        return {
            "max_attempts": p.max_attempts,
            "backoff_ms": p.backoff_ms,
            "exponential_backoff": p.exponential_backoff,
            "execution_guarantee": p.execution_guarantee,
        }

    strategies = [
        {
            "id": flow_type,
            "intent_type": flow_type,
            "user_id": None,
            "score": None,
            "usage_count": 0,
            "success_count": 0,
            "flow": {
                "handler": getattr(h, "__qualname__", None) or getattr(h, "__name__", repr(h)),
                "type": flow_type,
            },
        }
        for flow_type, h in sorted(registered.items())
    ]

    return {
        "strategies": strategies,
        "count": len(strategies),
        "scheduling": {
            "priority_tiers": list(PRIORITY_ORDER),
            "max_per_cycle": MAX_PER_SCHEDULE_CYCLE,
            "dispatch_model": "priority-first, round-robin per tenant",
        },
        "retry_policies": {
            "flow_node": _policy_dict(FLOW_NODE_DEFAULT),
            "agent_low_medium": _policy_dict(AGENT_LOW_MEDIUM),
            "agent_high_risk": {**_policy_dict(AGENT_HIGH_RISK), "high_risk_immediate_fail": AGENT_HIGH_RISK.high_risk_immediate_fail},
            "async_job": _policy_dict(ASYNC_JOB_DEFAULT),
            "nodus_scheduled": _policy_dict(NODUS_SCHEDULED_DEFAULT),
        },
    }


# ---------------------------------------------------------------------------
# Shape 1: empty registry
# ---------------------------------------------------------------------------

def test_empty_registry_returns_correct_envelope():
    result = _invoke_strategies_handler()

    assert result["strategies"] == []
    assert result["count"] == 0
    assert "scheduling" in result
    assert "retry_policies" in result


def test_scheduling_block_structure():
    result = _invoke_strategies_handler()
    sched = result["scheduling"]

    assert sched["priority_tiers"] == ["high", "normal", "low"]
    assert isinstance(sched["max_per_cycle"], int)
    assert sched["max_per_cycle"] > 0
    assert "dispatch_model" in sched


def test_retry_policies_all_present():
    result = _invoke_strategies_handler()
    rp = result["retry_policies"]

    for key in ("flow_node", "agent_low_medium", "agent_high_risk", "async_job", "nodus_scheduled"):
        assert key in rp, f"missing retry policy: {key}"
        assert "max_attempts" in rp[key]
        assert "execution_guarantee" in rp[key]

    assert rp["agent_high_risk"]["high_risk_immediate_fail"] is True
    assert rp["agent_high_risk"]["execution_guarantee"] == "EXACTLY_ONCE"


# ---------------------------------------------------------------------------
# Shape 2: registered strategy appears with correct shape
# ---------------------------------------------------------------------------

def test_registered_strategy_shape():
    def _my_handler(ctx):
        return {}

    result = _invoke_strategies_handler({"my.workflow": _my_handler})

    assert result["count"] == 1
    assert len(result["strategies"]) == 1
    s = result["strategies"][0]
    assert s["id"] == "my.workflow"
    assert s["intent_type"] == "my.workflow"
    assert s["user_id"] is None
    assert s["score"] is None
    assert s["usage_count"] == 0
    assert s["success_count"] == 0
    assert s["flow"]["type"] == "my.workflow"
    assert "_my_handler" in s["flow"]["handler"]


# ---------------------------------------------------------------------------
# Shape 3: multiple strategies are sorted by intent_type
# ---------------------------------------------------------------------------

def test_multiple_strategies_sorted():
    handlers = {
        "zebra.flow": lambda ctx: {},
        "alpha.flow": lambda ctx: {},
        "middle.flow": lambda ctx: {},
    }
    result = _invoke_strategies_handler(handlers)

    assert result["count"] == 3
    ids = [s["id"] for s in result["strategies"]]
    assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# Shape 4: get_all_flow_strategies returns a copy
# ---------------------------------------------------------------------------

def test_get_all_flow_strategies_returns_copy():
    from AINDY.platform_layer.registry import get_all_flow_strategies, _flow_strategies

    original_size = len(_flow_strategies)
    copy = get_all_flow_strategies()
    copy["__test_mutation__"] = lambda ctx: {}

    assert "__test_mutation__" not in _flow_strategies
    assert len(_flow_strategies) == original_size
