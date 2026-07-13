"""
RTR-4 gap (c): delegation-token-scoped private memory.

A delegated child run's memory writes are stamped with its ``owner_run_id`` and
are visible only to reads carrying the same run scope — never to the parent or a
sibling delegate. Tenant-shared nodes (``owner_run_id IS NULL``) stay visible to
everyone in the tenant. The whole feature is gated on
``AINDY_DELEGATION_PRIVATE_MEMORY`` (default off): when off, writes stamp NULL and
reads ignore the run-scope clause, so behavior is identical to pre-feature.

One ContextVar (``set_owner_run_id``) carries the active run's scope for both the
write path (``resolve_owner_run_id`` → stamped in the DAO) and the read path
(``apply_memory_owner_scope`` self-resolves it). Set only for delegate runs, at
the execution boundary.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest

from AINDY.memory import memory_persistence as mp
from AINDY.memory.memory_persistence import (
    MemoryNodeModel,
    apply_memory_owner_scope,
    resolve_owner_run_id,
    set_owner_run_id,
    reset_owner_run_id,
    get_owner_run_id,
)

pytestmark = pytest.mark.runtime_only


@contextmanager
def _flag(monkeypatch, on: bool):
    monkeypatch.setattr(mp, "delegation_private_memory_enabled", lambda: on)
    yield


@contextmanager
def _run_scope(run_id):
    token = set_owner_run_id(run_id)
    try:
        yield
    finally:
        reset_owner_run_id(token)


# ─────────────────────────── resolve_owner_run_id (pure) ───────────────────────

def test_resolve_is_none_when_flag_off(monkeypatch):
    with _flag(monkeypatch, False), _run_scope("11111111-1111-1111-1111-111111111111"):
        assert resolve_owner_run_id() is None


def test_resolve_precedence(monkeypatch):
    run = "22222222-2222-2222-2222-222222222222"
    with _flag(monkeypatch, True):
        # explicit opt-out wins over an active scope
        with _run_scope(run):
            assert resolve_owner_run_id(private_to_run=False) is None
            # escape hatch: publishing shared/global is not private
            assert resolve_owner_run_id(visibility="shared") is None
            assert resolve_owner_run_id(visibility="global") is None
            # explicit run id beats the ContextVar
            explicit = "33333333-3333-3333-3333-333333333333"
            assert resolve_owner_run_id(explicit=explicit) == uuid.UUID(explicit)
            # otherwise: the ContextVar (delegate run)
            assert resolve_owner_run_id() == uuid.UUID(run)
        # no scope → tenant-shared
        assert resolve_owner_run_id() is None


def test_contextvar_set_reset_roundtrip():
    assert get_owner_run_id() is None
    with _run_scope("44444444-4444-4444-4444-444444444444"):
        assert get_owner_run_id() == "44444444-4444-4444-4444-444444444444"
    assert get_owner_run_id() is None


# ─────────────────── apply_memory_owner_scope SQL (flag on/off) ────────────────

def _where(query) -> str:
    sql = " ".join(str(query.statement.compile()).split())
    return sql.split("WHERE", 1)[1].strip() if "WHERE" in sql else ""


def _q():
    from AINDY.db.database import SessionLocal

    return SessionLocal().query(MemoryNodeModel)


def test_read_scope_flag_off_has_no_run_clause(monkeypatch):
    with _flag(monkeypatch, False), _run_scope("55555555-5555-5555-5555-555555555555"):
        where = _where(apply_memory_owner_scope(_q(), owner_user_id=uuid.uuid4()))
        assert "owner_run_id" not in where


def test_read_scope_flag_on_adds_run_clause_from_contextvar(monkeypatch):
    run = "66666666-6666-6666-6666-666666666666"
    with _flag(monkeypatch, True), _run_scope(run):
        where = _where(apply_memory_owner_scope(_q(), owner_user_id=uuid.uuid4()))
        assert "owner_run_id IS NULL" in where
        assert "owner_run_id =" in where


def test_read_scope_flag_on_no_run_sees_only_shared(monkeypatch):
    with _flag(monkeypatch, True):  # no run scope
        where = _where(apply_memory_owner_scope(_q(), owner_user_id=uuid.uuid4()))
        assert "owner_run_id IS NULL" in where
        assert "owner_run_id =" not in where


# ──────────────────────────── persistence + isolation ─────────────────────────

def _save(dao, *, user_id, content, run_scope=None, visibility="private"):
    with _run_scope(run_scope):
        return dao.save(
            content=content,
            tags=["t"],
            user_id=user_id,
            node_type="insight",
            visibility=visibility,
            generate_embedding=False,
        )


def _ids_for_run(dao, *, user_id, run_scope):
    with _run_scope(run_scope):
        return {n["id"] for n in dao.get_by_tags(["t"], user_id=str(user_id))}


def test_delegate_memory_is_isolated_when_flag_on(monkeypatch, db_session):
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    dao = MemoryNodeDAO(db_session)
    user = str(uuid.uuid4())
    run_a = str(uuid.uuid4())
    run_b = str(uuid.uuid4())

    with _flag(monkeypatch, True):
        a = _save(dao, user_id=user, content="A private", run_scope=run_a)["id"]
        b = _save(dao, user_id=user, content="B private", run_scope=run_b)["id"]
        shared = _save(dao, user_id=user, content="tenant shared", run_scope=None)["id"]
        # delegate A publishes upward via visibility=shared → NOT run-private
        published = _save(
            dao, user_id=user, content="A published", run_scope=run_a, visibility="shared"
        )["id"]

        # run A sees its own private + shared + published, NOT run B's private
        seen_a = _ids_for_run(dao, user_id=user, run_scope=run_a)
        assert {a, shared, published} <= seen_a
        assert b not in seen_a

        # run B sees its own + shared + published, NOT run A's private
        seen_b = _ids_for_run(dao, user_id=user, run_scope=run_b)
        assert {b, shared, published} <= seen_b
        assert a not in seen_b

        # the parent (no run scope) sees only tenant-shared, neither delegate's private
        seen_parent = _ids_for_run(dao, user_id=user, run_scope=None)
        assert {shared, published} <= seen_parent
        assert a not in seen_parent
        assert b not in seen_parent


def test_owner_run_id_stamped_only_for_delegate_scope(monkeypatch, db_session):
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    dao = MemoryNodeDAO(db_session)
    user = str(uuid.uuid4())
    run = str(uuid.uuid4())

    with _flag(monkeypatch, True):
        priv = _save(dao, user_id=user, content="p", run_scope=run)["id"]
        shared = _save(dao, user_id=user, content="s", run_scope=None)["id"]

    row_priv = db_session.query(MemoryNodeModel).filter(
        MemoryNodeModel.id == uuid.UUID(priv)
    ).first()
    row_shared = db_session.query(MemoryNodeModel).filter(
        MemoryNodeModel.id == uuid.UUID(shared)
    ).first()
    assert str(row_priv.owner_run_id) == run
    assert row_shared.owner_run_id is None


def test_flag_off_never_stamps_and_read_unaffected(monkeypatch, db_session):
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    dao = MemoryNodeDAO(db_session)
    user = str(uuid.uuid4())
    run_a = str(uuid.uuid4())
    run_b = str(uuid.uuid4())

    with _flag(monkeypatch, False):
        a = _save(dao, user_id=user, content="A", run_scope=run_a)["id"]
        b = _save(dao, user_id=user, content="B", run_scope=run_b)["id"]

        # nothing stamped
        for nid in (a, b):
            row = db_session.query(MemoryNodeModel).filter(
                MemoryNodeModel.id == uuid.UUID(nid)
            ).first()
            assert row.owner_run_id is None

        # reads are unscoped by run — run A sees both (pre-feature behavior)
        seen_a = _ids_for_run(dao, user_id=user, run_scope=run_a)
        assert {a, b} <= seen_a
