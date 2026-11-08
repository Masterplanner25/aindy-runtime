from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from AINDY.config import settings
from AINDY.core.execution_signal_helper import queue_system_event
from AINDY.core.execution_dispatcher import dispatch_job
from AINDY.db.database import SessionLocal
from AINDY.core.system_event_types import SystemEventTypes
from AINDY.memory.embedding_service import generate_embedding
from AINDY.platform_layer.async_job_service import (
    _INLINE_ACTIVE,
    _is_background_runner_active,
    register_async_job,
)

logger = logging.getLogger(__name__)

EMBEDDING_JOB_NAME = "memory.generate_embedding"
EMBEDDING_SWEEP_JOB_NAME = "memory.embedding_sweep"
EMBEDDING_SWEEP_LIMIT = 25
SETTLE_BATCH_SIZE = 500


def enqueue_embedding(
    memory_id: str,
    *,
    user_id: str | None = None,
    trace_id: str | None = None,
    db: Session | None = None,
) -> str:
    inline_mode = _INLINE_ACTIVE.get()
    env_name = os.getenv("ENV", "").lower()
    testing_flag = os.getenv("TESTING", "false").lower() in {"1", "true", "yes"}
    if settings.is_testing or env_name == "test" or testing_flag or inline_mode:
        logger.info(
            "[EmbeddingJobs] Skipping embedding job during inline/test mode (memory=%s inline=%s)",
            memory_id,
            inline_mode,
        )
        return memory_id
    # FR-56 — on the instance holding the scheduler lease, the one-minute sweep
    # (`process_pending_embeddings`) owns embedding: every node is written with
    # `embedding_pending=True`, so the sweep finds it without a job. Submitting one here
    # used to record a `pending` JobLog that nothing ran (`async_job_service` returned before
    # the executor for `execute_inline_in_test_mode=False`). It held an admission slot until the
    # stale cleanup failed it, an hour or two later, for work the sweep did. With no scheduler
    # running here (a follower, or before boot finishes) the sweep is not running in this
    # process, so the job is still submitted; it runs inline, or on the distributed queue, and
    # closes its own row.
    if _is_background_runner_active():
        logger.debug("[EmbeddingJobs] memory=%s left to the embedding sweep", memory_id)
        return memory_id
    result = dispatch_job(
        task_name=EMBEDDING_JOB_NAME,
        payload={
            "memory_id": memory_id,
            "trace_id": trace_id or memory_id,
            "user_id": user_id,
        },
        user_id=user_id,
        source="memory",
        max_attempts=1,
        execute_inline_in_test_mode=False,
        db=db,
    )
    return result.meta["log_id"]


def _set_embedding_status(db, memory_node, status: str, embedding: list[float] | None = None) -> None:
    memory_node.embedding_status = status
    if embedding is not None:
        memory_node.embedding = embedding
    db.add(memory_node)
    db.commit()
    db.refresh(memory_node)


