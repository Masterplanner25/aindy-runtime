"""The two job-row settles, on real Postgres.

`settle_superseded_embedding_logs` filters on `JobLog.payload["memory_id"].as_string()`, and its
unit tests run on SQLite only. On the sweep a failure there is a logged warning and nothing else,
and on the app's stack no row has existed to match (`RUNTIME_2_29_2_UPGRADE.md`), so this is the
only place the filter meets a real row. The join in `settle_stale_job_units` runs here too.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from AINDY.db.models.execution_unit import ExecutionUnit
from AINDY.db.models.job_log import JobLog
from AINDY.memory import embedding_jobs
from AINDY.memory.memory_persistence import MemoryNodeModel
from AINDY.platform_layer.async_job_service import STALE_JOB_ERROR, settle_stale_job_units

pytestmark = [pytest.mark.integration]


def _on_postgres(db) -> None:
    assert db.get_bind().dialect.name == "postgresql", "this suite exists to exercise Postgres"


def _node(db) -> str:
    node = MemoryNodeModel(
        id=uuid.uuid4(),
        content="remember this",
        node_type="insight",
        embedding_pending=False,
        embedding_status="complete",
    )
    db.add(node)
    db.commit()
    return str(node.id)


def _job(db, *, memory_id: str, status: str = "pending", error: str | None = None) -> str:
    log_id = str(uuid.uuid4())
    db.add(
        JobLog(
            id=log_id,
            source="memory",
            task_name=embedding_jobs.EMBEDDING_JOB_NAME,
            payload={"memory_id": memory_id, "trace_id": memory_id, "user_id": None},
            status=status,
            error_message=error,
            max_attempts=1,
            attempt_count=0,
            trace_id=log_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        ExecutionUnit(
            id=uuid.uuid4(),
            type="job",
            status="pending",
            source_type="job_log",
            source_id=log_id,
            correlation_id=log_id,
        )
    )
    db.commit()
    return log_id


def _unit_status(db, log_id: str) -> str:
    db.expire_all()
    return db.query(ExecutionUnit).filter(ExecutionUnit.source_id == log_id).one().status


def _row_status(db, log_id: str) -> str:
    db.expire_all()
    return db.query(JobLog).filter(JobLog.id == log_id).one().status


def test_the_scoped_settle_matches_a_real_row_through_the_json_filter(db_session):
    _on_postgres(db_session)
    in_scope_node, other_node = _node(db_session), _node(db_session)
    in_scope = _job(db_session, memory_id=in_scope_node)
    other = _job(db_session, memory_id=other_node)

    report = embedding_jobs.settle_superseded_embedding_logs(db_session, memory_ids=[in_scope_node])

    assert report["settled"] == 1
    assert _row_status(db_session, in_scope) == "success"
    assert _unit_status(db_session, in_scope) == "completed"
    assert _row_status(db_session, other) == "pending", "the filter must scope, not match everything"


def test_the_stale_unit_settle_joins_and_moves_a_real_unit(db_session):
    _on_postgres(db_session)
    node = _node(db_session)
    stranded = _job(db_session, memory_id=node, status="failed", error=STALE_JOB_ERROR)
    other = _job(db_session, memory_id=node, status="failed", error="boom")

    report = settle_stale_job_units(db_session, batch_size=1)

    assert report["settled"] == 1
    assert _unit_status(db_session, stranded) == "failed"
    assert _unit_status(db_session, other) == "pending"
    assert _row_status(db_session, stranded) == "failed"
