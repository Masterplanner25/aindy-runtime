"""FR-53 (b) / DEC-099 — a payload `user_id` written outside the runtime may only repeat the
caller's identity, never choose the user a syscall acts on.

`dispatch_syscall` authenticates the caller but never compared it with a payload `user_id`, and
app handlers read the payload's. Each transport whose payload is written by someone else (an
outside AI over MCP, a Nodus script, an HTTP client) now binds it before dispatch. Every
refusal below is paired with a control that dispatches the same shape with the caller's own id.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from unittest.mock import patch

import pytest

from AINDY.kernel.payload_identity import (
    PayloadIdentityViolation,
    bind_payload_user_id,
    payload_user_id_mismatch,
)

pytestmark = pytest.mark.runtime_only

ROOT = pathlib.Path(__file__).resolve().parents[2]


# ── the helper ─────────────────────────────────────────────────────────────────────────


def test_absent_user_id_is_filled_with_the_caller():
    assert bind_payload_user_id({"q": 1}, "u1") == {"q": 1, "user_id": "u1"}
    assert bind_payload_user_id({"user_id": None}, "u1") == {"user_id": "u1"}
    assert bind_payload_user_id({"user_id": ""}, "u1") == {"user_id": "u1"}
    assert bind_payload_user_id(None, "u1") == {"user_id": "u1"}


def test_the_callers_own_id_passes_in_any_uuid_spelling():
    uid = uuid.uuid4()
    for spelling in (str(uid), str(uid).upper(), uid.hex, f"{{{uid}}}", uid):
        assert bind_payload_user_id({"user_id": spelling}, str(uid))["user_id"] == spelling
        assert not payload_user_id_mismatch({"user_id": spelling}, str(uid))


def test_another_user_is_refused_never_overwritten():
    with pytest.raises(PayloadIdentityViolation, match="TENANT_VIOLATION") as exc_info:
        bind_payload_user_id({"user_id": str(uuid.uuid4())}, str(uuid.uuid4()), syscall="sys.v1.a.b")
    assert "sys.v1.a.b" in str(exc_info.value)
    assert isinstance(exc_info.value, PermissionError)
    with pytest.raises(PayloadIdentityViolation):
        bind_payload_user_id({"user_id": "alice"}, "bob")


def test_the_input_is_not_mutated():
    original = {"q": 1}
    bind_payload_user_id(original, "u1")
    assert original == {"q": 1}


# ── MCP: the outside AI writes the arguments ───────────────────────────────────────────


def _mcp_tool(configured: str):
    pytest.importorskip("nodus_mcp_aindy")
    from AINDY.platform_layer import mcp_server

    registry = mcp_server.build_registry(configured, ["sys.v1.memory.read"])
    return registry.get(registry.names()[0])


def test_mcp_dispatches_when_the_arguments_name_the_caller():
    tool = _mcp_tool("user-xyz")
    with patch("AINDY.kernel.syscall_dispatcher.dispatch_syscall") as m:
        m.return_value = {"status": "success", "data": {}}
        result = tool.handler({"query": "x", "user_id": "user-xyz"})
    assert result["status"] == "success"
    assert m.call_args.args[1] == {"query": "x", "user_id": "user-xyz"}


def test_mcp_refuses_arguments_that_name_another_user():
    tool = _mcp_tool("user-xyz")
    with patch("AINDY.kernel.syscall_dispatcher.dispatch_syscall") as m:
        result = tool.handler({"query": "x", "user_id": "victim"})
    m.assert_not_called()
    assert result["status"] == "error"
    assert result["failure_class"] == "permission"
    assert "TENANT_VIOLATION" in result["error"]
    assert result["syscall"] == "sys.v1.memory.read"


def test_mcp_refusal_claims_no_execution_unit():
    # Refused before the per-call unit is owned, so a refused call costs the identity nothing.
    tool = _mcp_tool("user-xyz")
    from AINDY.kernel.resource_manager import get_resource_manager

    with patch.object(type(get_resource_manager()), "owned_execution") as owned:
        tool.handler({"user_id": "victim"})
    owned.assert_not_called()


# ── POST /platform/syscall: the HTTP client writes the payload ─────────────────────────


def _session(uid: str) -> dict:
    from AINDY.auth.api_key_auth import derive_session_scopes

    return {
        "sub": uid,
        "user_id": uid,
        "auth_type": "jwt",
        "is_admin": True,
        "session_scopes": derive_session_scopes(is_admin=True),
    }


def test_route_refuses_a_payload_naming_another_user(runtime_only_app):
    from fastapi.testclient import TestClient

    from AINDY.kernel.syscall_dispatcher import SyscallDispatcher
    from AINDY.services.auth_service import get_current_user

    caller = str(uuid.uuid4())
    runtime_only_app.dependency_overrides[get_current_user] = lambda: _session(caller)
    dispatched: list[dict] = []
    real_dispatch = SyscallDispatcher.dispatch

    def _spy(self, name, payload, context):
        dispatched.append(dict(payload))
        return real_dispatch(self, name, payload, context)

    body = {"event_type": "fr53.probe", "payload": {}}
    with patch.object(SyscallDispatcher, "dispatch", _spy), TestClient(
        runtime_only_app, raise_server_exceptions=False
    ) as client:
        # Control: the caller's own id dispatches and succeeds.
        own = client.post(
            "/platform/syscall",
            json={"name": "sys.v1.event.emit", "payload": {**body, "user_id": caller}},
        )
        assert own.status_code == 200, own.text
        assert own.json()["status"] == "success"
        assert len(dispatched) == 1

        foreign = client.post(
            "/platform/syscall",
            json={"name": "sys.v1.event.emit", "payload": {**body, "user_id": str(uuid.uuid4())}},
        )
    assert foreign.status_code == 403, foreign.text
    assert "TENANT_VIOLATION" in foreign.text
    assert len(dispatched) == 1, "the refused call reached the dispatcher"


# ── every outside-payload transport binds — derived, not listed ────────────────────────

# The transports whose payload is written outside the runtime. A new one belongs here AND
# must call bind_payload_user_id; the census below fails when a listed site stops calling it.
_OUTSIDE_PAYLOAD_TRANSPORTS = {
    "AINDY/platform_layer/mcp_server.py": "_make_handler",
    "AINDY/runtime/nodus_worker.py": "dispatch_worker_syscall",
    "AINDY/routes/platform/platform_ops_router.py": "dispatch_syscall",
}


def _calls_in(func: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            target = node.func
            names.add(getattr(target, "id", None) or getattr(target, "attr", "") or "")
    return names


@pytest.mark.parametrize("relpath,function", sorted(_OUTSIDE_PAYLOAD_TRANSPORTS.items()))
def test_each_outside_payload_transport_binds_before_it_dispatches(relpath, function):
    tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8-sig"))
    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function
    ]
    assert funcs, f"{relpath}::{function} not found — the census is stale"
    calls = set().union(*(_calls_in(f) for f in funcs))
    assert "dispatch" in calls or "dispatch_syscall" in calls, "liveness: the site still dispatches"
    assert "bind_payload_user_id" in calls, f"{relpath}::{function} dispatches without binding"


# ── FR-53 (a): the MCP server process registers the app's syscalls ─────────────────────

_PLUGIN_SOURCE = r'''\
import os
import sys


def _handler(payload, context):
    return {"echo": payload.get("user_id")}


def bootstrap():
    # Both kinds of stray output a plugin can produce: Python's and the raw descriptor's.
    print("fr53 plugin says hello on stdout")
    os.write(1, b"fr53 raw fd write\n")
    from AINDY.kernel.syscall_registry import register_syscall

    register_syscall("sys.v1.fr53probe.echo", _handler, capability="fr53probe.echo")
'''


@pytest.fixture
def fr53_plugin(tmp_path, monkeypatch):
    """An in-process first-party plugin `apps.fr53_plugin` behind a temporary manifest."""
    import sys
    import types

    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY
    from AINDY.platform_layer import registry
    from tests.unit.test_extension_abi import _REGISTRY_STATE_EMPTY, _copy_registry_value

    pkg = tmp_path / "apps"
    pkg.mkdir()
    (pkg / "fr53_plugin.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
    fake_apps = types.ModuleType("apps")
    fake_apps.__path__ = [str(pkg)]
    monkeypatch.setitem(sys.modules, "apps", fake_apps)
    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text('{"plugins": ["apps.fr53_plugin"]}', encoding="utf-8")
    monkeypatch.setenv("AINDY_PLUGIN_MANIFEST", str(manifest))

    snapshot = {n: _copy_registry_value(getattr(registry, n)) for n in _REGISTRY_STATE_EMPTY}
    for n, value in _REGISTRY_STATE_EMPTY.items():
        setattr(registry, n, _copy_registry_value(value))
    try:
        yield "sys.v1.fr53probe.echo"
    finally:
        for n, value in snapshot.items():
            setattr(registry, n, value)
        SYSCALL_REGISTRY.pop("sys.v1.fr53probe.echo", None)
        sys.modules.pop("apps.fr53_plugin", None)


def test_mcp_server_exposes_an_app_syscall_and_keeps_stdout_clean(fr53_plugin, monkeypatch, capfd):
    pytest.importorskip("nodus_mcp_aindy")
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY
    from AINDY.platform_layer import mcp_server

    assert fr53_plugin not in SYSCALL_REGISTRY, "liveness: the probe must start unregistered"
    monkeypatch.setenv("AINDY_MCP_SERVER_TOOLS", f"{fr53_plugin},sys.v1.memory.read")
    monkeypatch.setenv("AINDY_MCP_SERVER_USER_ID", "user-xyz")
    monkeypatch.delenv("AINDY_MCP_SERVER_MULTI_TENANT", raising=False)

    built: list = []
    real_build = mcp_server.build_registry

    def _capture(*args, **kwargs):
        built.append(real_build(*args, **kwargs))
        return built[-1]

    monkeypatch.setattr(mcp_server, "build_registry", _capture)
    mcp_server._build_server()

    assert fr53_plugin in SYSCALL_REGISTRY
    [registry] = built
    # Both allowlisted names are served: the app's syscall and the runtime's.
    assert len(registry.names()) == 2, registry.names()
    out, err = capfd.readouterr()
    # Over stdio this stream is the MCP channel — the plugin's chatter must land on stderr.
    assert "fr53" not in out, f"plugin output reached stdout: {out!r}"
    assert "fr53 plugin says hello on stdout" in err
    assert "fr53 raw fd write" in err


def test_a_plugin_that_fails_to_load_stops_the_server(fr53_plugin, monkeypatch, tmp_path):
    from AINDY.platform_layer import mcp_server

    (tmp_path / "apps" / "fr53_plugin.py").write_text("def bootstrap():\n    raise ImportError('boom')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="plugin load failed"):
        mcp_server.load_app_syscalls()


def test_platform_only_profile_is_the_escape_from_a_broken_plugin(fr53_plugin, monkeypatch, tmp_path):
    """DEC-101's documented escape: an operator serving runtime tools only, with a broken app
    plugin, starts the MCP server under `AINDY_BOOT_PROFILE=platform-only`. Control first: the
    same profile-format manifest without the variable stops the server."""
    from AINDY.platform_layer import mcp_server

    (tmp_path / "apps" / "fr53_plugin.py").write_text("def bootstrap():\n    raise ImportError('boom')\n", encoding="utf-8")
    manifest = tmp_path / "profiles.json"
    manifest.write_text(
        '{"default_profile": "default-apps", "profiles": {"platform-only": {"plugins": []},'
        ' "default-apps": {"plugins": ["apps.fr53_plugin"]}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("AINDY_PLUGIN_MANIFEST", str(manifest))
    monkeypatch.delenv("AINDY_BOOT_PROFILE", raising=False)
    monkeypatch.delenv("AINDY_PLUGIN_PROFILE", raising=False)
    with pytest.raises(RuntimeError, match="plugin load failed"):
        mcp_server.load_app_syscalls()

    monkeypatch.setenv("AINDY_BOOT_PROFILE", "platform-only")
    assert mcp_server.load_app_syscalls() == []
