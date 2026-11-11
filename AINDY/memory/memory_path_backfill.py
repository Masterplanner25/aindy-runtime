"""MEM-PATH-NULL-1 — store a path on memory nodes saved without one (operator-run, DEC-119).

Every reader already showed such a node at ``derive_legacy_path``, but the path tools
(``walk_path``, ``memory.tree``, a wildcard ``memory.list``) filter on the stored column and
could not find it. ``MemoryNodeDAO.save`` now stores that path when given none. This fills the
rows written before that, with exactly the same path, so nothing a caller has seen changes.

A row with no owner is left alone: there is no tenant to put in its path.

FR-67 (DEC-131) — the same run repairs rows whose stored path does not end with their id (the
write resolver minted one id for the leaf and the DAO stored the row under another). The PATH is
rewritten to end with the row's id; the id never changes, since links, embeddings and callers'
stored ids all name it. The old address stops resolving: that is the cost, and why it is an
operator command with a dry run.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

BACKFILL_BATCH_SIZE = 500


def backfill_memory_paths(
    db: Session, *, dry_run: bool = True, batch_size: int = BACKFILL_BATCH_SIZE
) -> dict[str, Any]:
    """Fill ``path``, ``namespace``, ``addr_type`` and ``parent_path`` on owned rows with no path.

    Walks the rows in id order and commits after each batch, so a large table never holds one
    long transaction. The walk moves past every row it reads, so a row that fails to take its
    path can't make it loop; it is counted in ``not_filled`` instead. ``dry_run`` counts and
    changes nothing. Running it again finds nothing to do.
    """
    from AINDY.memory.memory_address_space import stored_path_fields
    from AINDY.memory.memory_persistence import MemoryNodeModel

    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    leaf = _repair_leaves(db, dry_run=dry_run, batch_size=batch_size)

    needs_path = MemoryNodeModel.path.is_(None)
    owned = MemoryNodeModel.user_id.isnot(None)
    report: dict[str, Any] = {
        "would_fill": db.query(MemoryNodeModel.id).filter(needs_path, owned).count(),
        "skipped_no_owner": db.query(MemoryNodeModel.id)
        .filter(needs_path, MemoryNodeModel.user_id.is_(None))
        .count(),
        "filled": 0,
        "not_filled": 0,
        "batches": 0,
        "dry_run": dry_run,
        **leaf,
    }
    if dry_run:
        db.rollback()
        return report

    last_id = None
    while True:
        query = db.query(MemoryNodeModel).filter(needs_path, owned)
        if last_id is not None:
            query = query.filter(MemoryNodeModel.id > last_id)
        rows = query.order_by(MemoryNodeModel.id).limit(batch_size).all()
        if not rows:
            break
        last_id = rows[-1].id
        for row in rows:
            fields = stored_path_fields(
                user_id=str(row.user_id),
                memory_type=row.memory_type or row.node_type,
                node_id=str(row.id),
            )
            for name, value in fields.items():
                setattr(row, name, value)
        db.commit()
        report["batches"] += 1

    remaining = db.query(MemoryNodeModel.id).filter(needs_path, owned).count()
    report["filled"] = report["would_fill"] - remaining
    report["not_filled"] = remaining
    # would_fill stays what the run found to do (FR-62: zeroing it read as "nothing was found")
    return report


def _repaired_path(path: str, node_id: str) -> str | None:
    """The path ``path`` should have been: the same tenant, namespace and type, ending in the id.

    ``None`` when it already ends with the id. Raises ``ValueError`` when the stored path has no
    tenant, namespace and type to keep, so there is nothing to repair it from.
    """
    from AINDY.memory.memory_address_space import build_path, parse_path

    parts = parse_path(path)
    if parts["node_id"] == node_id:
        return None
    if not (parts["tenant_id"] and parts["namespace"] and parts["addr_type"]):
        raise ValueError(f"no tenant/namespace/type to keep in {path!r}")
    if "*" in (parts["namespace"] + parts["addr_type"]):
        raise ValueError(f"a wildcard is not an address: {path!r}")
    return build_path(parts["tenant_id"], parts["namespace"], parts["addr_type"], node_id)


def _repair_leaves(db: Session, *, dry_run: bool, batch_size: int) -> dict[str, int]:
    """Rewrite each stored path whose leaf is not its row's id (FR-67). Batched, committed per batch.

    The mismatch is found in Python, not SQL: comparing a UUID column to a string suffix is not
    portable across the dialects the tests and deployments use, and this runs once.
    """
    from AINDY.memory.memory_address_space import parent_path_of
    from AINDY.memory.memory_persistence import MemoryNodeModel

    counts = {"would_fix_leaf": 0, "fixed_leaf": 0, "leaf_unrepairable": 0}
    last_id = None
    while True:
        query = db.query(MemoryNodeModel).filter(MemoryNodeModel.path.isnot(None))
        if last_id is not None:
            query = query.filter(MemoryNodeModel.id > last_id)
        rows = query.order_by(MemoryNodeModel.id).limit(batch_size).all()
        if not rows:
            break
        last_id = rows[-1].id
        changed = False
        for row in rows:
            try:
                new_path = _repaired_path(row.path, str(row.id))
            except ValueError:
                counts["leaf_unrepairable"] += 1
                continue
            if new_path is None:
                continue
            counts["would_fix_leaf"] += 1
            if not dry_run:
                row.path = new_path
                row.parent_path = parent_path_of(new_path)
                counts["fixed_leaf"] += 1
                changed = True
        if changed:
            db.commit()
        else:
            db.rollback()
    return counts
