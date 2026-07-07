"""AGENT-HARDEN-8 (PR1) — declarative per-capability policy: recipient/domain allowlists."""
from __future__ import annotations

import pytest

from AINDY.agents.capability_policy import (
    CapabilityPolicy,
    clear_capability_policies,
    enforce_capability_policy,
    enforce_capability_rate,
    extract_domains,
    extract_recipients,
    has_capability_policies,
    parse_rate,
    register_capability_policy,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_capability_policies()
    yield
    clear_capability_policies()


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

def test_extract_recipients_and_domains_from_nested_args():
    args = {"to": "Alice <a@x.com>", "cc": ["b@y.io"], "body": "see https://api.z.org/v1"}
    assert extract_recipients(args) == {"a@x.com", "b@y.io"}
    assert extract_domains(args) == {"api.z.org"}


# --------------------------------------------------------------------------- #
# enforce_capability_policy
# --------------------------------------------------------------------------- #

def test_vacuous_when_no_policy():
    assert has_capability_policies() is False
    result = enforce_capability_policy(["send_email"], {"to": "anyone@anywhere.com"})
    assert result == {"allowed": True, "violations": []}


def test_recipient_allowlist_allows_and_denies():
    register_capability_policy("send", CapabilityPolicy(recipients=("ok@example.com", "@partner.com")))
    assert enforce_capability_policy(["send"], {"to": "ok@example.com"})["allowed"] is True
    assert enforce_capability_policy(["send"], {"to": "anyone@partner.com"})["allowed"] is True  # @domain

    bad = enforce_capability_policy(["send"], {"to": "evil@bad.com"})
    assert bad["allowed"] is False
    assert bad["violations"][0] == {"capability": "send", "kind": "recipient", "value": "evil@bad.com"}


def test_domain_allowlist_allows_subdomain_and_denies_others():
    register_capability_policy("fetch", CapabilityPolicy(domains=("example.com",)))
    assert enforce_capability_policy(["fetch"], {"url": "https://example.com/a"})["allowed"] is True
    assert enforce_capability_policy(["fetch"], {"url": "https://api.example.com/a"})["allowed"] is True  # subdomain
    bad = enforce_capability_policy(["fetch"], {"url": "https://evil.net/x"})
    assert bad["allowed"] is False
    assert bad["violations"][0]["kind"] == "domain" and bad["violations"][0]["value"] == "evil.net"


def test_only_policy_bound_capabilities_are_checked():
    register_capability_policy("send", CapabilityPolicy(recipients=("ok@example.com",)))
    # 'other' has no policy → its presence doesn't constrain anything.
    result = enforce_capability_policy(["other"], {"to": "evil@bad.com"})
    assert result["allowed"] is True


def test_rate_only_policy_is_not_enforced_here():
    # rate is enforced in PR2 (Redis); a rate-only policy imposes no recipient/domain bound.
    register_capability_policy("send", CapabilityPolicy(rate="30/minute"))
    assert enforce_capability_policy(["send"], {"to": "evil@bad.com"})["allowed"] is True


# --------------------------------------------------------------------------- #
# Rate limits (PR2)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("30/minute", (30, 60)),
    ("5/s", (5, 1)),
    ("2/second", (2, 1)),
    ("100/hour", (100, 3600)),
    ("10/day", (10, 86400)),
    ("bad", None),
    ("0/minute", None),
    ("5/fortnight", None),
    ("", None),
])
def test_parse_rate(raw, expected):
    assert parse_rate(raw) == expected


def test_rate_limit_hit_fixed_window_is_deterministic():
    from AINDY.kernel.resource_manager import ResourceManager

    rm = ResourceManager()
    # Same window (now fixed) → counts accumulate; 3rd hit exceeds a limit of 2.
    assert rm.rate_limit_hit("k", limit=2, window_secs=60, now=1000.0) == (1, False)
    assert rm.rate_limit_hit("k", limit=2, window_secs=60, now=1001.0) == (2, False)
    assert rm.rate_limit_hit("k", limit=2, window_secs=60, now=1002.0) == (3, True)
    # New window resets the count.
    assert rm.rate_limit_hit("k", limit=2, window_secs=60, now=1099.0) == (1, False)


