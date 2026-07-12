"""Server-side MCP interop (ECOGAP-4 / G4b) — identity, allowlist, and registry build.

Config resolution (identity + allowlist) is pure and always tested. The registry build
needs the [mcp] extra (nodus_mcp_aindy); those tests skip cleanly without it and dispatch
is mocked so nothing hits the kernel/DB.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from AINDY.platform_layer import mcp_server

pytestmark = pytest.mark.runtime_only


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


# ── MEB-3a — per-session (multi-tenant) identity ────────────────────────────────

def test_multi_tenant_flag(monkeypatch):
    monkeypatch.setenv("AINDY_MCP_SERVER_MULTI_TENANT", "true")
    assert mcp_server.multi_tenant_enabled() is True
    monkeypatch.setenv("AINDY_MCP_SERVER_MULTI_TENANT", "false")
    assert mcp_server.multi_tenant_enabled() is False
    monkeypatch.delenv("AINDY_MCP_SERVER_MULTI_TENANT", raising=False)
    assert mcp_server.multi_tenant_enabled() is False


def test_resolve_session_identity_none_without_headers():
    assert mcp_server._resolve_session_identity({}) is None
    assert mcp_server._resolve_session_identity({"headers": {}}) is None
    assert mcp_server._resolve_session_identity({"headers": {"x-other": "v"}}) is None


def test_resolve_session_identity_bearer_jwt():
    ctx = {"headers": {"Authorization": "Bearer sometoken"}}  # header case is normalized
    with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()), patch(
        "AINDY.services.auth_service.decode_access_token", return_value={"sub": "u-1"}
    ), patch(
        "AINDY.services.auth_service._resolve_authenticated_jwt_user",
        return_value={"user_id": "user-jwt"},
    ):
        assert mcp_server._resolve_session_identity(ctx) == "user-jwt"


def test_resolve_session_identity_platform_key():
    ctx = {"headers": {"x-platform-key": "pk-123"}}
    with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()), patch(
        "AINDY.services.auth_service._resolve_platform_key_as_user",
        return_value={"user_id": "user-pk"},
    ):
        assert mcp_server._resolve_session_identity(ctx) == "user-pk"


def test_auth_hook_denies_without_identity():
    hook = mcp_server.build_auth_hook()
    with pytest.raises(PermissionError):
        hook("sys.v1.memory.read", {}, {"headers": {}})


def test_auth_hook_denies_on_invalid_credential():
    from fastapi import HTTPException

    hook = mcp_server.build_auth_hook()
    with patch("AINDY.db.database.SessionLocal", return_value=MagicMock()), patch(
        "AINDY.services.auth_service.decode_access_token",
        side_effect=HTTPException(status_code=401, detail="bad"),
    ):
        with pytest.raises(PermissionError):
            hook("sys.v1.memory.read", {}, {"headers": {"authorization": "Bearer nope"}})


def test_auth_hook_sets_session_identity_and_handler_uses_it():
    # The auth_hook resolves a per-session identity; the handler dispatches as THAT id,
    # overriding the configured fallback.
    pytest.importorskip("nodus_mcp_aindy")
    token = mcp_server._SESSION_IDENTITY.set(None)
    try:
        hook = mcp_server.build_auth_hook()
        with patch.object(mcp_server, "_resolve_session_identity", return_value="tenant-7"):
            hook("sys.v1.memory.read", {}, {"headers": {"authorization": "Bearer t"}})
        assert mcp_server._SESSION_IDENTITY.get() == "tenant-7"

        registry = mcp_server.build_registry("configured-fallback", ["sys.v1.memory.read"])
        tool = registry.get(registry.names()[0])
        with patch("AINDY.kernel.syscall_dispatcher.dispatch_syscall") as m:
            m.return_value = {"status": "success"}
            tool.handler({"path": "/x/**"})
        # Dispatched as the per-session identity, not the configured fallback.
        assert m.call_args.kwargs["user_id"] == "tenant-7"
    finally:
        mcp_server._SESSION_IDENTITY.reset(token)


def test_session_token_stringifies_session_object_or_none():
    class _Sess:
        pass

    sess = _Sess()
    assert mcp_server._session_token({"session": sess}) == f"mcp:{id(sess)}"
    assert mcp_server._session_token({"session": None}) is None
    assert mcp_server._session_token({}) is None


def test_auth_hook_sets_effect_attribution(monkeypatch):
    # MEB-3b — the auth_hook stashes the resolved identity + session id ambiently so any
    # effect record written under the call is attributed to that tenant/session.
    from AINDY.kernel import effect_ledger

    attr_token = effect_ledger.set_effect_attribution(tenant_id=None, session_id=None)
    id_token = mcp_server._SESSION_IDENTITY.set(None)

    class _Sess:
        pass

    sess = _Sess()
    try:
        hook = mcp_server.build_auth_hook()
        with patch.object(mcp_server, "_resolve_session_identity", return_value="tenant-9"):
            hook("sys.v1.memory.read", {}, {"headers": {"authorization": "Bearer t"}, "session": sess})
        assert effect_ledger.current_effect_attribution() == ("tenant-9", f"mcp:{id(sess)}")
    finally:
        effect_ledger.reset_effect_attribution(attr_token)
        mcp_server._SESSION_IDENTITY.reset(id_token)


def test_serve_stdio_rejects_multi_tenant(monkeypatch):
    monkeypatch.setenv("AINDY_MCP_SERVER_MULTI_TENANT", "true")
    monkeypatch.setenv("AINDY_MCP_SERVER_USER_ID", "u-1")
    with pytest.raises(RuntimeError, match="only meaningful over the SSE"):
        mcp_server.serve_stdio()
