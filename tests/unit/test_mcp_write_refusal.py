"""The MCP server refuses to expose a write unless AINDY_MCP_SERVER_ALLOW_WRITES is set (DEC-128).

`AINDY_MCP_SERVER_TOOLS` replaced the allowlist outright, writes included, whatever
`ALLOW_WRITES` said; only the app's own launcher refused that. A syscall now counts as a read
only when it declares `read_only=True`, so an app syscall that never said is refused rather than
guessed at. The check runs when the server is built, after the app's syscalls are registered.
"""
from __future__ import annotations

import pytest

pytest.importorskip("nodus_mcp_aindy")

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def single_identity(monkeypatch):
    monkeypatch.setenv("AINDY_MCP_SERVER_USER_ID", "user-dec128")
    monkeypatch.delenv("AINDY_MCP_SERVER_MULTI_TENANT", raising=False)
    monkeypatch.delenv("AINDY_MCP_SERVER_ALLOW_WRITES", raising=False)
    monkeypatch.delenv("AINDY_MCP_SERVER_TOOLS", raising=False)


@pytest.fixture
def app_syscalls():
    """Register two app-style syscalls, one declared read-only and one not; remove them after."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall

    def _handler(payload, context):
        return {}

    names = {"read": "sys.v1.dec128probe.read", "undeclared": "sys.v1.dec128probe.undeclared"}
    register_syscall(names["read"], _handler, capability="dec128probe.read", read_only=True)
    register_syscall(names["undeclared"], _handler, capability="dec128probe.undeclared")
    try:
        yield names
    finally:
        for name in names.values():
            SYSCALL_REGISTRY.pop(name, None)


def _build():
    from AINDY.platform_layer import mcp_server

    server, _mode = mcp_server._build_server()
    return server


# -- the runtime's own lists agree with the declarations ----------------------------------------


def test_every_default_read_tool_is_declared_read_only():
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY
    from AINDY.platform_layer.mcp_server import READ_ONLY_SYSCALLS

    assert READ_ONLY_SYSCALLS, "an empty list would make this census say nothing"
    assert [n for n in READ_ONLY_SYSCALLS if not SYSCALL_REGISTRY[n].read_only] == []


def test_every_listed_write_tool_is_not_declared_read_only():
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY
    from AINDY.platform_layer.mcp_server import WRITE_SYSCALLS

    assert WRITE_SYSCALLS
    assert [n for n in WRITE_SYSCALLS if SYSCALL_REGISTRY[n].read_only] == []


# -- an explicit tool list cannot bring in a write ----------------------------------------------


def test_control_the_default_read_only_list_builds(single_identity):
    assert _build() is not None


def test_an_explicit_runtime_write_is_refused_without_the_flag(single_identity, monkeypatch):
    monkeypatch.setenv("AINDY_MCP_SERVER_TOOLS", "sys.v1.memory.read,sys.v1.memory.write")

    with pytest.raises(RuntimeError, match=r"refusing to expose sys\.v1\.memory\.write"):
        _build()


def test_the_flag_lets_the_same_list_build(single_identity, monkeypatch):
    monkeypatch.setenv("AINDY_MCP_SERVER_TOOLS", "sys.v1.memory.read,sys.v1.memory.write")
    monkeypatch.setenv("AINDY_MCP_SERVER_ALLOW_WRITES", "true")

    assert _build() is not None


def test_an_app_syscall_that_never_declared_is_treated_as_a_write(single_identity, monkeypatch, app_syscalls):
    monkeypatch.setenv("AINDY_MCP_SERVER_TOOLS", f"{app_syscalls['read']},{app_syscalls['undeclared']}")

    with pytest.raises(RuntimeError) as refused:
        _build()
    assert app_syscalls["undeclared"] in str(refused.value)
    assert app_syscalls["read"] not in str(refused.value)


def test_an_app_syscall_declared_read_only_is_served(single_identity, monkeypatch, app_syscalls):
    monkeypatch.setenv("AINDY_MCP_SERVER_TOOLS", app_syscalls["read"])

    assert _build() is not None


def test_an_unregistered_name_is_not_this_checks_business(single_identity, monkeypatch):
    """`build_registry` skips an unknown name with a warning; this check doesn't guess at it."""
    from AINDY.platform_layer.mcp_server import refuse_unflagged_writes

    refuse_unflagged_writes(["sys.v1.nothing.here"])
