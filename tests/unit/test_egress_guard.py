"""MEB-2b — socket-level egress guard (ECOGAP-4 / G4a strong, in-process form).

The guard wraps socket.getaddrinfo and denies hostname resolution outside the active
allowlist — catching runtime-built URLs that MEB-2a's static arg inspection misses.
Inert unless an egress_scope allowlist is set. No real DNS: the original resolver is stubbed.
"""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from AINDY.platform_layer import egress_guard as eg
from AINDY.agents import tool_registry as tr

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _reset_guard():
    """The guard is a process-wide install — reset it around every test."""
    orig = socket.getaddrinfo
    eg._installed = False
    eg._orig_getaddrinfo = None
    yield
    socket.getaddrinfo = orig
    eg._installed = False
    eg._orig_getaddrinfo = None


def _install_with_stub():
    """Install the guard, then swap its original resolver for a no-network stub.

    socket.getaddrinfo becomes the guard (which delegates to the stub for allowed hosts),
    so no real DNS happens.
    """
    eg.install_egress_guard()
    stub_calls = []
    eg._orig_getaddrinfo = lambda *a, **k: stub_calls.append(a[0]) or [("stub",)]
    return stub_calls


def test_guard_inert_without_scope():
    stub_calls = _install_with_stub()
    # No egress_scope active → contextvar None → passes straight through to the stub.
    socket.getaddrinfo("anything.com", 80)
    assert stub_calls == ["anything.com"]


def test_guard_denies_host_outside_allowlist():
    _install_with_stub()
    with eg.egress_scope(["allowed.com"]):
        with pytest.raises(eg.EgressDenied):
            socket.getaddrinfo("evil.com", 80)


def test_guard_allows_host_in_allowlist_and_subdomains():
    stub_calls = _install_with_stub()
    with eg.egress_scope(["allowed.com"]):
        socket.getaddrinfo("allowed.com", 80)
        socket.getaddrinfo("api.allowed.com", 443)  # subdomain allowed
    assert stub_calls == ["allowed.com", "api.allowed.com"]


def test_scope_resets_after_block():
    stub_calls = _install_with_stub()
    with eg.egress_scope(["allowed.com"]):
        pass
    # Outside the block the guard is inert again.
    socket.getaddrinfo("evil.com", 80)
    assert "evil.com" in stub_calls


def test_enforcement_flag(monkeypatch):
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "true")
    assert eg.egress_enforcement_enabled() is True
    monkeypatch.delenv("AINDY_EGRESS_ENFORCEMENT", raising=False)
    assert eg.egress_enforcement_enabled() is False


def test_execute_tool_blocks_runtime_built_egress(monkeypatch):
    """The MEB-2b payoff: a tool that builds a URL at runtime (host NOT in its args) and
    egresses to a disallowed host is blocked — the gap MEB-2a could not close."""
    from AINDY.agents import capability_policy as cp

    cp.clear_capability_policies()
    cp.register_capability_policy("outbound.http", cp.CapabilityPolicy(domains=("allowed.com",)))
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "true")
    _install_with_stub()

    # The tool resolves a host built at call time — nothing in `args` reveals it, so MEB-2a's
    # static inspection sees nothing to block; MEB-2b catches it at resolution.
    def _fn(args, user_id, db):
        socket.getaddrinfo("evil.com", 443)
        return {"ok": True}

    tr.TOOL_REGISTRY["egress_tool"] = {
        "fn": _fn, "risk": "high", "description": "t", "capability": "outbound.http",
        "required_capability": "outbound.http", "category": "test", "egress_scope": "web",
    }
    try:
        with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch(
            "AINDY.agents.capability_service.check_tool_capability",
            return_value={"ok": True, "allowed_capabilities": ["outbound.http"], "granted_tools": ["egress_tool"]},
        ), patch(
            "AINDY.agents.capability_service._get_capabilities_for_tool", return_value=["outbound.http"]
        ), patch.object(tr, "queue_system_event", lambda **k: None):
            result = tr.execute_tool(
                "egress_tool", {"note": "no url here"}, "user-1", MagicMock(),
                run_id="run-1", execution_token={"t": 1},
            )
    finally:
        tr.TOOL_REGISTRY.pop("egress_tool", None)
        cp.clear_capability_policies()

    assert result["success"] is False
    assert "egress" in result["error"].lower()
