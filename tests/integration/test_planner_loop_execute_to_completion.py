"""
tests/integration/test_planner_loop_execute_to_completion.py
────────────────────────────────────────────────────────────
Execute-to-completion verification for MEM-NODETYPE-1 against real PostgreSQL.

The runtime_local planner almost always plans a memory write first, so the
execute half of the agent loop persists a memory node. Every default node_type
across the write paths was "execution", which VALID_NODE_TYPES rejects — the
before_insert validator raised ValueError at persist time. In the script paths
the rejected save is swallowed (logger.warning + continue / return None), so the
script reports completion while the memory node silently vanishes.

These tests drive each real write path with a *default* node_type and assert the
node actually lands in PostgreSQL with a valid type ("insight"):

  1. dispatcher syscall        — sys.v1.memory.write via SyscallDispatcher
  2. adapter deferred persist  — NodusRuntimeAdapter._apply_deferred_memory_writes
  3. Nodus `remember` builtin  — AINDYMemoryBridge.remember (in-subprocess dao.save)
  4. full subprocess VM run    — execute_nodus_runtime running a remember() script

Harness note: the write paths each open their own SessionLocal and commit to the
real database. The shared db_session/test_user fixtures live in a rolled-back
savepoint on a single connection, so a separate-connection write cannot satisfy
the memory_nodes.user_id FK and its commit is invisible cross-connection. This
module therefore manages a real-committed user (with cleanup) and verifies via
fresh sessions.

Requires: docker-compose -f docker-compose.test.yml up -d  (DATABASE_URL → PostgreSQL).
"""
from __future__ import annotations

import types
import uuid

import pytest

pytestmark = [pytest.mark.integration]


@pytest.fixture
def real_user():
    """A user committed to real PostgreSQL so separate-connection write paths
    satisfy the memory_nodes.user_id FK. Yields the user id.

    No per-test row deletion: the write paths emit FK-referencing rows
    (system_events, execution_units, …) that make a targeted user DELETE brittle.
    Each test uses a unique user and unique tags, and the session-scoped
    _setup_postgres_schema fixture drop_all's every table at session teardown
    (the test database is ephemeral tmpfs), so leftover rows are harmless.
    """
    from AINDY.db.database import SessionLocal
    from AINDY.db.models.user import User
    from AINDY.services.auth_service import hash_password

    session = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"e2e-{suffix}@aindy.test",
            username=f"e2e-{suffix}",
            hashed_password=hash_password("e2e-password"),
            is_active=True,
            is_admin=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = user.id
    finally:
        session.close()

    return user_id


def _fresh_nodes_for_tag(user_id, tag):
    """Read persisted nodes via an independent committed-visible session."""
    from AINDY.db.database import SessionLocal
    from AINDY.memory.memory_persistence import MemoryNodeModel

    session = SessionLocal()
    try:
        rows = (
            session.query(MemoryNodeModel)
            .filter(MemoryNodeModel.user_id == user_id)
            .all()
        )
        return [
            {"id": str(r.id), "node_type": r.node_type, "tags": list(r.tags or [])}
            for r in rows
            if tag in (r.tags or [])
        ]
    finally:
        session.close()


def test_dispatcher_memory_write_default_persists_as_insight(real_user):
    """sys.v1.memory.write with no node_type, driven through SyscallDispatcher,
    must succeed and persist a node with a valid node_type."""
    from AINDY.db.database import SessionLocal
    from AINDY.kernel.syscall_dispatcher import dispatch_syscall

    tag = f"mem-nodetype-dispatch-{uuid.uuid4().hex[:8]}"
    session = SessionLocal()
    try:
        envelope = dispatch_syscall(
            "sys.v1.memory.write",
            {"content": "dispatcher execute-to-completion probe", "tags": [tag]},
            db=session,
            user_id=str(real_user),
        )
    finally:
        session.close()

    assert envelope.get("status") == "success", (
        f"default memory.write did not succeed: {envelope.get('error') or envelope}"
    )
    matched = _fresh_nodes_for_tag(real_user, tag)
    assert len(matched) == 1, f"expected one persisted node, got {len(matched)}"
    assert matched[0]["node_type"] == "insight"


def test_adapter_deferred_write_default_persists_as_insight(real_user):
    """Adapter persistence path: a deferred write dict lacking node_type must be
    saved with a valid node_type — the exact dao.save the flow engine runs for
    collected deferred writes."""
    from AINDY.db.database import SessionLocal
    from AINDY.runtime.nodus_runtime_adapter import _apply_deferred_memory_writes

    tag = f"mem-nodetype-adapter-{uuid.uuid4().hex[:8]}"
    write = {
        "kind": "memory.write",
        "content": "deferred execute-to-completion probe",
        "tags": [tag],
        "significance": 0.5,
        "user_id": str(real_user),
    }  # node_type deliberately omitted → persistence default applies
    context = types.SimpleNamespace(user_id=str(real_user))

    session = SessionLocal()
    try:
        _apply_deferred_memory_writes(session, [write], context)
    finally:
        session.close()

    matched = _fresh_nodes_for_tag(real_user, tag)
    assert len(matched) == 1, f"expected one persisted node, got {len(matched)}"
    assert matched[0]["node_type"] == "insight", (
        f"deferred write persisted node_type={matched[0]['node_type']!r}"
    )


def test_bridge_remember_default_persists_as_insight(real_user):
    """The Nodus `remember` builtin (AINDYMemoryBridge.remember) persists directly
    via dao.save; with no node_type it must default to a valid type and return an
    id — not swallow a ValueError and return None."""
    from AINDY.nodus.runtime.memory_bridge import AINDYMemoryBridge

    tag = f"mem-nodetype-remember-{uuid.uuid4().hex[:8]}"
    bridge = AINDYMemoryBridge(user_id=str(real_user))

    node_id = bridge.remember(content="remember default probe", tags=[tag])

    assert node_id, "remember() returned None — the write was rejected and swallowed"
    matched = _fresh_nodes_for_tag(real_user, tag)
    assert len(matched) == 1, f"expected one persisted node, got {len(matched)}"
    assert matched[0]["node_type"] == "insight"


def test_subprocess_script_remember_runs_to_completion(real_user):
    """Full deferred path: a Nodus script calling remember() with a falsy node_type
    must run to completion in the subprocess VM AND land a row in PostgreSQL with a
    valid node_type. This is the planner loop's execute half, end-to-end."""
    from AINDY.db.database import SessionLocal
    from AINDY.runtime.nodus_execution_service import execute_nodus_runtime

    tag = f"mem-nodetype-e2e-{uuid.uuid4().hex[:8]}"
    # remember(content, node_type, tags); "" is falsy → bridge applies its default.
    script = f'node = remember("execute-to-completion probe", "", ["{tag}"])\n'

    session = SessionLocal()
    try:
        result = execute_nodus_runtime(
            db=session,
            user_id=str(real_user),
            execution_unit_id=f"eu-{uuid.uuid4().hex[:8]}",
            script=script,
        )
    finally:
        session.close()

    status = getattr(result, "status", None)
    assert status in ("success", "completed"), (
        f"Nodus execution did not complete: status={status}, "
        f"error={getattr(result, 'error', None)}"
    )
    matched = _fresh_nodes_for_tag(real_user, tag)
    assert len(matched) == 1, (
        f"expected one persisted node from the script write, got {len(matched)}"
    )
    assert matched[0]["node_type"] == "insight"
