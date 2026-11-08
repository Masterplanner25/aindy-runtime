"""MEM-PATH-NULL-1: a node saved without a path was invisible to every path tool.

`walk_path`, `memory.tree` and a wildcard `memory.list` filter on the stored column, and most
writers pass no path, so on the app's stack 1,371 of 1,397 nodes could not be found that way.
A node now stores the path readers were already shown for it (`derive_legacy_path`), at save
and, for existing rows, through `aindy-runtime memory backfill-paths`.
"""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

USER = "00000000-0000-0000-0000-00000000a0a1"
OTHER = "00000000-0000-0000-0000-00000000a0a2"


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "paths.db")
    f = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", f)
    try:
        yield f
    finally:
        engine.dispose()


def _save(factory, **kwargs) -> dict:
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    db = factory()
    try:
        node = MemoryNodeDAO(db).save(generate_embedding=False, **kwargs)
        db.commit()
        return node
    finally:
        db.close()


def _walk(factory, user=USER) -> list[dict]:
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    db = factory()
    try:
        return MemoryNodeDAO(db).walk_path(f"/memory/{user}", user_id=user)
    finally:
        db.close()


def _tree_count(user=USER) -> int:
    from AINDY.kernel.syscall_dispatcher import get_dispatcher
    from AINDY.kernel.syscall_registry import SyscallContext

    ctx = SyscallContext(
        execution_unit_id=str(uuid.uuid4()), user_id=user, capabilities=["memory.read"], trace_id="t"
    )
    result = get_dispatcher().dispatch("sys.v1.memory.tree", {"path": f"/memory/{user}/**"}, ctx)
    assert result["status"] == "success", result
    return result["data"]["node_count"]


# -- at save ------------------------------------------------------------------------------------


def test_a_node_saved_without_a_path_stores_the_one_readers_were_shown(factory):
    from AINDY.memory.memory_address_space import derive_legacy_path

    node = _save(factory, content="captured", user_id=USER, node_type="insight", memory_type="insight")

    assert node["path"] == derive_legacy_path({**node, "path": None})
    assert node["path"] == f"/memory/{USER}/_legacy/insight/{node['id']}"
    assert node["namespace"] == "_legacy"
    assert node["addr_type"] == "insight"
    assert node["parent_path"] == f"/memory/{USER}/_legacy/insight"


def test_the_path_tools_can_now_find_it(factory):
    node = _save(factory, content="captured", user_id=USER, node_type="insight")

    assert [n["id"] for n in _walk(factory)] == [node["id"]]
    assert _tree_count() == 1


def test_control_an_explicit_path_is_kept(factory):
    path = f"/memory/{USER}/notes/idea/{uuid.uuid4()}"
    node = _save(factory, content="written", user_id=USER, node_type="insight", path=path, namespace="notes", addr_type="idea")

    assert node["path"] == path
    assert node["namespace"] == "notes"


def test_a_node_with_no_owner_gets_no_path(factory):
    """There is no tenant to put in the path. It stays unreachable by path, as before."""
    node = _save(factory, content="system", user_id=None, node_type="insight")

    assert node["path"] is None


def test_another_users_tree_does_not_see_it(factory):
    _save(factory, content="mine", user_id=USER, node_type="insight")

    assert _walk(factory, user=OTHER) == []
    assert _tree_count(user=OTHER) == 0


# -- backfill -----------------------------------------------------------------------------------


def _seed_null_path_rows(factory, n: int, user=USER) -> list[str]:
    """Rows as every pre-fix writer left them: owned, but with no path."""
    from AINDY.memory.memory_persistence import MemoryNodeModel

    db = factory()
    try:
        ids = []
        for i in range(n):
            row = MemoryNodeModel(
                id=uuid.uuid4(), content=f"old {i}", tags=[], node_type="outcome", memory_type="outcome",
                user_id=uuid.UUID(user), extra={}, embedding_pending=False, embedding_status="complete",
            )
            db.add(row)
            ids.append(str(row.id))
        db.commit()
        return ids
    finally:
        db.close()


def _paths(factory) -> dict[str, str | None]:
    from AINDY.memory.memory_persistence import MemoryNodeModel

    db = factory()
    try:
        return {str(r.id): r.path for r in db.query(MemoryNodeModel.id, MemoryNodeModel.path).all()}
    finally:
        db.close()


