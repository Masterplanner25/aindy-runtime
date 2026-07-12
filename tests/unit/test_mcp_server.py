"""Server-side MCP interop (ECOGAP-4 / G4b) — identity, allowlist, and registry build.

Config resolution (identity + allowlist) is pure and always tested. The registry build
needs the [mcp] extra (nodus_mcp_aindy); those tests skip cleanly without it and dispatch
is mocked so nothing hits the kernel/DB.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.platform_layer import mcp_server


def test_identity_required(monkeypatch):
    monkeypatch.delenv("AINDY_MCP_SERVER_USER_ID", raising=False)
    with pytest.raises(RuntimeError):
        mcp_server.resolve_identity()


def test_identity_returns_configured(monkeypatch):
    monkeypatch.setenv("AINDY_MCP_SERVER_USER_ID", "user-abc")
    assert mcp_server.resolve_identity() == "user-abc"


def test_allowlist_default_is_read_only(monkeypatch):
    monkeypatch.delenv("AINDY_MCP_SERVER_TOOLS", raising=False)
    monkeypatch.delenv("AINDY_MCP_SERVER_ALLOW_WRITES", raising=False)
    allow = mcp_server.resolve_allowlist()
    assert "sys.v1.memory.read" in allow
    # Writes are excluded by default.
    assert "sys.v1.memory.write" not in allow
    assert "sys.v1.memory.delete" not in allow
    assert "sys.v1.flow.run" not in allow


def test_allowlist_includes_writes_when_enabled(monkeypatch):
    monkeypatch.delenv("AINDY_MCP_SERVER_TOOLS", raising=False)
    monkeypatch.setenv("AINDY_MCP_SERVER_ALLOW_WRITES", "true")
    allow = mcp_server.resolve_allowlist()
    assert "sys.v1.memory.write" in allow
    assert "sys.v1.memory.delete" in allow
    assert "sys.v1.flow.run" in allow


def test_allowlist_explicit_override(monkeypatch):
    monkeypatch.setenv("AINDY_MCP_SERVER_TOOLS", "sys.v1.memory.read, sys.v1.flow.run")
    monkeypatch.setenv("AINDY_MCP_SERVER_ALLOW_WRITES", "true")  # ignored when TOOLS is set
    assert mcp_server.resolve_allowlist() == ["sys.v1.memory.read", "sys.v1.flow.run"]


def test_build_registry_exposes_allowlisted_syscalls_and_skips_unknown():
    pytest.importorskip("nodus_mcp_aindy")
    registry = mcp_server.build_registry(
        "user-1", ["sys.v1.memory.read", "sys.v1.does.not.exist"]
    )
    # One real syscall registered; the unknown one is skipped.
    assert len(registry.names()) == 1


def test_registered_handler_dispatches_as_configured_identity():
    pytest.importorskip("nodus_mcp_aindy")
    registry = mcp_server.build_registry("user-xyz", ["sys.v1.memory.read"])
    tool = registry.get(registry.names()[0])

    with patch("AINDY.kernel.syscall_dispatcher.dispatch_syscall") as m:
        m.return_value = {"status": "success", "data": {"nodes": []}}
        result = tool.handler({"path": "/memory/x/**"})

    assert result == {"status": "success", "data": {"nodes": []}}
    # Dispatched the syscall as the configured identity.
    args, kwargs = m.call_args
    assert args[0] == "sys.v1.memory.read"
    assert args[1] == {"path": "/memory/x/**"}
    assert kwargs["user_id"] == "user-xyz"
