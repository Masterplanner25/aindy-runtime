"""
Regression test for MEM-NODETYPE-1.

Two memory write-path defaults shipped ``node_type="execution"`` — a value the
``before_insert``/``before_update`` validator in ``memory_persistence.py`` rejects
(``VALID_NODE_TYPES`` omits "execution"). Every default ``memory.write`` therefore
raised ``ValueError`` at persist time, blocking the execute half of the planner loop.

The two offending sites:
  - AINDY/kernel/syscall_registry.py  — sys.v1.memory.write handler
  - AINDY/runtime/nodus_builtins.py    — Nodus script `memory.write` builtin

Both now default to "insight" (a member of VALID_NODE_TYPES, and the same value the
scorer falls back to when type is unspecified — so a defaulted write ranks identically
to an untyped one). This test pins the contract: no write-path default may produce a
node_type the validator rejects.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only


def test_execution_is_rejected_and_insight_is_accepted_by_validator():
    """Documents the original defect: "execution" raises; "insight" passes."""
    from AINDY.memory.memory_persistence import (
        VALID_NODE_TYPES,
        MemoryNodeModel,
        validate_node_type,
    )

    assert "execution" not in VALID_NODE_TYPES
    assert "insight" in VALID_NODE_TYPES

    with pytest.raises(ValueError):
        validate_node_type(None, None, MemoryNodeModel(node_type="execution"))

    # Must not raise.
    validate_node_type(None, None, MemoryNodeModel(node_type="insight"))


def test_nodus_builtin_write_default_is_valid():
    """The Nodus `memory.write` builtin default must be a valid node_type."""
    from AINDY.memory.memory_persistence import VALID_NODE_TYPES
    from AINDY.runtime.nodus_builtins import NodusMemoryBuiltins

    default = inspect.signature(NodusMemoryBuiltins.write).parameters["node_type"].default
    assert default in VALID_NODE_TYPES, (
        f"NodusMemoryBuiltins.write defaults node_type={default!r}, "
        f"which the persistence validator rejects."
    )


def test_syscall_memory_write_default_is_valid():
    """The sys.v1.memory.write handler default must reach the DAO as a valid node_type."""
    from AINDY.memory.memory_persistence import VALID_NODE_TYPES
    from AINDY.kernel import syscall_registry

    captured: dict = {}

    class _FakeDAO:
        def __init__(self, _db):
            pass

        def save(self, **kwargs):
            captured.update(kwargs)
            return {"id": "node-1"}

    ctx = MagicMock()
    ctx.user_id = "user-123"
    ctx.execution_unit_id = "eu-1"

    with patch("AINDY.db.dao.memory_node_dao.MemoryNodeDAO", _FakeDAO), patch.object(
        syscall_registry, "_acquire_handler_db", return_value=(MagicMock(), True)
    ), patch.object(syscall_registry, "_finish_handler_write"), patch(
        "AINDY.memory.memory_address_space.path_from_write_payload",
        return_value=("/memory/user-123/general/insight/x", "general", "insight"),
    ):
        # Payload deliberately omits node_type so the handler default applies.
        syscall_registry._handle_memory_write({"content": "hello"}, ctx)

    assert captured.get("node_type") in VALID_NODE_TYPES, (
        f"sys.v1.memory.write defaulted node_type={captured.get('node_type')!r}, "
        f"which the persistence validator rejects."
    )
