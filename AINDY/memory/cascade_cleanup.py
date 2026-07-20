"""
One-time cleanup for RT-MEMTXN-LEAK-1 cascade debris.

Before the fix, every memory node enqueued an embedding job, and that job's own
lifecycle events (`execution.started`, `feedback.abandonment_detected`, …) were
captured *as memory* — producing another node, another job, and so on. Deployments
that ran an affected version accumulated a large body of memory nodes that record
nothing but the runtime talking to itself. On one real stack: 1,912 of 3,000 nodes.

They are inert now that the cycle is cut, but they are not harmless — they pad every
recall candidate set and leave a standing embedding backlog for the sweep to grind
through on each boot.

**Scoping.** The predicate is not a content-string heuristic. Captured system events
store their originating event under ``extra.event_payload``, so the debris is exactly
the set whose ``extra.event_payload.task_name`` names a runtime-internal memory job —
i.e. precisely the set ``capture_system_event_as_memory`` now refuses to create. No
user- or app-authored memory can match: those task names are registered by the runtime
itself (``AINDY/memory/embedding_jobs.py``) and never appear in an application capture.

Deletion is a hard delete. Child rows (history / traces / edges / links) are removed by
``ON DELETE CASCADE``, matching the ``sys.v1.memory.delete`` contract (MEM-DELETE-1).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from AINDY.core.memory_capture_guard import RUNTIME_INTERNAL_TASK_NAMES
from AINDY.db.database import SessionLocal

logger = logging.getLogger(__name__)

__all__ = ["prune_cascade_debris", "summarize_cascade_debris"]

DEFAULT_BATCH_SIZE = 500


def _task_name_sql(db: Session) -> str:
    """Dialect-appropriate accessor for ``extra.event_payload.task_name``."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return "extra -> 'event_payload' ->> 'task_name'"
    if dialect == "sqlite":
        return "json_extract(extra, '$.event_payload.task_name')"
    raise RuntimeError(
        f"cascade-debris cleanup does not support the '{dialect}' dialect "
        "(needs a JSON path accessor; PostgreSQL and SQLite are supported)"
    )


def _event_type_sql(db: Session) -> str:
    """Dialect-appropriate accessor for ``extra.event_type`` (reporting only)."""
    if db.get_bind().dialect.name == "postgresql":
        return "extra ->> 'event_type'"
    return "json_extract(extra, '$.event_type')"


def _task_name_params(task_names: Iterable[str]) -> tuple[str, dict[str, Any]]:
    """Build an IN-list with individually-bound params (portable, no array types)."""
    names = sorted(task_names)
    if not names:
        raise ValueError("task_names must not be empty — refusing an unscoped delete")
    keys = [f"task_{i}" for i in range(len(names))]
    clause = ", ".join(f":{k}" for k in keys)
    return clause, dict(zip(keys, names))


def summarize_cascade_debris(
    db: Session,
    *,
    task_names: Iterable[str] = RUNTIME_INTERNAL_TASK_NAMES,
) -> dict[str, Any]:
    """Report what a prune would remove, without touching anything."""
    accessor = _task_name_sql(db)
    event_type = _event_type_sql(db)
    in_clause, params = _task_name_params(task_names)

    rows = db.execute(
        text(
            f"SELECT {accessor} AS task_name, "
            f"       {event_type} AS event_type, "
            "       (user_id IS NULL) AS is_global, "
            "       count(*) AS n "
            "  FROM memory_nodes "
            f" WHERE {accessor} IN ({in_clause}) "
            " GROUP BY 1, 2, 3 "
            " ORDER BY 4 DESC"
        ),
        params,
    ).fetchall()

    breakdown = [
        {
            "task_name": r[0],
            "event_type": r[1],
            "global": bool(r[2]),
            "count": int(r[3]),
        }
        for r in rows
    ]
    total = sum(item["count"] for item in breakdown)
    owned = sum(item["count"] for item in breakdown if not item["global"])
    return {
        "matched": total,
        "global": total - owned,
        "owned": owned,
        "task_names": sorted(task_names),
        "breakdown": breakdown,
    }


def prune_cascade_debris(
    *,
    dry_run: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
    task_names: Iterable[str] = RUNTIME_INTERNAL_TASK_NAMES,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    """
    Delete memory nodes created by the RT-MEMTXN-LEAK-1 capture cascade.

    Deletes in committed batches so a large backlog never becomes one long-running
    transaction holding a pooled connection — the failure mode this whole item exists
    to prevent. ``dry_run=True`` (the default) reports and changes nothing.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    owns_session = db is None
    session = db or SessionLocal()
    try:
        report = summarize_cascade_debris(session, task_names=task_names)
        report["dry_run"] = dry_run
        report["deleted"] = 0
        report["batches"] = 0

        if dry_run or not report["matched"]:
            return report

        accessor = _task_name_sql(session)
        in_clause, params = _task_name_params(task_names)
        deleted = 0
        batches = 0

        while True:
            result = session.execute(
                text(
                    "DELETE FROM memory_nodes WHERE id IN ("
                    "  SELECT id FROM memory_nodes "
                    f"  WHERE {accessor} IN ({in_clause}) "
                    "  LIMIT :batch_size"
                    ")"
                ),
                {**params, "batch_size": batch_size},
            )
            session.commit()  # release the connection between batches
            removed = int(result.rowcount or 0)
            if removed <= 0:
                break
            deleted += removed
            batches += 1
            logger.info(
                "[CascadeCleanup] removed %s nodes (batch %s, running total %s)",
                removed,
                batches,
                deleted,
            )

        report["deleted"] = deleted
        report["batches"] = batches
        return report
    finally:
        if owns_session:
            session.close()
