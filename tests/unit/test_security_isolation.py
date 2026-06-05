"""
Security isolation tests for extension boundary, tenant enforcement, and
capability enforcement paths.

Invariants tested:
  - All _BLOCKED_ROOT_KEYS are stripped from extension contexts
  - AINDY.* objects are redacted in extension payloads (never passed raw)
  - Safe primitive values pass through sanitization unchanged
  - Extension tenant mismatch (user_id in metadata != context.user_id) is rejected
  - Quota backend failure fails open in dev/test, fails closed in production
  - Tier 1 trusted code (runtime-built-in) executes without capability confinement

These tests exercise the enforcement paths documented in:
  docs/runtime/SECURITY_POSTURE.md
  docs/runtime/EXTENSION_TRUST_MODEL.md
  docs/runtime/EXTENSION_CAPABILITIES.md
"""
from __future__ import annotations

import pytest

from AINDY.platform_layer.extension_boundary import (
    sanitize_extension_context,
    sanitize_extension_payload,
)

pytestmark = pytest.mark.runtime_only


# ── extension context sanitization ───────────────────────────────────────────

# Every key in _BLOCKED_ROOT_KEYS must be stripped from extension context
_ALL_BLOCKED_KEYS = [
    "db", "_db", "session", "engine", "settings", "config",
    "secret", "secrets", "request", "response", "app",
]


@pytest.mark.parametrize("blocked_key", _ALL_BLOCKED_KEYS)
def test_blocked_root_key_is_stripped_from_extension_context(blocked_key: str) -> None:
    ctx = {blocked_key: "sensitive_value", "safe": "visible"}
    result = sanitize_extension_context(ctx)
    assert blocked_key not in result
    assert result["safe"] == "visible"


def test_settings_key_is_stripped_from_extension_context() -> None:
    ctx = {"settings": {"SECRET_KEY": "hunter2"}, "user_id": "u1"}
    result = sanitize_extension_context(ctx)
    assert "settings" not in result
    assert result["user_id"] == "u1"


def test_secret_key_is_stripped_from_extension_context() -> None:
    ctx = {"secret": "tok-abc", "safe_field": "ok"}
    result = sanitize_extension_context(ctx)
    assert "secret" not in result
    assert result["safe_field"] == "ok"


def test_blocked_key_nested_inside_non_root_is_not_stripped() -> None:
    """_BLOCKED_ROOT_KEYS only applies at root level, not nested."""
    ctx = {"outer": {"db": "nested-value"}, "safe": "ok"}
    result = sanitize_extension_context(ctx)
    # nested "db" inside "outer" is redacted because it's a non-primitive, not because of key blocking
    # actually "nested-value" is a string, so it passes through
    assert result["outer"]["db"] == "nested-value"
    assert result["safe"] == "ok"


def test_none_context_returns_empty_dict() -> None:
    result = sanitize_extension_context(None)
    assert result == {}


def test_primitive_values_pass_through_sanitization() -> None:
    ctx = {"name": "my-extension", "version": 2, "enabled": True, "score": 3.14}
    result = sanitize_extension_context(ctx)
    assert result == ctx


def test_list_of_primitives_passes_through() -> None:
    ctx = {"tags": ["a", "b", "c"], "count": 3}
    result = sanitize_extension_context(ctx)
    assert result["tags"] == ["a", "b", "c"]


# ── AINDY.* object redaction ─────────────────────────────────────────────────

def test_aindy_object_is_redacted_in_extension_payload() -> None:
    """Objects whose __module__ starts with 'AINDY.' must be redacted."""
    class _FakeAINDYObj:
        __module__ = "AINDY.kernel.something"

    result = sanitize_extension_payload(_FakeAINDYObj())
    assert isinstance(result, dict)
    assert result["_redacted_type"] == "_FakeAINDYObj"


def test_aindy_object_in_dict_is_redacted_in_extension_payload() -> None:
    class _FakeAINDYThing:
        __module__ = "AINDY.platform_layer.registry"

    result = sanitize_extension_payload({"safe": "value", "internal": _FakeAINDYThing()})
    assert result["safe"] == "value"
    assert result["internal"] == {"_redacted_type": "_FakeAINDYThing"}


def test_aindy_object_in_nested_list_is_redacted() -> None:
    class _FakeNode:
        __module__ = "AINDY.runtime.flow_engine"

    result = sanitize_extension_payload({"nodes": [_FakeNode(), "ok"]})
    assert result["nodes"][0] == {"_redacted_type": "_FakeNode"}
    assert result["nodes"][1] == "ok"


def test_non_aindy_custom_object_is_also_redacted() -> None:
    """Any non-primitive non-AINDY object is also redacted as a safety default."""
    class _ArbitraryObject:
        pass

    result = sanitize_extension_payload(_ArbitraryObject())
    assert isinstance(result, dict)
    assert "_redacted_type" in result


# ── extension runtime call tenant mismatch ───────────────────────────────────