def test_control_the_old_rows_are_invisible_before_the_backfill(factory):
    _seed_null_path_rows(factory, 3)

    assert _walk(factory) == []
    assert _tree_count() == 0


def test_a_dry_run_counts_and_changes_nothing(factory):
    from AINDY.memory.memory_path_backfill import backfill_memory_paths

    _seed_null_path_rows(factory, 3)
    db = factory()
    try:
        report = backfill_memory_paths(db, dry_run=True, batch_size=2)
    finally:
        db.close()

    assert report["would_fill"] == 3
    assert report["filled"] == 0
    assert all(p is None for p in _paths(factory).values())


def test_the_backfill_stores_the_shown_path_in_batches(factory):
    from AINDY.memory.memory_path_backfill import backfill_memory_paths

    ids = _seed_null_path_rows(factory, 5)
    db = factory()
    try:
        report = backfill_memory_paths(db, dry_run=False, batch_size=2)
    finally:
        db.close()

    assert report["filled"] == 5
    assert report["batches"] == 3
    paths = _paths(factory)
    assert all(paths[i] == f"/memory/{USER}/_legacy/outcome/{i}" for i in ids)
    assert len(_walk(factory)) == 5
    assert _tree_count() == 5


def test_the_backfill_leaves_ownerless_and_already_pathed_rows_alone(factory):
    from AINDY.memory.memory_path_backfill import backfill_memory_paths

    kept = _save(factory, content="has a path", user_id=USER, node_type="insight",
                 path=f"/memory/{USER}/notes/idea/x1", namespace="notes", addr_type="idea")
    ownerless = _save(factory, content="system", user_id=None, node_type="insight")
    _seed_null_path_rows(factory, 1)

    db = factory()
    try:
        report = backfill_memory_paths(db, dry_run=False)
    finally:
        db.close()

    paths = _paths(factory)
    assert report["filled"] == 1
    assert report["skipped_no_owner"] == 1
    assert paths[kept["id"]] == f"/memory/{USER}/notes/idea/x1"
    assert paths[ownerless["id"]] is None


def test_running_it_twice_changes_nothing_the_second_time(factory):
    from AINDY.memory.memory_path_backfill import backfill_memory_paths

    _seed_null_path_rows(factory, 2)
    db = factory()
    try:
        backfill_memory_paths(db, dry_run=False)
        again = backfill_memory_paths(db, dry_run=False)
    finally:
        db.close()

    assert again["filled"] == 0
    assert again["would_fill"] == 0


def test_a_row_that_will_not_take_a_path_is_reported_not_looped_on(factory, monkeypatch):
    """The walk moves past every row it reads. Re-querying "rows with no path" would spin forever
    on a row that never takes one."""
    from AINDY.memory import memory_address_space
    from AINDY.memory.memory_path_backfill import backfill_memory_paths

    ids = _seed_null_path_rows(factory, 3)
    real = memory_address_space.stored_path_fields

    def _refuses_one(*, user_id, memory_type, node_id):
        fields = real(user_id=user_id, memory_type=memory_type, node_id=node_id)
        return {**fields, "path": None} if node_id == ids[1] else fields

    monkeypatch.setattr(memory_address_space, "stored_path_fields", _refuses_one)
    outcome: dict = {}

    def _run():
        db = factory()
        try:
            outcome["report"] = backfill_memory_paths(db, dry_run=False, batch_size=1)
        finally:
            db.close()

    # A daemon thread with a deadline, so a loop fails this test instead of hanging the suite.
    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout=20)
    assert not worker.is_alive(), "the backfill did not finish: it is looping on a row with no path"
    report = outcome["report"]

    assert report["filled"] == 2
    assert report["not_filled"] == 1
    assert report["batches"] == 3


def test_the_command_is_registered_and_dry_runs_by_default(factory, capsys):
    from AINDY import runtime_only

    _seed_null_path_rows(factory, 2)
    with pytest.raises(SystemExit) as done:
        runtime_only._backfill_memory_paths(yes=False, batch_size=500)

    assert done.value.code == 0
    assert "dry-run: 2" in capsys.readouterr().out
    assert all(p is None for p in _paths(factory).values())
