"""sys.v1.memory.delete — hard-delete syscall contract.

Covers the handler behavior (node_id required, tenant user_id threaded to the DAO,
idempotent deleted:false on miss/other-tenant, result shape) and the registration /
scope contract (dedicated memory.delete capability + scope, NOT authorized by
memory.write). The DAO delete_by_id + DB cascade is exercised against real Postgres
separately (PG-typed columns); this suite is DB-free via a mocked DAO.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only


def _ctx(user_id="user-123"):
    ctx = MagicMock()
    ctx.user_id = user_id
    ctx.execution_unit_id = "eu-1"
    return ctx


def _call_delete(payload, dao_return):
    from AINDY.kernel import syscall_registry

    captured: dict = {}

    class _FakeDAO:
        def __init__(self, _db):
            pass

        def delete_by_id(self, node_id, user_id=None):
            captured["node_id"] = node_id
            captured["user_id"] = user_id
            return dao_return

    with patch("AINDY.db.dao.memory_node_dao.MemoryNodeDAO", _FakeDAO), patch.object(
        syscall_registry, "_acquire_handler_db", return_value=(MagicMock(), True)
    ), patch.object(syscall_registry, "_finish_handler_write"):
        result = syscall_registry._handle_memory_delete(payload, _ctx())
    return result, captured


def test_delete_requires_node_id():
    from AINDY.kernel import syscall_registry

    with pytest.raises(ValueError):
        syscall_registry._handle_memory_delete({}, _ctx())


def test_delete_existing_node_returns_true_and_threads_tenant():
    result, captured = _call_delete({"node_id": "abc-123"}, dao_return=True)
    assert result == {"deleted": True, "node_id": "abc-123"}
    # Tenant isolation: the caller's user_id must reach the DAO scope filter.
    assert captured == {"node_id": "abc-123", "user_id": "user-123"}


def test_delete_missing_or_other_tenant_returns_false_no_error():
    # DAO returns False for a missing node OR a node owned by another tenant.
    result, _ = _call_delete({"node_id": "not-mine"}, dao_return=False)
    assert result == {"deleted": False, "node_id": "not-mine"}


def test_registration_capability_and_schema():
    from AINDY.kernel import syscall_registry

    entry = syscall_registry.SYSCALL_REGISTRY["sys.v1.memory.delete"]
    assert entry.capability == "memory.delete"
    assert entry.input_schema["required"] == ["node_id"]


def test_delete_scope_is_dedicated_not_satisfied_by_write():
    """memory.delete must require its own scope — write access must NOT grant delete."""
    from AINDY.routes.platform.platform_ops_router import _DISPATCH_CAPABILITY_SCOPES
    from AINDY.auth.api_key_auth import Scopes

    authorizing = _DISPATCH_CAPABILITY_SCOPES["memory.delete"]
    assert authorizing == {Scopes.MEMORY_DELETE}
    assert Scopes.MEMORY_WRITE not in authorizing
    # And the scope is a real, grantable scope (not in any default-on set here —
    # operators grant it explicitly).
    assert Scopes.MEMORY_DELETE == "memory.delete"
    assert Scopes.MEMORY_DELETE in Scopes.ALL
