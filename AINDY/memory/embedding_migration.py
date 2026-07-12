"""
Re-embed migration — ECOGAP-3 Phase 1 (Increment 2).

Switching the embedding provider or its vector dimension on an *existing* deployment
is a data migration, not a config flip: pgvector stores fixed-width vectors, so vectors
produced by the old model are incompatible with a differently-dimensioned column, and
``create_all`` never alters an existing column. This module performs the operator-run
migration:

  1. Validate the active provider against the configured column dimension (fail-closed).
  2. (PostgreSQL) NULL out existing embeddings, ALTER the column to the target
     ``vector(N)``, and mark every row pending.
  3. Optionally drain the pending set now, re-embedding each node with the new provider
     (reuses the existing per-node job); otherwise the background sweep picks them up.

PostgreSQL only (the pgvector column ALTER has no SQLite equivalent). Intended to run
with traffic stopped. Invoked via ``aindy-runtime memory reembed``.

IMPORTANT: run in a process started with the *target* ``AINDY_EMBEDDING_DIMENSIONS``
already set in the environment. pgvector's ORM ``Vector(N)`` column type is fixed at
import time and its bind processor enforces the width, so re-embedded vectors are only
writable when the model was imported at the target dimension. The CLI satisfies this
automatically (the operator sets the env var, then runs the command in a fresh process);
an in-process caller that mutates the setting after import will fail on write.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from AINDY.memory.embedding_providers import resolve_embedding_column_dimensions

logger = logging.getLogger(__name__)


def _is_postgres(engine: Engine) -> bool:
    return not str(engine.url).startswith("sqlite")


def reembed_all_memory_nodes(
    *,
    engine: Engine | None = None,
    alter_column: bool = True,
    drain: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Re-embed every ``memory_nodes`` row with the currently-configured provider.

    Args:
        engine: SQLAlchemy engine; defaults to the runtime engine.
        alter_column: ALTER the pgvector column to the target dimension (PG only).
        drain: re-embed pending rows inline now; if False, only alter + mark pending
            and leave the background embedding sweep to regenerate vectors.
        dry_run: report the plan (row count, target dimension) without mutating anything.

    Returns a summary dict. Raises EmbeddingProviderError if the active provider's
    dimension does not match the configured column dimension.
    """
    # Import inside the function — keeps the CLI import chain off AINDY.db at module load.
    from AINDY.db.database import SessionLocal
    from AINDY.db.database import engine as default_engine
    from AINDY.memory import embedding_service
    from AINDY.memory.embedding_jobs import process_embedding_job
    from AINDY.memory.memory_persistence import MemoryNodeModel

    engine = engine or default_engine

    # Rebuild the provider from current settings and fail closed if provider dim and
    # configured column dim disagree — before any destructive DDL.
    embedding_service.reset_embedding_provider()
    provider = embedding_service.get_embedding_provider()
    target_dim = resolve_embedding_column_dimensions()

    if not _is_postgres(engine):
        raise RuntimeError(
            "reembed requires PostgreSQL (the pgvector column ALTER has no SQLite equivalent)."
        )

    session = SessionLocal()
    try:
        total = session.query(MemoryNodeModel.id).count()
    finally:
        session.close()

    plan = {
        "provider": provider.name,
        "target_dimension": target_dim,
        "total_rows": total,
        "alter_column": alter_column,
        "drain": drain,
    }
    if dry_run:
        return {"status": "dry_run", **plan}

    logger.warning(
        "[reembed] starting — provider=%s target_dim=%s rows=%s alter_column=%s drain=%s",
        provider.name, target_dim, total, alter_column, drain,
    )

    altered = False
    if alter_column:
        # One transaction: clear vectors (so the type change can't fail on width),
        # ALTER to the target dimension, then mark every row pending. target_dim is a
        # validated int — safe to inline (type is DDL, not bindable).
        with engine.begin() as conn:
            conn.execute(text("UPDATE memory_nodes SET embedding = NULL"))
            conn.execute(
                text(f"ALTER TABLE memory_nodes ALTER COLUMN embedding TYPE vector({int(target_dim)})")
            )
            conn.execute(
                text(
                    "UPDATE memory_nodes SET embedding_pending = true, "
                    "embedding_status = 'pending'"
                )
            )
        altered = True
        logger.warning("[reembed] column altered to vector(%s); all rows marked pending.", target_dim)

    completed = 0
    deferred = 0
    if drain:
        # Snapshot the pending IDs up front and process each once — a re-query loop
        # would spin forever on permanently-deferred rows (e.g. empty content).
        session = SessionLocal()
        try:
            pending_ids = [
                str(row_id)
                for (row_id,) in session.query(MemoryNodeModel.id)
                .filter(MemoryNodeModel.embedding_pending.is_(True))
                .all()
            ]
        finally:
            session.close()

        for memory_id in pending_ids:
            session = SessionLocal()
            try:
                result = process_embedding_job(
                    {"memory_id": memory_id, "trace_id": memory_id}, session
                )
            finally:
                session.close()
            if result.get("embedding_pending"):
                deferred += 1
            else:
                completed += 1

        logger.warning(
            "[reembed] drain complete — completed=%s deferred=%s of %s pending.",
            completed, deferred, len(pending_ids),
        )

    return {
        "status": "ok",
        **plan,
        "column_altered": altered,
        "reembedded": completed,
        "deferred": deferred,
    }