@register_async_job(EMBEDDING_JOB_NAME)
def process_embedding_job(payload: dict[str, Any], db):
    from AINDY.memory.memory_persistence import MemoryNodeModel

    memory_node = (
        db.query(MemoryNodeModel)
        .filter(MemoryNodeModel.id == uuid.UUID(str(payload["memory_id"])))
        .first()
    )
    if not memory_node:
        logger.warning("[EmbeddingJobs] memory node %s not found", payload["memory_id"])
        return {"memory_id": str(payload["memory_id"]), "embedding_pending": True, "status": "missing"}

    trace_id = payload.get("trace_id") or (memory_node.extra or {}).get("trace_id") or str(memory_node.id)
    parent_event_id = str(memory_node.source_event_id) if memory_node.source_event_id else None

    if not getattr(memory_node, "embedding_pending", True):
        return {
            "memory_id": str(memory_node.id),
            "embedding_pending": False,
            "embedding_status": memory_node.embedding_status,
            "status": "already_processed",
        }

    started_event_id = queue_system_event(
        db=db,
        event_type=SystemEventTypes.EMBEDDING_STARTED,
        user_id=memory_node.user_id,
        trace_id=trace_id,
        parent_event_id=parent_event_id,
        source="memory",
        payload={"memory_id": str(memory_node.id)},
        required=True,
    )

    # RT-MEMTXN-LEAK-1 (follow-up) — release the DB connection BEFORE the slow embedding API
    # call. `queue_system_event(required=True)` above commits, which expires `memory_node`, so
    # reading `.content` triggers a refresh `SELECT memory_nodes …` that opens a FRESH
    # transaction — and `generate_embedding()` then runs (seconds) with it open, pinning the
    # connection `idle in transaction` (xact_age == idle_s). One job is enqueued per captured
    # memory, so a single request fans out to dozens of concurrently-held connections and
    # exhausts the pool (login ~45s). Read the content, commit to return the connection to the
    # pool, then embed; the write below re-acquires a connection for its fast execution. This
    # session is the job's own (not request-shared), and the EMBEDDING_STARTED event should be
    # durable regardless, so committing here is correct as well as necessary.
    node_content = memory_node.content
    # FR-61: read the owner with the content, before the commit expires the row; reading it after
    # would re-open a transaction for the length of the embedding call (RT-MEMTXN-LEAK-1).
    node_owner = memory_node.user_id
    try:
        db.commit()
    except Exception as exc:  # never fail the job on pool hygiene
        logger.warning(
            "[EmbeddingJobs] pre-embedding commit failed for %s: %s", payload["memory_id"], exc
        )

    try:
        from AINDY.platform_layer.external_call_service import external_calls_attributed_to

        with external_calls_attributed_to(node_owner):  # FR-61: the model call is the owner's
            embedding = generate_embedding(node_content)
        if not embedding or not any(float(value) != 0.0 for value in embedding):
            raise RuntimeError("Embedding generation returned an empty or zero vector")

        memory_node.embedding = embedding
        memory_node.embedding_pending = False
        memory_node.embedding_status = "complete"
        db.add(memory_node)
        db.commit()
        db.refresh(memory_node)

        queue_system_event(
            db=db,
            event_type=SystemEventTypes.EMBEDDING_COMPLETED,
            user_id=memory_node.user_id,
            trace_id=trace_id,
            parent_event_id=str(started_event_id) if started_event_id else parent_event_id,
            source="memory",
            payload={
                "memory_id": str(memory_node.id),
                "dimensions": len(embedding),
            },
            required=True,
        )
        return {
            "memory_id": str(memory_node.id),
            "embedding_pending": memory_node.embedding_pending,
            "embedding_status": memory_node.embedding_status,
        }
    except Exception as exc:
        logger.warning("[EmbeddingJobs] embedding deferred for %s: %s", memory_node.id, exc)
        memory_node.embedding_pending = True
        memory_node.embedding_status = "pending"
        db.add(memory_node)
        db.commit()
        db.refresh(memory_node)
        return {
            "memory_id": str(memory_node.id),
            "embedding_pending": memory_node.embedding_pending,
            "embedding_status": memory_node.embedding_status,
            "error": str(exc),
        }


def process_pending_embeddings(*, limit: int = EMBEDDING_SWEEP_LIMIT, db: Session | None = None) -> dict[str, Any]:
    from AINDY.memory.memory_persistence import MemoryNodeModel

    owns_session = db is None
    session = db or SessionLocal()
    try:
        pending_rows = (
            session.query(MemoryNodeModel)
            .filter(MemoryNodeModel.embedding_pending.is_(True))
            .order_by(MemoryNodeModel.created_at.asc(), MemoryNodeModel.id.asc())
            .limit(max(1, int(limit)))
            .all()
        )
        processed = 0
        completed = 0
        deferred = 0
        completed_ids: list[str] = []
        for row in pending_rows:
            result = process_embedding_job(
                {
                    "memory_id": str(row.id),
                    "trace_id": (row.extra or {}).get("trace_id") or str(row.id),
                },
                session,
            )
            processed += 1
            if result.get("embedding_pending"):
                deferred += 1
            else:
                completed += 1
                completed_ids.append(str(row.id))
        # FR-56 — a node the sweep just embedded may still have an embedding job row that
        # never ran; close it, or it holds an admission slot forever. Never fatal to the sweep.
        logs_closed = 0
        if completed_ids:
            try:
                logs_closed = settle_superseded_embedding_logs(session, memory_ids=completed_ids)["settled"]
            except Exception as exc:
                session.rollback()
                logger.warning("[EmbeddingJobs] closing superseded embedding logs failed: %s", exc)
        return {
            "job": EMBEDDING_SWEEP_JOB_NAME,
            "processed": processed,
            "completed": completed,
            "deferred": deferred,
            "logs_closed": logs_closed,
        }
    finally:
        if owns_session:
            session.close()


