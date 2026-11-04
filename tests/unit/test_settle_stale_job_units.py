"""DEC-107 — `jobs settle-stale-units` closes the units the stale cleanup stranded before DEC-105.

The command commits in batches, so this runs on the private engine: on the shared fixture a
flush reads like a commit (variant 15).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from AINDY.db.models.execution_unit import ExecutionUnit
from AINDY.db.models.job_log import JobLog
from AINDY.platform_layer.async_job_service import STALE_JOB_ERROR, settle_stale_job_units
from AINDY.platform_layer.scheduler_service import _cleanup_stale_logs
from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "stale_units.db")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", session_factory)
    yield session_factory
    engine.dispose()


def _job(
    factory,
    *,
    status: str = "failed",
    error: str | None = STALE_JOB_ERROR,
    unit_status: str = "pending",
    source_type: str = "job_log",
) -> str:
    log_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            JobLog(
                id=log_id,
                source="test",
                task_name="memory.generate_embedding",
                payload={},
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
                status=unit_status,
                source_type=source_type,
                source_id=log_id,
                correlation_id=log_id,
            )
        )
        db.commit()
    return log_id


def _unit(factory, log_id: str) -> ExecutionUnit:
    with factory() as db:
        return db.query(ExecutionUnit).filter(ExecutionUnit.source_id == log_id).one()


def _row(factory, log_id: str) -> JobLog:
    with factory() as db:
        return db.query(JobLog).filter(JobLog.id == log_id).one()


def _settle(factory, **kwargs) -> dict:
    with factory() as db:
        return settle_stale_job_units(db, **kwargs)


def test_a_stranded_unit_fails_durably_and_its_row_is_unchanged(factory):
    log_id = _job(factory)

    report = _settle(factory)

    assert report == {"examined": 1, "settled": 1, "not_moved": 0, "dry_run": False}
    unit = _unit(factory, log_id)
    assert unit.status == "failed"
    assert unit.completed_at is not None
    row = _row(factory, log_id)
    assert (row.status, row.error_message) == ("failed", STALE_JOB_ERROR)


def test_a_dry_run_reports_and_changes_nothing(factory, monkeypatch):
    from AINDY.core.execution_unit_service import ExecutionUnitService

    log_id = _job(factory)
    # A flushed move would roll back on close and read as nothing, so refuse the call itself.
    monkeypatch.setattr(
        ExecutionUnitService,
        "update_status",
        lambda self, eu_id, status: pytest.fail("a dry run must not move a unit"),
    )

    report = _settle(factory, dry_run=True)

    assert (report["examined"], report["settled"], report["dry_run"]) == (1, 1, True)
    assert _unit(factory, log_id).status == "pending"


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"status": "pending", "error": None}, id="row-still-pending"),
        pytest.param({"status": "pending"}, id="row-pending-with-the-stale-message"),
        pytest.param({"status": "success", "error": None}, id="row-succeeded"),
        pytest.param({"error": "boom"}, id="row-failed-another-way"),
        pytest.param({"error": None}, id="row-failed-without-a-message"),
        pytest.param({"unit_status": "executing"}, id="unit-executing"),
        pytest.param({"unit_status": "completed"}, id="unit-completed"),
        pytest.param({"source_type": "flow_run"}, id="unit-of-another-source"),
    ],
)
def test_anything_else_is_left_alone(factory, kwargs):
    """Each runs beside a stranded unit: the liveness control that the pass did run."""
    other = _job(factory, **kwargs)
    before = _unit(factory, other).status
    stranded = _job(factory)

    report = _settle(factory)

    assert report["settled"] == 1
    assert _unit(factory, stranded).status == "failed"
    assert _unit(factory, other).status == before


def test_every_page_is_reached(factory):
    log_ids = [_job(factory) for _ in range(5)]

    report = _settle(factory, batch_size=2)

    assert report["settled"] == 5
    assert {_unit(factory, log_id).status for log_id in log_ids} == {"failed"}


def test_a_unit_that_does_not_move_is_counted_and_does_not_loop(factory, monkeypatch):
    from AINDY.core.execution_unit_service import ExecutionUnitService

    monkeypatch.setattr(ExecutionUnitService, "update_status", lambda self, eu_id, status: False)
    log_ids = [_job(factory) for _ in range(3)]

    report = _settle(factory, batch_size=2)

    assert (report["examined"], report["settled"], report["not_moved"]) == (3, 0, 3)
    assert {_unit(factory, log_id).status for log_id in log_ids} == {"pending"}


def test_the_predicate_matches_what_the_cleanup_writes(factory):
    """The two share one constant. A row failed by the real cleanup, unit reset to its old state."""
    log_id = str(uuid.uuid4())
    with factory() as db:
        db.add(
            JobLog(
                id=log_id,
                source="test",
                task_name="some.job",
                payload={},
                status="pending",
                max_attempts=1,
                attempt_count=0,
                trace_id=log_id,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.commit()
    _cleanup_stale_logs()
    assert _row(factory, log_id).error_message == STALE_JOB_ERROR
    with factory() as db:
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

    assert _settle(factory)["settled"] == 1
    assert _unit(factory, log_id).status == "failed"


def test_batch_size_below_one_is_refused(factory):
    with pytest.raises(ValueError):
        _settle(factory, batch_size=0)


@pytest.mark.parametrize(
    ("argv", "expected_status", "expected_out"),
    [
        pytest.param([], "pending", "dry-run: 1 unit(s) would become failed", id="dry-run"),
        pytest.param(["--yes"], "failed", "ok: settled 1 unit(s) as failed", id="yes"),
    ],
)
def test_the_cli_entry_point(factory, monkeypatch, capsys, argv, expected_status, expected_out):
    """Through the real parser: a command nothing routes to would pass every test above."""
    from AINDY import runtime_only
    from AINDY.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///unused", raising=False)
    monkeypatch.setattr("sys.argv", ["aindy-runtime", "jobs", "settle-stale-units", *argv])
    log_id = _job(factory)

    with pytest.raises(SystemExit) as exc:
        runtime_only.main()

    assert exc.value.code == 0
    assert expected_out in capsys.readouterr().out
    assert _unit(factory, log_id).status == expected_status
