from __future__ import annotations

import pytest

from AINDY.core.retry_policy import (
    AGENT_HIGH_RISK,
    AGENT_LOW_MEDIUM,
    ASYNC_JOB_DEFAULT,
    FLOW_NODE_DEFAULT,
    NO_RETRY,
    NODUS_SCHEDULED_DEFAULT,
    RetryPolicy,
    is_retryable_error,
)
from AINDY.core.execution_gate import _resolve_policy_for_eu, compute_action_id

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# NF-4: execution_guarantee field on RetryPolicy
# ---------------------------------------------------------------------------

def test_retry_policy_dataclass_has_execution_guarantee_field():
    policy = RetryPolicy(max_attempts=1)
    assert policy.execution_guarantee == "AT_LEAST_ONCE"


def test_agent_high_risk_has_exactly_once_guarantee():
    assert AGENT_HIGH_RISK.execution_guarantee == "EXACTLY_ONCE"


def test_all_other_named_constants_are_at_least_once():
    for constant in (FLOW_NODE_DEFAULT, AGENT_LOW_MEDIUM, ASYNC_JOB_DEFAULT, NODUS_SCHEDULED_DEFAULT, NO_RETRY):
        assert constant.execution_guarantee == "AT_LEAST_ONCE"


def test_resolve_policy_for_eu_includes_execution_guarantee():
    result = _resolve_policy_for_eu("agent", {"risk_level": "high"})
    assert "execution_guarantee" in result
    assert result["execution_guarantee"] == "EXACTLY_ONCE"


# ---------------------------------------------------------------------------
# NF-2: compute_action_id()
# ---------------------------------------------------------------------------

def test_compute_action_id_is_deterministic():
    h1 = compute_action_id("tool.call", {"key": "value"}, "scope-a")
    h2 = compute_action_id("tool.call", {"key": "value"}, "scope-a")
    assert h1 == h2


def test_compute_action_id_differs_on_different_action_type():
    h1 = compute_action_id("tool.call", {"key": "value"}, "scope-a")
    h2 = compute_action_id("tool.other", {"key": "value"}, "scope-a")
    assert h1 != h2


def test_compute_action_id_differs_on_different_input():
    h1 = compute_action_id("tool.call", {"key": "value"}, "scope-a")
    h2 = compute_action_id("tool.call", {"key": "different"}, "scope-a")
    assert h1 != h2


def test_compute_action_id_is_key_order_independent():
    h1 = compute_action_id("tool.call", {"a": 1, "b": 2}, "scope-a")
    h2 = compute_action_id("tool.call", {"b": 2, "a": 1}, "scope-a")
    assert h1 == h2


# ---------------------------------------------------------------------------
# NF-3: is_retryable_error()
# ---------------------------------------------------------------------------

def test_is_retryable_error_returns_false_for_404():
    assert is_retryable_error("HTTP 404 not found") is False


def test_is_retryable_error_returns_false_for_permission():
    assert is_retryable_error("permission denied") is False


def test_is_retryable_error_returns_true_for_timeout():
    assert is_retryable_error("connection timeout") is True


def test_is_retryable_error_returns_true_for_none():
    assert is_retryable_error(None) is True
