"""MEM-PATH-NULL-1 — store a path on memory nodes saved without one (operator-run, DEC-119).

Every reader already showed such a node at ``derive_legacy_path``, but the path tools
(``walk_path``, ``memory.tree``, a wildcard ``memory.list``) filter on the stored column and
could not find it. ``MemoryNodeDAO.save`` now stores that path when given none. This fills the
rows written before that, with exactly the same path, so nothing a caller has seen changes.

A row with no owner is left alone: there is no tenant to put in its path.
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
    report["would_fill"] = 0
    return report
