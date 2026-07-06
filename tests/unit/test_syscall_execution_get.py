"""
Unit tests for the sys.v1.execution.get syscall handler.

Covers the SDK-facing execution-introspection contract (aindy-sdk
``client.execution.get``): tenant-scoped lookup by ExecutionUnit id, by soft
source_id, and by flow_run_id; not-found and cross-tenant isolation.
"""
from __future__ import annotations

import uuid

import pytest

from AINDY.kernel.syscall_registry import (
    SYSCALL_REGISTRY,
    SyscallContext,
    _handle_execution_get,
)

pytestmark = pytest.mark.runtime_only


def _ctx(user_id: str, db) -> SyscallContext:
    return SyscallContext(
        execution_unit_id="eu-test",
        user_id=user_id,
        capabilities=["execution.read"],
        trace_id="trace-test",
        metadata={"_db": db},
    )


def _insert_eu(db, *, user_id, source_id=None, flow_run_id=None, status="completed"):
    from AINDY.db.models import ExecutionUnit

    eu = ExecutionUnit(
        type="flow",
        status=status,
        user_id=uuid.UUID(user_id),
        source_type="flow_run" if source_id else None,
        source_id=source_id,
        flow_run_id=flow_run_id,
        wall_time_ms=1234,
        syscall_count=7,
        priority="normal",
    )
    db.add(eu)
    db.flush()
    return eu


def test_registered_stable_with_execution_read_capability():
    entry = SYSCALL_REGISTRY["sys.v1.execution.get"]
    assert entry.capability == "execution.read"
    assert entry.stable is True


def test_get_by_execution_unit_id(db_session):
    user_id = str(uuid.uuid4())
    eu = _insert_eu(db_session, user_id=user_id)

    result = _handle_execution_get({"execution_id": str(eu.id)}, _ctx(user_id, db_session))

    assert result["execution_id"] == str(eu.id)
    assert result["status"] == "completed"
    assert result["syscall_count"] == 7
    assert result["wall_time_ms"] == 1234
    assert result["priority"] == "normal"


def test_get_by_source_id(db_session):
    user_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    _insert_eu(db_session, user_id=user_id, source_id=run_id, status="executing")

    result = _handle_execution_get({"execution_id": run_id}, _ctx(user_id, db_session))

    assert result["status"] == "executing"
    assert result["source_id"] == run_id


def test_get_by_flow_run_id(db_session):
    user_id = str(uuid.uuid4())
    flow_run_id = "flow-run-" + uuid.uuid4().hex
    _insert_eu(db_session, user_id=user_id, flow_run_id=flow_run_id, status="waiting")

    result = _handle_execution_get({"execution_id": flow_run_id}, _ctx(user_id, db_session))

    assert result["status"] == "waiting"


def test_missing_execution_id_raises():
    with pytest.raises(ValueError, match="requires 'execution_id'"):
        _handle_execution_get({}, _ctx(str(uuid.uuid4()), db=object()))


def test_not_found_raises(db_session):
    user_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match="no execution unit found"):
        _handle_execution_get(
            {"execution_id": str(uuid.uuid4())}, _ctx(user_id, db_session)
        )


def test_cross_tenant_isolation(db_session):
    owner_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    eu = _insert_eu(db_session, user_id=owner_id)

    # Another tenant must not be able to read the owner's execution unit.
    with pytest.raises(ValueError, match="no execution unit found"):
        _handle_execution_get({"execution_id": str(eu.id)}, _ctx(other_id, db_session))
