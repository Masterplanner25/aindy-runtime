"""Client-side MCP interop (ECOGAP-4 / G4b) — registration + sync bridge contract.

Mocks nodus_mcp_aindy so there is no network. Pins: MCP tools register into the
EXECUTABLE TOOL_REGISTRY with the right shape, the registered fn proxies through the
sync<->async bridge, discovery is resilient, and bootstrap is a no-op when off.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.platform_layer import mcp_client


@pytest.fixture(autouse=True)
def _clean_registry():
    """Remove any mcp_* tools this test registered, so tests don't leak into each other."""
    from AINDY.agents import tool_registry as tr

    before = set(tr.TOOL_REGISTRY)
    tr._MCP_CLIENT_LOADED = False
    yield
    for name in set(tr.TOOL_REGISTRY) - before:
        tr.TOOL_REGISTRY.pop(name, None)
    tr._MCP_CLIENT_LOADED = False


class _FakeAdapter:
    """Async MCP client adapter stand-in."""

    last_call = None

    def __init__(self, url, *, timeout=10.0, **_):
        self.url = url

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def call_tool(self, name, args):
        _FakeAdapter.last_call = (self.url, name, args)
        return {"echoed": args, "tool": name}


async def _fake_discover(url, *, timeout=10.0):
    return [
        SimpleNamespace(name="echo", description="Echo the args", input_schema={}, risk="high"),
        SimpleNamespace(name="add", description=None, input_schema={}, risk="high"),
    ]


def test_discover_and_register_creates_executable_tools():
    from AINDY.agents.tool_registry import TOOL_REGISTRY

    with patch("nodus_mcp_aindy.discover_tools", _fake_discover):
        names = mcp_client.discover_and_register({"name": "demo", "url": "http://mcp.local"})

    assert names == ["mcp_demo_echo", "mcp_demo_add"]
    entry = TOOL_REGISTRY["mcp_demo_echo"]
    # Executable path: lands in TOOL_REGISTRY with the full 7-key shape.
    assert callable(entry["fn"])
    assert entry["capability"] == mcp_client.MCP_EGRESS_CAPABILITY == "outbound.mcp"
    assert entry["required_capability"] == "outbound.mcp"
    assert entry["category"] == "mcp"
    assert entry["egress_scope"] == "demo"
    assert entry["risk"] == "high"
    assert entry["description"] == "Echo the args"
    # description falls back when the remote tool omits one.
    assert "add" in TOOL_REGISTRY["mcp_demo_add"]["description"]


def test_registered_fn_proxies_through_sync_bridge():
    from AINDY.agents.tool_registry import TOOL_REGISTRY

    with patch("nodus_mcp_aindy.discover_tools", _fake_discover):
        mcp_client.discover_and_register({"name": "demo", "url": "http://mcp.local", "timeout": 3})

    fn = TOOL_REGISTRY["mcp_demo_echo"]["fn"]
    with patch("nodus_mcp_aindy.MCPClientAdapter", _FakeAdapter):
        # execute_tool calls fn(args=, user_id=, db=)
        result = fn(args={"msg": "hi"}, user_id="u-1", db=None)

    assert result == {"echoed": {"msg": "hi"}, "tool": "echo"}
    assert _FakeAdapter.last_call == ("http://mcp.local", "echo", {"msg": "hi"})


def test_discovery_failure_is_resilient():
    async def _boom(url, *, timeout=10.0):
        raise ConnectionError("server down")

    with patch("nodus_mcp_aindy.discover_tools", _boom):
        names = mcp_client.discover_and_register({"name": "dead", "url": "http://nope"})
    assert names == []


def test_bootstrap_noop_when_disabled(monkeypatch):
    from AINDY.agents.tool_registry import TOOL_REGISTRY

    monkeypatch.delenv("AINDY_MCP_CLIENT_ENABLED", raising=False)
    before = set(TOOL_REGISTRY)
    mcp_client.bootstrap()
    assert set(TOOL_REGISTRY) == before


def test_bootstrap_enabled_registers_from_servers(monkeypatch):
    from AINDY.agents.tool_registry import TOOL_REGISTRY

    monkeypatch.setenv("AINDY_MCP_CLIENT_ENABLED", "true")
    monkeypatch.setenv(
        "AINDY_MCP_SERVERS", '[{"name": "demo", "url": "http://mcp.local"}]'
    )
    with patch("nodus_mcp_aindy.discover_tools", _fake_discover):
        mcp_client.bootstrap()
    assert "mcp_demo_echo" in TOOL_REGISTRY


@pytest.mark.parametrize("bad", ["not json", "{}", "[1,2,3]", ""])
def test_parse_servers_handles_bad_config(monkeypatch, bad):
    monkeypatch.setenv("AINDY_MCP_SERVERS", bad)
    assert mcp_client._parse_servers() == []


def test_sync_bridge_returns_value_without_caller_loop():
    async def _co():
        return 42

    assert mcp_client._run_sync(_co(), timeout=5) == 42


def test_ensure_mcp_client_tools_is_memoized():
    """The tool-load hook calls bootstrap once per process, then no-ops."""
    from AINDY.agents import tool_registry as tr

    calls = {"n": 0}
    with patch.object(mcp_client, "bootstrap", lambda: calls.__setitem__("n", calls["n"] + 1)):
        tr._ensure_mcp_client_tools()
        tr._ensure_mcp_client_tools()
    assert calls["n"] == 1
