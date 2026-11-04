"""DEC-105 — the stale-log cleanup closes the execution unit and tells the app.

``_cleanup_stale_logs`` failed a JobLog row that sat ``pending`` for an hour, and did nothing
else: the row's execution unit stayed ``pending`` for good, and ``job_log.written`` never fired,
so the app's mirror of ``job_logs`` kept saying ``pending``. That drift is what sent FR-56 the
wrong way: 2,387 rows read as holding admission slots had already been failed here.

The cleanup COMMITS and a handler reads from its OWN session, so this runs on the private
engine: on the shared fixture a flush reads like a commit (variant 15).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from AINDY.db.models.execution_unit import ExecutionUnit
from AINDY.db.models.job_log import JobLog
from AINDY.memory import embedding_jobs
from AINDY.memory.memory_persistence import MemoryNodeModel
from AINDY.platform_layer import registry
from AINDY.platform_layer.scheduler_service import _cleanup_stale_logs
from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

STALE_MESSAGE = "Stale: never started within 1 hour of creation"


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "stale_cleanup.db")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", session_factory)
    yield session_factory
    engine.dispose()


def _job(
    factory,
    *,
    age_hours: float = 2,
    status: str = "pending",
    task_name: str = "some.job",
    unit_status: str | None = "pending",
    payload: dict | None = None,
) -> str:
    log_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            JobLog(
                id=log_id,
                source="test",
                task_name=task_name,
                payload=payload or {},
                status=status,
                max_attempts=1,
                attempt_count=0,
                trace_id=log_id,
                created_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
            )
        )
        if unit_status is not None:
            db.add(
                ExecutionUnit(
                    id=uuid.uuid4(),
                    type="job",
                    status=unit_status,
                    source_type="job_log",
                    source_id=log_id,
                    correlation_id=log_id,
                )
            )
        db.commit()
    return log_id


def _row(factory, log_id: str) -> JobLog:
    with factory() as db:
        return db.query(JobLog).filter(JobLog.id == log_id).one()


def _unit(factory, log_id: str) -> ExecutionUnit:
    with factory() as db:
        return db.query(ExecutionUnit).filter(ExecutionUnit.source_id == log_id).one()


@pytest.fixture
def written(factory, monkeypatch):
    """A real `job_log.written` handler that reads the row from its own session, as the app's does."""
    seen: dict[str, str] = {}

    def handler(context: dict) -> None:
        log_id = context["job_log_id"]
        seen[log_id] = _row(factory, log_id).status

    monkeypatch.setitem(registry._event_handlers, "job_log.written", [handler])
    return seen


def test_a_stale_row_fails_and_its_unit_fails_durably(factory):
    log_id = _job(factory)

    _cleanup_stale_logs()

    row = _row(factory, log_id)
    assert (row.status, row.error_message) == ("failed", STALE_MESSAGE)
    unit = _unit(factory, log_id)
    assert unit.status == "failed"
    assert unit.completed_at is not None


def test_a_row_younger_than_the_cutoff_is_left_alone(factory):
    """The liveness control beside it: the same run does fail the older row."""
    young = _job(factory, age_hours=0.5)
    old = _job(factory)

    _cleanup_stale_logs()

    assert _row(factory, young).status == "pending"
    assert _unit(factory, young).status == "pending"
    assert _unit(factory, old).status == "failed"


def test_a_row_not_pending_is_left_alone(factory):
    running = _job(factory, status="running", unit_status="executing")
    old = _job(factory)

    _cleanup_stale_logs()

    assert _row(factory, running).status == "running"
    assert _unit(factory, running).status == "executing"
    assert _unit(factory, old).status == "failed"


def test_a_unit_already_terminal_keeps_its_status(factory):
    log_id = _job(factory, unit_status="completed")

    _cleanup_stale_logs()

    assert _row(factory, log_id).status == "failed"
    assert _unit(factory, log_id).status == "completed"


def test_a_row_with_no_unit_still_fails(factory):
    log_id = _job(factory, unit_status=None)

    _cleanup_stale_logs()

    assert _row(factory, log_id).status == "failed"


def test_the_app_is_told_after_the_commit(factory, written):
    """The handler sees `failed` from its own session, which only a committed row can show."""
    stale = _job(factory)
    young = _job(factory, age_hours=0.5)

    _cleanup_stale_logs()

    assert written == {stale: "failed"}
    assert young not in written


def test_a_raising_handler_does_not_undo_the_cleanup(factory, monkeypatch):
    def handler(_context: dict) -> None:
        raise RuntimeError("the app's mirror is down")

    monkeypatch.setitem(registry._event_handlers, "job_log.written", [handler])
    first = _job(factory)
    second = _job(factory)

    _cleanup_stale_logs()

    assert _row(factory, first).status == "failed"
    assert _row(factory, second).status == "failed"
    assert _unit(factory, second).status == "failed"


def test_the_settle_command_leaves_a_failed_row_failed(factory):
    """DEC-105: `never started` is true of the job, so the row is not rewritten to `success`."""
    memory_id = uuid.uuid4()
    with factory() as db:
        db.add(
            MemoryNodeModel(
                id=memory_id,
                content="remember this",
                node_type="insight",
                embedding_pending=False,
                embedding_status="complete",
            )
        )
        db.commit()
    log_id = _job(
        factory,
        task_name=embedding_jobs.EMBEDDING_JOB_NAME,
        payload={"memory_id": str(memory_id)},
    )
    _cleanup_stale_logs()

    with factory() as db:
        report = embedding_jobs.settle_superseded_embedding_logs(db)

    assert report["settled"] == 0
    row = _row(factory, log_id)
    assert (row.status, row.error_message) == ("failed", STALE_MESSAGE)
