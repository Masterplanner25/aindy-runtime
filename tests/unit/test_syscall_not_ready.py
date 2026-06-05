"""
INV-SYSCALL-001 regression tests: syscall dispatch rejects invalid calls
before any side effects (handler never called).

Covers:
- Unknown syscall name returns error envelope (no raise)
- Missing required capability returns error envelope (handler not called)
- Missing user_id (tenant violation) returns error envelope (handler not called)
- SyscallContractViolation propagates through dispatch() — not swallowed by
  the belt-and-suspenders except handler
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
import AINDY.kernel.syscall_registry as syscall_registry
from AINDY.kernel.syscall_dispatcher import SyscallContractViolation

pytestmark = pytest.mark.runtime_only


@pytest.fixture()
def dispatcher() -> syscall_dispatcher.SyscallDispatcher:
    d = syscall_dispatcher.SyscallDispatcher()
    d._emit_syscall_event = MagicMock()
    return d


class _OkRm:
    def check_quota(self, eu_id):
        return True, None

    def record_usage(self, eu_id, usage):
        return None


def _ctx(*, user_id: str | None = "user-1", caps: list[str] | None = None):
    return syscall_registry.SyscallContext(
        execution_unit_id="eu-1",
        user_id=user_id,
        capabilities=caps or ["my.capability"],
        trace_id="trace-1",
    )


# ── unknown syscall ───────────────────────────────────────────────────────────

def test_unknown_syscall_returns_error_envelope(dispatcher, monkeypatch):
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    result = dispatcher.dispatch("sys.v1.nonexistent.action", {}, _ctx())
    assert result["status"] == "error"
    assert "Unknown syscall" in result["error"]


def test_unknown_syscall_does_not_raise(dispatcher, monkeypatch):
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        dispatcher.dispatch("sys.v1.bogus.bogus", {}, _ctx())
    except Exception as exc:
        pytest.fail(f"dispatch() raised unexpectedly for unknown syscall: {exc}")


def test_unknown_syscall_envelope_has_correct_shape(dispatcher, monkeypatch):
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    result = dispatcher.dispatch("sys.v1.unknown.syscall", {}, _ctx())
    for key in ("status", "data", "trace_id", "execution_unit_id", "syscall", "version",
                "duration_ms", "error"):
        assert key in result, f"envelope missing key {key!r}"
    assert result["data"] == {}


# ── capability enforcement ────────────────────────────────────────────────────

def test_missing_capability_returns_error_envelope(dispatcher, monkeypatch):
    name = "sys.v1.test.cap_gate"
    handler = MagicMock(return_value={"ok": True})
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="required.capability",
    )
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        result = dispatcher.dispatch(name, {}, _ctx(caps=["other.capability"]))
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert result["status"] == "error"
    assert "Permission denied" in result["error"]


def test_missing_capability_handler_is_never_called(dispatcher, monkeypatch):
    name = "sys.v1.test.cap_no_call"
    handler = MagicMock(return_value={"ok": True})
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="required.capability",
    )
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        dispatcher.dispatch(name, {}, _ctx(caps=["wrong.capability"]))
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    handler.assert_not_called()


# ── tenant / user_id enforcement ─────────────────────────────────────────────

def test_missing_user_id_returns_tenant_violation_envelope(dispatcher, monkeypatch):
    name = "sys.v1.test.tenant_gate"
    handler = MagicMock(return_value={"ok": True})
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="my.capability",
    )
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        result = dispatcher.dispatch(name, {}, _ctx(user_id=None))
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert result["status"] == "error"
    assert "TENANT_VIOLATION" in result["error"]


def test_missing_user_id_handler_is_never_called(dispatcher, monkeypatch):
    name = "sys.v1.test.tenant_no_call"
    handler = MagicMock(return_value={"ok": True})
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="my.capability",
    )
    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        dispatcher.dispatch(name, {}, _ctx(user_id=None))
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    handler.assert_not_called()


# ── SyscallContractViolation propagation ─────────────────────────────────────

def test_syscall_contract_violation_propagates_through_dispatch(dispatcher, monkeypatch):
    """SyscallContractViolation must escape dispatch() — the belt-and-suspenders
    except handler must NOT swallow it."""
    def _raise_scv(*args, **kwargs):
        raise SyscallContractViolation("test contract violation")

    monkeypatch.setattr(dispatcher, "_dispatch", _raise_scv)
    with pytest.raises(SyscallContractViolation, match="test contract violation"):
        dispatcher.dispatch("sys.v1.any.thing", {}, _ctx())
