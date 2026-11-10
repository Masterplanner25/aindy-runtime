"""Which scopes the MCP OAuth layer offers, and what a scope authorizes (FR-66 phase 2, DEC-130).

An app tool's capability may have no scope named for it (the app's `work.context`), so an OAuth
grant could never reach it. A scope that names the capability exactly now authorizes it. Platform
keys are minted only with `Scopes.ALL`, and none of those is such a capability, so for keys
nothing changes; that is pinned too.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


def test_an_oauth_grant_naming_the_capability_exactly_is_authorized():
    from AINDY.auth.api_key_auth import key_scopes_authorize

    assert key_scopes_authorize("work.context", {"work.context"}, exact_capability=True) is True
    assert key_scopes_authorize("work.context", {"work.facts"}, exact_capability=True) is False  # control
    assert key_scopes_authorize("work.context", {"memory.read", "memory.write"}, exact_capability=True) is False


def test_a_platform_key_never_gets_the_exact_rule():
    """`flow.execute` is a key scope AND the capability of an unmapped syscall
    (`sys.v1.flow.execute_intent`). The exact rule would have let such a key reach it."""
    from AINDY.auth.api_key_auth import DISPATCH_CAPABILITY_SCOPES, Scopes, key_scopes_authorize
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    unmapped = {e.capability for e in SYSCALL_REGISTRY.values()} - set(DISPATCH_CAPABILITY_SCOPES)
    overlap = sorted(unmapped & set(Scopes.ALL))
    assert overlap, "liveness: the overlap this guards is real today"
    for capability in overlap:
        assert key_scopes_authorize(capability, {capability}) is False
        assert key_scopes_authorize(capability, {capability}, exact_capability=True) is True


@pytest.mark.parametrize(
    "capability,scope",
    [
        ("memory.read", "memory.read"),
        ("memory.search", "memory.read"),
        ("memory.write", "memory.write"),
        ("flow.run", "flow.execute"),
        ("work.context", "work.context"),
    ],
)
def test_the_offered_scope_for_each_capability_authorizes_it(capability, scope):
    from AINDY.auth.api_key_auth import key_scopes_authorize
    from AINDY.platform_layer.mcp_oauth import scope_for_capability

    assert scope_for_capability(capability) == scope
    assert key_scopes_authorize(capability, {scope}, exact_capability=True) is True  # an OAuth grant


def test_reads_are_offered_by_default_and_writes_only_when_exposed(monkeypatch):
    from AINDY.platform_layer.mcp_oauth import build_config

    monkeypatch.setenv("AINDY_MCP_PUBLIC_URL", "https://mcp.example.test")
    cfg = build_config("/mcp", ["sys.v1.memory.read", "sys.v1.memory.tree", "sys.v1.memory.write"])

    assert cfg.issuer == "https://mcp.example.test"
    assert cfg.resource == "https://mcp.example.test/mcp"
    assert set(cfg.offered_scopes) == {"memory.read", "memory.write"}
    assert cfg.default_scopes == ("memory.read",)
    assert cfg.scope_tools["memory.read"] == ["sys.v1.memory.read", "sys.v1.memory.tree"]


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://mcp.example.test", True),
        ("http://127.0.0.1:8080", True),
        ("http://mcp.example.test", False),
        ("https://mcp.example.test/sub", False),
        ("", False),
    ],
)
def test_the_public_url_must_be_an_https_origin(monkeypatch, url, ok):
    from AINDY.platform_layer.mcp_oauth import public_url

    monkeypatch.setenv("AINDY_MCP_PUBLIC_URL", url)
    if ok:
        assert public_url() == url
    else:
        with pytest.raises(RuntimeError):
            public_url()


def test_oauth_refuses_the_transports_it_does_not_apply_to(monkeypatch):
    pytest.importorskip("nodus_mcp_aindy")
    from AINDY.platform_layer import mcp_server

    monkeypatch.setenv("AINDY_MCP_OAUTH", "on")
    monkeypatch.setenv("AINDY_MCP_SERVER_USER_ID", "u-1")
    monkeypatch.delenv("AINDY_MCP_SERVER_MULTI_TENANT", raising=False)
    with pytest.raises(RuntimeError, match="--transport http only"):
        mcp_server.serve_stdio()
