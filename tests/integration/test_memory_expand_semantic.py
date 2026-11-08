"""MEM-EXPAND-DEAD-1 (DEC-122) — the semantic half of ``MemoryNodeDAO.expand()``, on real Postgres.

It returned ``[]`` on every call for as long as it existed: pgvector 0.4.2 read a stored embedding
back as a ``numpy.ndarray`` and ``_embedding_is_usable`` accepts only a ``list``. No test noticed in
either direction. pgvector 0.5.x reads a ``list``, so the half is now behind
``AINDY_MEMORY_EXPAND_SIMILAR`` (default off) with a similarity floor.

Only Postgres runs the pgvector ``<=>`` query; SQLite would test the Python fallback alone.
"""
from __future__ import annotations

import uuid

import pytest

from tests.integration.work_counter import count_work

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(test_engine):
    if test_engine.dialect.name != "postgresql":
        pytest.skip("the pgvector query runs on Postgres only")
    return test_engine


@pytest.fixture
def switch(monkeypatch):
    def _set(on: bool, floor: str | None = None):
        monkeypatch.setenv("AINDY_MEMORY_EXPAND_SIMILAR", "true" if on else "false")
        if floor is None:
            monkeypatch.delenv("AINDY_MEMORY_EXPAND_MIN_SIMILARITY", raising=False)
        else:
            monkeypatch.setenv("AINDY_MEMORY_EXPAND_MIN_SIMILARITY", floor)

    return _set


def _vec(i: int, j: int | None = None) -> list[float]:
    from AINDY.memory.embedding_providers import resolve_embedding_column_dimensions

    v = [0.0] * resolve_embedding_column_dimensions()
    v[i] = 1.0
    if j is not None:
        v[j] = 0.2  # cosine ~0.98 with _vec(i): similarity ~0.99
    return v


def _node(session, user_id, content: str, embedding: list[float]) -> str:
    from AINDY.memory.memory_persistence import MemoryNodeModel

    node = MemoryNodeModel(content=content, tags=[], node_type="insight", memory_type="insight",
                           user_id=user_id, embedding=embedding,
                           embedding_pending=False, embedding_status="complete")
    session.add(node)
    session.flush()
    return str(node.id)


def _other_user(session):
    from AINDY.db.models.user import User

    user = User(email=f"other-{uuid.uuid4().hex[:8]}@aindy.test", username=f"other-{uuid.uuid4().hex[:8]}",
                hashed_password="x", is_active=True)
    session.add(user)
    session.flush()
    return user


@pytest.fixture
def nodes(engine, db_session, test_user):
    """``a``; ``near`` it (similarity ~0.99); ``far`` (orthogonal: similarity 0.5)."""
    return {
        "a": _node(db_session, test_user.id, "a", _vec(0)),
        "near": _node(db_session, test_user.id, "near", _vec(0, 1)),
        "far": _node(db_session, test_user.id, "far", _vec(7)),
    }


def _similar(db_session, user, node_id, **kwargs) -> list[str]:
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    out = MemoryNodeDAO(db_session).expand([node_id], user_id=str(user), include_linked=False, **kwargs)
    return out["expansion_map"][node_id]["similar"]


# ── the dependency fact the whole feature rests on ─────────────────────────────────────────


def test_a_stored_embedding_reads_back_as_a_list(db_session, nodes):
    """If a pgvector release changes this again, the feature switches itself off silently.
    This is the line that says so."""
    from AINDY.memory.memory_persistence import MemoryNodeModel

    db_session.expire_all()
    stored = db_session.get(MemoryNodeModel, uuid.UUID(nodes["a"])).embedding
    assert type(stored) is list, type(stored)


# ── the switch ─────────────────────────────────────────────────────────────────────────────


def test_switch_off_returns_nothing_and_queries_nothing(engine, db_session, test_user, nodes, switch):
    switch(False)
    with count_work(engine) as work:
        similar = _similar(db_session, test_user.id, nodes["a"])
    assert similar == []
    assert work.queries == 0, work.statements


def test_switch_on_returns_the_near_node_and_the_floor_drops_the_far_one(db_session, test_user, nodes, switch):
    names = {v: k for k, v in nodes.items()}

    switch(True, floor="0")
    assert sorted(names[i] for i in _similar(db_session, test_user.id, nodes["a"])) == ["far", "near"], (
        "control: with no floor the orthogonal node is returned, so the default is what excludes it"
    )

    switch(True)
    assert [names[i] for i in _similar(db_session, test_user.id, nodes["a"])] == ["near"]


def test_a_floor_out_of_range_uses_the_default(db_session, test_user, nodes, switch):
    switch(True, floor="7")
    assert _similar(db_session, test_user.id, nodes["a"]) == [nodes["near"]]


# ── ownership ──────────────────────────────────────────────────────────────────────────────


def test_another_users_node_is_never_returned(db_session, test_user, nodes, switch):
    other = _other_user(db_session)
    theirs = _node(db_session, other.id, "theirs", _vec(0))  # identical to `a`: the nearest there is
    mine_near_theirs = _node(db_session, other.id, "their near", _vec(0, 1))
    switch(True)

    assert _similar(db_session, other.id, theirs) == [mine_near_theirs], (
        "control: their own expansion finds their node, so the query can see the row"
    )
    assert theirs not in _similar(db_session, test_user.id, nodes["a"])
    assert _similar(db_session, test_user.id, theirs) == [], "expanding someone else's node yields nothing"


# ── the Python fallback ────────────────────────────────────────────────────────────────────


def test_the_python_fallback_ranks_the_same(db_session, test_user, nodes, switch, monkeypatch):
    """It ran `list(embedding or [])`, which raises on an ndarray. It runs when the pgvector query
    raises; forcing the non-Postgres branch reaches the same code."""
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    switch(True)
    monkeypatch.setattr(MemoryNodeDAO, "_is_postgres", lambda self: False)
    assert _similar(db_session, test_user.id, nodes["a"]) == [nodes["near"]]