def settle_superseded_embedding_logs(
    db: Session,
    *,
    memory_ids: Iterable[str] | None = None,
    dry_run: bool = False,
    batch_size: int = SETTLE_BATCH_SIZE,
) -> dict[str, Any]:
    """Close embedding JobLog rows that never ran but whose node is embedded (FR-56).

    A row qualifies only when all of these hold: it is a ``memory.generate_embedding`` job,
    still ``pending``, never started (``started_at`` NULL, ``attempt_count`` 0), and its
    payload's node exists with ``embedding_pending`` False. The work it describes was done
    by the sweep, so the row becomes ``success`` with ``result.superseded_by`` naming the
    sweep, and its execution unit is walked ``pending -> executing -> completed``. A row whose
    node is still pending or gone is left alone and counted.

    ``memory_ids`` scopes the pass to those nodes (the sweep's own call); without it every
    qualifying row is examined, in committed batches of ``batch_size``. ``dry_run`` reports
    and changes nothing.
    """
    from AINDY.db.models.job_log import JobLog
    from AINDY.memory.memory_persistence import MemoryNodeModel

    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    report = {"examined": 0, "settled": 0, "node_pending": 0, "node_missing": 0, "dry_run": dry_run}
    scope = None
    if memory_ids is not None:
        scope = sorted({str(m) for m in memory_ids})
        if not scope:
            return report

    query = db.query(JobLog).filter(
        JobLog.job_name == EMBEDDING_JOB_NAME,
        JobLog.status == "pending",
        JobLog.started_at.is_(None),
        JobLog.attempt_count == 0,
    )
    if scope is not None:
        query = query.filter(JobLog.payload["memory_id"].as_string().in_(scope))
    query = query.order_by(JobLog.id.asc())

    # Keyset pages on the primary key: a settled row leaves the predicate, a skipped one
    # stays, so an OFFSET would skip rows and re-reading from the start would loop.
    last_id: str | None = None
    while True:
        page = query if last_id is None else query.filter(JobLog.id > last_id)
        rows = page.limit(batch_size).all()
        if not rows:
            break
        last_id = rows[-1].id

        node_ids: dict[str, uuid.UUID] = {}
        for row in rows:
            try:
                node_ids[row.id] = uuid.UUID(str((row.payload or {}).get("memory_id")))
            except (TypeError, ValueError):
                pass
        pending_by_node: dict[uuid.UUID, bool] = {}
        if node_ids:
            pending_by_node = dict(
                db.query(MemoryNodeModel.id, MemoryNodeModel.embedding_pending)
                .filter(MemoryNodeModel.id.in_(set(node_ids.values())))
                .all()
            )

        now = datetime.now(timezone.utc)
        for row in rows:
            report["examined"] += 1
            node_id = node_ids.get(row.id)
            pending = pending_by_node.get(node_id) if node_id is not None else None
            if pending is None:
                report["node_missing"] += 1
                continue
            if pending:
                report["node_pending"] += 1
                continue
            report["settled"] += 1
            if dry_run:
                continue
            row.status = "success"
            row.completed_at = now
            row.result = {"memory_id": str(node_id), "superseded_by": EMBEDDING_SWEEP_JOB_NAME}
            db.add(row)
            try:
                from AINDY.core.execution_unit_service import ExecutionUnitService

                units = ExecutionUnitService(db)
                unit = units.get_by_source("job_log", row.id)
                if unit is not None and unit.status == "pending":
                    units.update_status(unit.id, "executing")
                    units.update_status(unit.id, "completed")
            except Exception as exc:  # a unit hook is never fatal to the settle
                logger.debug("[EmbeddingJobs] unit for log %s not finalised: %s", row.id, exc)
        if not dry_run:
            db.commit()  # release the connection between batches
        if len(rows) < batch_size:
            break

    return report


def process_pending_embedding(memory_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        return process_embedding_job({"memory_id": memory_id, "trace_id": memory_id}, db)
    finally:
        db.close()