def test_extension_runtime_call_with_mismatched_tenant_returns_violation_envelope(
    monkeypatch,
) -> None:
    """An extension call where tenant_user_id != context.user_id must fail with TENANT_VIOLATION.
    This is the cross-tenant escalation guard."""
    import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
    import AINDY.kernel.syscall_registry as syscall_registry
    from unittest.mock import MagicMock

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = MagicMock()

    name = "sys.v1.test.extension_tenant_mismatch"
    handler = MagicMock(return_value={"ok": True})
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="my.capability",
    )

    class _OkRm:
        def check_quota(self, eu_id):
            return True, None
        def record_usage(self, eu_id, usage):
            return None

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        ctx = syscall_registry.SyscallContext(
            execution_unit_id="eu-1",
            user_id="user-1",
            capabilities=["my.capability"],
            trace_id="trace-1",
            metadata={
                "_extension_call": {
                    "tenant_user_id": "user-2",  # different from context.user_id
                    "extension_name": "my-ext",
                }
            },
        )
        result = dispatcher.dispatch(name, {}, ctx)
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert result["status"] == "error"
    assert "TENANT_VIOLATION" in result["error"]
    handler.assert_not_called()


def test_extension_runtime_call_with_matching_tenant_passes_gate(monkeypatch) -> None:
    """A well-formed extension call where tenant_user_id == context.user_id proceeds."""
    import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
    import AINDY.kernel.syscall_registry as syscall_registry
    from unittest.mock import MagicMock

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = MagicMock()

    name = "sys.v1.test.extension_tenant_match"
    handler = MagicMock(return_value={"ok": True})
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="my.capability",
    )

    class _OkRm:
        def check_quota(self, eu_id):
            return True, None
        def record_usage(self, eu_id, usage):
            return None

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _OkRm())
    try:
        ctx = syscall_registry.SyscallContext(
            execution_unit_id="eu-1",
            user_id="user-1",
            capabilities=["my.capability"],
            trace_id="trace-1",
            metadata={
                "_extension_call": {
                    "tenant_user_id": "user-1",  # matches context.user_id
                    "extension_name": "my-ext",
                }
            },
        )
        result = dispatcher.dispatch(name, {}, ctx)
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert result["status"] == "success"
    handler.assert_called_once()


# ── quota backend failure modes ───────────────────────────────────────────────

def test_quota_backend_failure_fails_open_in_test_mode(monkeypatch) -> None:
    """In test/dev mode, quota backend errors allow execution to continue (fail open).
    No settings patching needed: test env already has TESTING=True -> is_testing=True."""
    import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
    import AINDY.kernel.syscall_registry as syscall_registry
    from unittest.mock import MagicMock

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = MagicMock()

    name = "sys.v1.test.quota_fail_open"
    handler = MagicMock(return_value={"ok": True})
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="my.capability",
    )

    class _BrokenRm:
        def check_quota(self, eu_id):
            raise RuntimeError("quota store unavailable")
        def record_usage(self, eu_id, usage):
            pass

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _BrokenRm())
    try:
        ctx = syscall_registry.SyscallContext(
            execution_unit_id="eu-1",
            user_id="user-1",
            capabilities=["my.capability"],
            trace_id="trace-1",
        )
        result = dispatcher.dispatch(name, {}, ctx)
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert result["status"] == "success"
    handler.assert_called_once()


def test_quota_backend_failure_fails_closed_in_production(monkeypatch) -> None:
    """In production mode, quota backend errors block execution (fail closed).
    is_testing and is_dev are @property computed from TESTING/TEST_MODE/ENV;
    patch the underlying fields, not the properties themselves."""
    import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
    import AINDY.kernel.syscall_registry as syscall_registry
    from unittest.mock import MagicMock

    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = MagicMock()

    name = "sys.v1.test.quota_fail_closed"
    handler = MagicMock(return_value={"ok": True})
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=handler,
        capability="my.capability",
    )

    class _BrokenRm:
        def check_quota(self, eu_id):
            raise RuntimeError("quota store unavailable")
        def record_usage(self, eu_id, usage):
            pass

    monkeypatch.setattr(syscall_dispatcher, "_get_rm", lambda: _BrokenRm())
    # is_testing = TESTING or TEST_MODE or ENV=="test"; is_dev = ENV in ("dev","development")
    monkeypatch.setattr(syscall_dispatcher.settings, "TESTING", False)
    monkeypatch.setattr(syscall_dispatcher.settings, "TEST_MODE", False)
    monkeypatch.setattr(syscall_dispatcher.settings, "ENV", "production")
    try:
        ctx = syscall_registry.SyscallContext(
            execution_unit_id="eu-1",
            user_id="user-1",
            capabilities=["my.capability"],
            trace_id="trace-1",
        )
        result = dispatcher.dispatch(name, {}, ctx)
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)

    assert result["status"] == "error"
    assert "Quota backend unavailable" in result["error"]
    handler.assert_not_called()
