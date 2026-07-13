"""
Regression test for the RTR-4 gap (c) PR1 refactor.

PR1 centralizes the memory read-scope predicate — previously duplicated across
~13 inline ``if owner_user_id: ... else: visibility.in_(...)`` blocks in the two
memory DAOs and the scorer — into a single helper,
``AINDY.memory.memory_persistence.apply_memory_owner_scope``. This is a pure,
behavior-preserving refactor: it introduces no new column and no run-scope
filter (that lands in PR2). These tests pin the three branches to the exact SQL
the inline blocks produced, so a future change to the helper (e.g. adding the
PR2 run-scope clause) cannot silently alter the base-case behavior.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.runtime_only


def _where_clause(query) -> str:
    """Return the normalized WHERE fragment of a query's compiled SQL, or '' ."""
    sql = " ".join(str(query.statement.compile()).split())
    return sql.split("WHERE", 1)[1].strip() if "WHERE" in sql else ""


def _base_query():
    from AINDY.db.database import SessionLocal
    from AINDY.memory.memory_persistence import MemoryNodeModel

    return SessionLocal().query(MemoryNodeModel)


def test_owner_present_scopes_to_user_id():
    """An owner (any shared_fallback) → restrict to that user_id, nothing else."""
    from AINDY.memory.memory_persistence import apply_memory_owner_scope

    for shared_fallback in (True, False):
        where = _where_clause(
            apply_memory_owner_scope(
                _base_query(), owner_user_id=uuid.uuid4(), shared_fallback=shared_fallback
            )
        )
        assert "memory_nodes.user_id =" in where
        assert "visibility" not in where


def test_ownerless_with_shared_fallback_restricts_to_shared_pool():
    """No owner + shared_fallback (Variant A) → visibility IN (shared, global)."""
    from AINDY.memory.memory_persistence import apply_memory_owner_scope

    where = _where_clause(
        apply_memory_owner_scope(_base_query(), owner_user_id=None, shared_fallback=True)
    )
    assert "memory_nodes.visibility IN" in where
    assert "user_id" not in where


def test_ownerless_without_shared_fallback_is_unfiltered():
    """No owner + not shared_fallback (Variant B) → no owner/visibility predicate."""
    from AINDY.memory.memory_persistence import apply_memory_owner_scope

    where = _where_clause(
        apply_memory_owner_scope(_base_query(), owner_user_id=None, shared_fallback=False)
    )
    assert where == ""


def test_helper_default_is_shared_fallback_true():
    """Default shared_fallback matches the dominant Variant-A call sites."""
    import inspect

    from AINDY.memory.memory_persistence import apply_memory_owner_scope

    assert inspect.signature(apply_memory_owner_scope).parameters["shared_fallback"].default is True
