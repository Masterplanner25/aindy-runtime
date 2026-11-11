"""FR-67 (DEC-131): a memory node's path ends with its own id.

Found by claude.ai reading a node over MCP during FR-66's D7: node `488317bc-…` sat at
`/memory/<owner>/general/insight/70494ab4-…`. `path_from_write_payload` minted a UUID for the leaf
and threw it away (`full_path, _ = generate_node_path(...)`), and `MemoryNodeDAO.save` stored the
row under a uuid4 of its own. 81 of the app's 1,863 pathed nodes. Reads by path worked; anything
that took the id from the path got an id that did not exist.

The fix sits at the chokepoint: `save()` takes the row id FROM the path's leaf, so no writer can
drift again. A caller-supplied leaf is refused (`invalid`). `memory backfill-paths` rewrites the
old rows' PATHS to end with their id; ids never change.

Every write here goes through the real dispatcher on a private engine, so a commit is a commit.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

USER = "00000000-0000-0000-0000-00000000f067"


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "fr67.db")
    f = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", f)
    try:
        yield f
    finally:
        engine.dispose()


def _write(payload: dict) -> dict:
    from AINDY.kernel.syscall_dispatcher import dispatch_syscall

    return dispatch_syscall(
        "sys.v1.memory.write", payload, user_id=USER, capability="memory.write",
        execution_unit_id=str(uuid.uuid4()),
    )


def _rows(factory) -> dict[str, dict]:
    from AINDY.memory.memory_persistence import MemoryNodeModel

    db = factory()
    try:
        return {
            str(r.id): {"path": r.path, "parent_path": r.parent_path, "content": r.content}
            for r in db.query(MemoryNodeModel).all()
        }
    finally:
        db.close()


def _leaf(path: str) -> str:
    return path.rsplit("/", 1)[1]


# -- at write -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "defaults"},
        {"content": "named", "namespace": "notes", "addr_type": "idea"},
        {"content": "type-level path", "path": f"/memory/{USER}/notes/idea"},
    ],
)
def test_a_written_node_is_stored_under_the_id_its_path_names(payload, factory):
    result = _write(payload)

    assert result["status"] == "success", result
    node = result["data"]["node"]
    assert _leaf(node["path"]) == node["id"], "the path's last segment is the node's id"
    assert result["data"]["path"] == node["path"]
    stored = _rows(factory)
    assert list(stored) == [node["id"]], "the row in the table is the one the path names"
    assert _leaf(stored[node["id"]]["path"]) == node["id"]


def test_the_id_taken_from_the_path_reads_the_node(factory):
    """What claude.ai did: take the id off the path and read it."""
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    node = _write({"content": "read me back"})["data"]["node"]

    db = factory()
    try:
        by_id = MemoryNodeDAO(db).get_by_id(_leaf(node["path"]), user_id=USER)
        by_path = MemoryNodeDAO(db).get_by_path(node["path"], user_id=USER)
    finally:
        db.close()
    assert by_id is not None and by_id["content"] == "read me back"
    assert by_path["id"] == by_id["id"]


@pytest.mark.parametrize("leaf", ["keep-me", str(uuid.uuid4())])
def test_a_caller_supplied_leaf_is_refused_as_invalid_and_nothing_is_written(leaf, factory):
    from AINDY.core.retry_policy import is_retryable_error

    result = _write({"content": "x", "path": f"/memory/{USER}/notes/idea/{leaf}"})

    assert result["status"] == "error", result
    assert result["failure_class"] == "invalid"
    assert is_retryable_error(result) is False
    assert _rows(factory) == {}


def test_control_the_same_path_without_a_leaf_is_written(factory):
    """Liveness for the refusal above: it is the leaf that is refused, not the path."""
    result = _write({"content": "x", "path": f"/memory/{USER}/notes/idea"})

    assert result["status"] == "success", result
    assert len(_rows(factory)) == 1


@pytest.mark.parametrize("path", [f"/memory/{USER}/notes/idea/not-a-uuid", f"/memory/{USER}/notes/idea"])
def test_the_dao_refuses_a_path_that_does_not_end_with_an_id(path, factory):
    """The chokepoint guard: a writer that bypasses the resolver cannot store a drifting path."""
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO
    from AINDY.memory.memory_address_space import InvalidMemoryPath

    db = factory()
    try:
        with pytest.raises(InvalidMemoryPath):
            MemoryNodeDAO(db).save(content="x", user_id=USER, node_type="insight",
                                   generate_embedding=False, path=path)
    finally:
        db.close()
    assert _rows(factory) == {}


# -- the backfill -------------------------------------------------------------------------------


def _seed(factory, path: str | None) -> str:
    """A row as the pre-fix resolver left it: its own id, a path naming another."""
    from AINDY.memory.memory_address_space import parent_path_of
    from AINDY.memory.memory_persistence import MemoryNodeModel

    db = factory()
    try:
        row = MemoryNodeModel(
            id=uuid.uuid4(), content="old", tags=[], node_type="insight", memory_type="insight",
            user_id=uuid.UUID(USER), extra={}, embedding_pending=False, embedding_status="complete",
            path=path, parent_path=parent_path_of(path) if path else None,
        )
        db.add(row)
        db.commit()
        return str(row.id)
    finally:
        db.close()


def _backfill(factory, *, dry_run: bool, batch_size: int = 500) -> dict:
    from AINDY.memory.memory_path_backfill import backfill_memory_paths

    db = factory()
    try:
        return backfill_memory_paths(db, dry_run=dry_run, batch_size=batch_size)
    finally:
        db.close()


def test_a_dry_run_counts_the_drifted_paths_and_changes_nothing(factory):
    drifted = [_seed(factory, f"/memory/{USER}/general/insight/{uuid.uuid4()}") for _ in range(3)]
    before = _rows(factory)

    report = _backfill(factory, dry_run=True)

    assert report["would_fix_leaf"] == 3
    assert report["fixed_leaf"] == 0
    assert _rows(factory) == before
    assert all(_leaf(before[i]["path"]) != i for i in drifted), "liveness: they really had drifted"


def test_the_backfill_ends_each_path_with_its_id_in_batches_and_keeps_the_ids(factory):
    drifted = [_seed(factory, f"/memory/{USER}/syscall/outcome/{uuid.uuid4()}") for _ in range(5)]
    correct = _write({"content": "already right"})["data"]["node"]

    report = _backfill(factory, dry_run=False, batch_size=2)

    rows = _rows(factory)
    assert report["would_fix_leaf"] == 5 and report["fixed_leaf"] == 5
    assert set(drifted) | {correct["id"]} == set(rows), "no id changed, no row lost"
    for node_id in drifted:
        assert rows[node_id]["path"] == f"/memory/{USER}/syscall/outcome/{node_id}"
        assert rows[node_id]["parent_path"] == f"/memory/{USER}/syscall/outcome"
    assert rows[correct["id"]]["path"] == correct["path"], "a correct path is not touched"


def test_running_it_again_finds_nothing(factory):
    _seed(factory, f"/memory/{USER}/general/insight/{uuid.uuid4()}")
    _backfill(factory, dry_run=False)

    again = _backfill(factory, dry_run=True)

    assert again["would_fix_leaf"] == 0 and again["would_fill"] == 0


def test_a_stored_path_that_is_no_address_is_reported_and_left_alone(factory):
    odd = _seed(factory, f"/memory/{USER}")
    _seed(factory, f"/memory/{USER}/general/insight/{uuid.uuid4()}")

    report = _backfill(factory, dry_run=False)

    assert report["leaf_unrepairable"] == 1
    assert report["fixed_leaf"] == 1
    assert _rows(factory)[odd]["path"] == f"/memory/{USER}"


def test_the_command_reports_the_rewrite_and_dry_runs_by_default(factory, capsys):
    from AINDY import runtime_only

    node_id = _seed(factory, f"/memory/{USER}/general/insight/{uuid.uuid4()}")
    before = _rows(factory)[node_id]["path"]

    with pytest.raises(SystemExit) as done:
        runtime_only._backfill_memory_paths(yes=False, batch_size=500)
    assert done.value.code == 0
    assert "1 path(s) would be rewritten to end with their node's id" in capsys.readouterr().out
    assert _rows(factory)[node_id]["path"] == before

    with pytest.raises(SystemExit) as done:
        runtime_only._backfill_memory_paths(yes=True, batch_size=500)
    assert done.value.code == 0
    assert "rewrote 1 path(s)" in capsys.readouterr().out
    assert _leaf(_rows(factory)[node_id]["path"]) == node_id
