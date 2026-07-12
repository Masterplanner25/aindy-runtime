"""MEB-2a — config-driven capability-policy + secret-scope activation (ECOGAP-4 / G4a thin).

Covers the new config loaders (parse → register → the dormant gates go live) and proves the
activated enforcement actually denies an out-of-allowlist egress through execute_tool.
"""
from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from AINDY.agents import capability_policy as cp
from AINDY.platform_layer import secret_broker as sb
from AINDY.agents import tool_registry as tr

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _clean():
    cp.clear_capability_policies()
    sb.clear_secret_scopes()
    tr._GOVERNANCE_LOADED = False
    saved = dict(tr.TOOL_REGISTRY)
    yield
    cp.clear_capability_policies()
    sb.clear_secret_scopes()
    tr._GOVERNANCE_LOADED = False
    tr.TOOL_REGISTRY.clear()
    tr.TOOL_REGISTRY.update(saved)


# ── config loaders ────────────────────────────────────────────────────────────

def test_load_capability_policies_registers_and_activates():
    assert cp.has_capability_policies() is False
    n = cp.load_capability_policies_from_env(
        '{"outbound.http": {"domains": ["api.ok.com"], "rate": "30/minute"}, '
        '"email.send": {"recipients": ["@ok.com"]}}'
    )
    assert n == 2
    assert cp.has_capability_policies() is True  # dormant gate now live
    pol = cp.get_capability_policy("outbound.http")
    assert pol.domains == ("api.ok.com",)
    assert pol.rate == "30/minute"
    assert cp.get_capability_policy("email.send").recipients == ("@ok.com",)


@pytest.mark.parametrize("bad", ["not json", "[1,2]", "", "{}"])
def test_load_capability_policies_bad_config_is_noop(bad):
    assert cp.load_capability_policies_from_env(bad) == 0
    assert cp.has_capability_policies() is False


def test_load_secret_scopes_registers():
    assert sb.load_secret_scopes_from_env('{"STRIPE_KEY": "billing.charge"}') == 1
    assert sb.SECRET_SCOPES.get("STRIPE_KEY") == "billing.charge"


def test_governance_hook_memoized_and_noop_without_config(monkeypatch):
    monkeypatch.delenv("AINDY_CAPABILITY_POLICIES", raising=False)
    monkeypatch.delenv("AINDY_SECRET_SCOPES", raising=False)
    calls = {"n": 0}
    with patch.object(cp, "load_capability_policies_from_env",
                      lambda: calls.__setitem__("n", calls["n"] + 1)):
        tr._ensure_capability_governance()
        tr._ensure_capability_governance()
    assert calls["n"] == 1  # memoized
    assert cp.has_capability_policies() is False  # no config → nothing registered


def test_governance_hook_loads_from_env(monkeypatch):
    monkeypatch.setenv("AINDY_CAPABILITY_POLICIES", '{"outbound.http": {"domains": ["ok.com"]}}')
    monkeypatch.setenv("AINDY_SECRET_SCOPES", '{"K": "billing.charge"}')
    tr._GOVERNANCE_LOADED = False
    tr._ensure_capability_governance()
    assert cp.has_capability_policies() is True
    assert cp.get_capability_policy("outbound.http").domains == ("ok.com",)
    assert sb.SECRET_SCOPES.get("K") == "billing.charge"


# ── activated enforcement (the point of MEB-2a) ────────────────────────────────

def test_registered_domain_policy_denies_out_of_allowlist():
    cp.register_capability_policy("outbound.http", cp.CapabilityPolicy(domains=("allowed.com",)))
    good = cp.enforce_capability_policy(["outbound.http"], {"url": "https://allowed.com/x"})
    bad = cp.enforce_capability_policy(["outbound.http"], {"url": "https://evil.com/x"})
    assert good["allowed"] is True
    assert bad["allowed"] is False
    assert bad["violations"][0]["kind"] == "domain"


def _run_tool(args, tool_caps):
    """Drive execute_tool past auth with a policy-bound tool; returns the result dict."""
    tr.TOOL_REGISTRY["egress_tool"] = {
        "fn": lambda args, user_id, db: {"sent": args},
        "risk": "high", "description": "t", "capability": "outbound.http",
        "required_capability": "outbound.http", "category": "test", "egress_scope": "web",
    }
    with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": tool_caps, "granted_tools": ["egress_tool"]},
    ), patch(
        "AINDY.agents.capability_service._get_capabilities_for_tool", return_value=tool_caps
    ), patch.object(tr, "queue_system_event", lambda **k: None), patch(
        "AINDY.platform_layer.secret_broker.capability_scope", lambda caps: contextlib.nullcontext()
    ):
        return tr.execute_tool("egress_tool", args, "user-1", MagicMock(),
                               run_id="run-1", execution_token={"t": 1})


def test_execute_tool_denies_egress_outside_registered_domain_allowlist():
    cp.register_capability_policy("outbound.http", cp.CapabilityPolicy(domains=("allowed.com",)))
    denied = _run_tool({"url": "https://evil.com/x"}, ["outbound.http"])
    assert denied["success"] is False
    allowed = _run_tool({"url": "https://allowed.com/x"}, ["outbound.http"])
    assert allowed["success"] is True
    assert allowed["result"] == {"sent": {"url": "https://allowed.com/x"}}


def test_execute_tool_unaffected_when_no_policy_registered():
    # No policy → has_capability_policies() False → enforcement block skipped entirely.
    assert cp.has_capability_policies() is False
    result = _run_tool({"url": "https://anywhere.com/x"}, ["outbound.http"])
    assert result["success"] is True