def test_enforce_rate_allows_then_denies(monkeypatch):
    from AINDY.kernel.resource_manager import ResourceManager

    rm = ResourceManager()
    monkeypatch.setattr("AINDY.kernel.resource_manager.get_resource_manager", lambda: rm)
    register_capability_policy("send", CapabilityPolicy(rate="2/minute"))

    assert enforce_capability_rate(["send"], scope="userA")["allowed"] is True
    assert enforce_capability_rate(["send"], scope="userA")["allowed"] is True
    denied = enforce_capability_rate(["send"], scope="userA")
    assert denied["allowed"] is False
    v = denied["violations"][0]
    assert v["kind"] == "rate" and v["capability"] == "send" and v["limit"] == 2

    # A different tenant scope has its own window.
    assert enforce_capability_rate(["send"], scope="userB")["allowed"] is True


def test_enforce_rate_vacuous_without_rate_policy(monkeypatch):
    register_capability_policy("send", CapabilityPolicy(recipients=("ok@x.com",)))  # no rate
    assert enforce_capability_rate(["send"], scope="u") == {"allowed": True, "violations": []}


# --------------------------------------------------------------------------- #
# execute_tool integration
# --------------------------------------------------------------------------- #

def _wire_tool(monkeypatch, *, cap, tool_fn):
    from AINDY.agents import tool_registry as tr

    monkeypatch.setattr(tr, "_ensure_tools_loaded", lambda: None)
    monkeypatch.setattr(tr, "queue_system_event", lambda **kw: None)
    monkeypatch.setitem(tr.TOOL_REGISTRY, "send_email", {"fn": tool_fn})
    monkeypatch.setattr("AINDY.agents.capability_service.check_tool_capability", lambda **kw: {"ok": True})
    monkeypatch.setattr("AINDY.agents.capability_service._get_capabilities_for_tool", lambda name: [cap])


def test_execute_tool_denies_policy_violation(monkeypatch):
    from AINDY.agents.tool_registry import execute_tool

    register_capability_policy("send_email_cap", CapabilityPolicy(recipients=("ok@example.com",)))
    _wire_tool(monkeypatch, cap="send_email_cap", tool_fn=lambda **kw: pytest.fail("must not execute on denial"))

    result = execute_tool(
        "send_email", {"to": "evil@bad.com"}, user_id="u", db=object(),
        run_id="r", execution_token={"token_hash": "h"},
    )
    assert result["success"] is False
    assert "capability policy violation" in result["error"]
    assert "evil@bad.com" in result["error"]


def test_execute_tool_allows_compliant_call(monkeypatch):
    from AINDY.agents.tool_registry import execute_tool

    register_capability_policy("send_email_cap", CapabilityPolicy(recipients=("ok@example.com",)))
    _wire_tool(monkeypatch, cap="send_email_cap", tool_fn=lambda **kw: {"sent": True})

    result = execute_tool(
        "send_email", {"to": "ok@example.com"}, user_id="u", db=object(),
        run_id="r", execution_token={"token_hash": "h"},
    )
    assert result["success"] is True and result["result"] == {"sent": True}


def test_execute_tool_enforces_rate_limit(monkeypatch):
    from AINDY.agents.tool_registry import execute_tool
    from AINDY.kernel.resource_manager import ResourceManager

    rm = ResourceManager()
    monkeypatch.setattr("AINDY.kernel.resource_manager.get_resource_manager", lambda: rm)
    register_capability_policy("send_email_cap", CapabilityPolicy(rate="1/minute"))
    calls = []
    _wire_tool(monkeypatch, cap="send_email_cap", tool_fn=lambda **kw: calls.append(1) or {"sent": True})

    first = execute_tool(
        "send_email", {}, user_id="u", db=object(), run_id="r", execution_token={"token_hash": "h"},
    )
    assert first["success"] is True
    second = execute_tool(
        "send_email", {}, user_id="u", db=object(), run_id="r", execution_token={"token_hash": "h"},
    )
    assert second["success"] is False and "rate limit exceeded" in second["error"]
    assert len(calls) == 1  # the over-limit call never reached the tool


def test_execute_tool_unchanged_when_no_policy(monkeypatch):
    from AINDY.agents.tool_registry import execute_tool

    # No policy registered → enforcement is skipped entirely (has_capability_policies False).
    _wire_tool(monkeypatch, cap="send_email_cap", tool_fn=lambda **kw: {"sent": True})
    result = execute_tool(
        "send_email", {"to": "evil@bad.com"}, user_id="u", db=object(),
        run_id="r", execution_token={"token_hash": "h"},
    )
    assert result["success"] is True  # unconstrained
