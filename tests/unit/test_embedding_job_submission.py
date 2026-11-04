"""FR-56 — embedding jobs submitted while the scheduler ran were recorded and never run.

``enqueue_embedding`` was the only caller passing ``execute_inline_in_test_mode=False``, and
``_submit_async_job_inner`` answered that flag on the queued path with ``return log_id`` —
after the JobLog row was committed ``pending``, before the semaphore and the executor. The
sweep embedded every node anyway, so nothing looked wrong, but the row never ran and
``_active_job_statuses()`` counts ``pending`` against the admission caps until the stale
cleanup fails it, an hour or two later (corrected 2026-11-03, DEC-105: the 2,387 rows first
reported were the app's mirror; in ``job_logs`` they were already ``failed``).

The fix makes the sweep own embedding on the instance that runs it (no job row at all), lets
a job that IS submitted reach the executor, has the sweep close any open row for a node it
embeds, and gives operators ``memory settle-embedding-logs`` for the rows already stranded.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from AINDY.db.database import Base
from AINDY.db.models.execution_unit import ExecutionUnit
from AINDY.db.models.job_log import JobLog
from AINDY.memory import embedding_jobs
from AINDY.memory.memory_persistence import MemoryNodeModel
from AINDY.platform_layer import async_job_service

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def production_mode(monkeypatch):
    """Turn off every test-mode switch above the decision under test (standing rule)."""
    from AINDY.config import Settings

    monkeypatch.setattr(Settings, "is_testing", property(lambda self: False))
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("TESTING", "false")


def _node(db, *, pending: bool) -> str:
    node = MemoryNodeModel(
        id=uuid.uuid4(),
        content="remember this",
        node_type="insight",
        embedding_pending=pending,
        embedding_status="pending" if pending else "complete",
    )
    db.add(node)
    db.commit()
    return str(node.id)


def _stranded_log(
    db, memory_id, *, status="pending", started=False, attempts=0, job_name=None
) -> str:
    log_id = str(uuid.uuid4())
    db.add(
        JobLog(
            id=log_id,
            source="memory",
            task_name=job_name or embedding_jobs.EMBEDDING_JOB_NAME,
            payload={"memory_id": memory_id, "trace_id": memory_id, "user_id": None},
            status=status,
            max_attempts=1,
            attempt_count=attempts,
            started_at=datetime.now(timezone.utc) if started else None,
            trace_id=log_id,
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


def _log(db, log_id) -> JobLog:
    db.expire_all()
    return db.query(JobLog).filter(JobLog.id == log_id).one()


def _unit(db, log_id) -> ExecutionUnit:
    return db.query(ExecutionUnit).filter(ExecutionUnit.source_id == log_id).one()


# ── enqueue: the sweep owns embedding where it runs ──────────────────────────────────


class TestEnqueue:
    def test_no_job_row_while_the_scheduler_runs(self, monkeypatch, production_mode):
        submitted = []
        monkeypatch.setattr(embedding_jobs, "_is_background_runner_active", lambda: True)
        monkeypatch.setattr(embedding_jobs, "dispatch_job", lambda **kw: submitted.append(kw))

        result = embedding_jobs.enqueue_embedding("m-1", user_id="u1")

        assert result == "m-1"
        assert submitted == [], "a job was submitted that the sweep already owns"

    def test_a_job_is_still_submitted_without_a_scheduler(self, monkeypatch, production_mode):
        """Liveness control: a follower (or a boot before the scheduler) has no sweep."""
        submitted = []

        class _Result:
            meta = {"log_id": "log-1"}

        monkeypatch.setattr(embedding_jobs, "_is_background_runner_active", lambda: False)
        monkeypatch.setattr(
            embedding_jobs, "dispatch_job", lambda **kw: submitted.append(kw) or _Result()
        )

        result = embedding_jobs.enqueue_embedding("m-1", user_id="u1")

        assert result == "log-1"
        assert [kw["task_name"] for kw in submitted] == [embedding_jobs.EMBEDDING_JOB_NAME]


# ── submit: the flag no longer strands a queued job ──────────────────────────────────


class TestQueuedSubmitReachesTheExecutor:
    def test_flag_off_queued_job_is_handed_to_the_executor(self, db, monkeypatch, production_mode):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setenv("AINDY_ENABLE_BACKGROUND_TASKS", "true")
        monkeypatch.setattr(async_job_service, "SessionLocal", lambda: db)
        monkeypatch.setattr(db, "close", lambda: None)
        monkeypatch.setattr(async_job_service, "_is_background_runner_active", lambda: True)
        monkeypatch.setattr(async_job_service, "_distributed_execution_enabled", lambda: False)
        monkeypatch.setattr(async_job_service, "_enforce_job_admission_limits", lambda *a, **kw: None)
        monkeypatch.setattr(async_job_service, "_enforce_distributed_queue_backpressure", lambda **kw: None)
        monkeypatch.setattr(async_job_service, "_emit_async_system_event", lambda **kw: None)
        monkeypatch.setattr(async_job_service, "_session_dialect_name", lambda _db: "postgresql")
        monkeypatch.setattr(
            async_job_service, "_execute_job_inline",
            lambda *a, **kw: pytest.fail("the queued path ran inline"),
        )
        submitted = []

        class _Executor:
            def submit(self, fn, *args):
                submitted.append(fn)

                class _F:
                    def add_done_callback(self, cb):
                        pass

                    def done(self):
                        return False

                return _F()

        monkeypatch.setattr(async_job_service, "_get_executor", lambda: _Executor())
        monkeypatch.setattr(async_job_service, "_track_future", lambda f: None)
        monkeypatch.setitem(async_job_service._JOB_REGISTRY, "fr56_probe", lambda payload, db: {})

        log_id = async_job_service.submit_async_job(
            task_name="fr56_probe",
            payload={},
            user_id=None,
            source="test",
            execute_inline_in_test_mode=False,
        )

        assert len(submitted) == 1, "the job was recorded but never handed to the executor"
        assert _log(db, log_id).status == "pending"  # the executor, not submit, runs it
        async_job_service._get_semaphore().release()  # the fake executor never ran the job


# ── settle: close what the sweep superseded ──────────────────────────────────────────


class TestSettle:
    def test_settles_a_never_started_row_whose_node_is_embedded(self, db):
        log_id = _stranded_log(db, _node(db, pending=False))

        report = embedding_jobs.settle_superseded_embedding_logs(db)

        assert report["settled"] == 1
        row = _log(db, log_id)
        assert row.status == "success"
        assert row.completed_at is not None
        assert row.result["superseded_by"] == embedding_jobs.EMBEDDING_SWEEP_JOB_NAME
        assert _unit(db, log_id).status == "completed"

    def test_leaves_rows_that_do_not_qualify(self, db):
        done = _node(db, pending=False)
        keep = {
            "node pending": _stranded_log(db, _node(db, pending=True)),
            "node gone": _stranded_log(db, str(uuid.uuid4())),
            "started": _stranded_log(db, done, started=True),
            "attempted": _stranded_log(db, done, attempts=1),
            "already failed": _stranded_log(db, done, status="failed"),
            "another job": _stranded_log(db, done, job_name="memory.embedding_sweep"),
        }
        control = _stranded_log(db, done)

        report = embedding_jobs.settle_superseded_embedding_logs(db)

        assert _log(db, control).status == "success"  # liveness: the predicate does match
        for why, log_id in keep.items():
            assert _log(db, log_id).status in {"pending", "failed"}, why
            assert _unit(db, log_id).status == "pending", why
        assert report["node_pending"] == 1
        assert report["node_missing"] == 1

    def test_dry_run_changes_nothing(self, db):
        log_id = _stranded_log(db, _node(db, pending=False))

        report = embedding_jobs.settle_superseded_embedding_logs(db, dry_run=True)

        assert report["settled"] == 1
        assert _log(db, log_id).status == "pending"
        assert _unit(db, log_id).status == "pending"

    def test_pages_past_rows_it_skips(self, db):
        """A batch full of skipped rows must not stop or loop the pass."""
        skipped = [_stranded_log(db, _node(db, pending=True)) for _ in range(3)]
        settled = [_stranded_log(db, _node(db, pending=False)) for _ in range(3)]

        report = embedding_jobs.settle_superseded_embedding_logs(db, batch_size=2)

        assert report["examined"] == 6
        assert report["settled"] == 3
        assert all(_log(db, i).status == "success" for i in settled)
        assert all(_log(db, i).status == "pending" for i in skipped)

    def test_memory_ids_scope_the_pass(self, db):
        in_scope = _node(db, pending=False)
        target = _stranded_log(db, in_scope)
        other = _stranded_log(db, _node(db, pending=False))

        report = embedding_jobs.settle_superseded_embedding_logs(db, memory_ids=[in_scope])

        assert report["settled"] == 1
        assert _log(db, target).status == "success"
        assert _log(db, other).status == "pending"

    def test_empty_scope_touches_nothing(self, db):
        log_id = _stranded_log(db, _node(db, pending=False))

        report = embedding_jobs.settle_superseded_embedding_logs(db, memory_ids=[])

        assert report["examined"] == 0
        assert _log(db, log_id).status == "pending"


class TestSweepClosesTheRow:
    def test_sweep_closes_the_open_row_of_a_node_it_embeds(self, db, monkeypatch):
        node_id = _node(db, pending=True)
        log_id = _stranded_log(db, node_id)
        untouched = _stranded_log(db, _node(db, pending=False))  # not this sweep's node
        monkeypatch.setattr(embedding_jobs, "queue_system_event", lambda **kw: None)
        monkeypatch.setattr(embedding_jobs, "generate_embedding", lambda text: [0.1] * 1536)

        result = embedding_jobs.process_pending_embeddings(db=db)

        assert result["completed"] == 1
        assert result["logs_closed"] == 1
        assert _log(db, log_id).status == "success"
        assert _log(db, untouched).status == "pending", "the sweep settled beyond its own nodes"

    def test_a_deferred_node_keeps_its_row_open(self, db, monkeypatch):
        log_id = _stranded_log(db, _node(db, pending=True))
        monkeypatch.setattr(embedding_jobs, "queue_system_event", lambda **kw: None)
        monkeypatch.setattr(embedding_jobs, "generate_embedding", lambda text: [0.0] * 1536)

        result = embedding_jobs.process_pending_embeddings(db=db)

        assert result["deferred"] == 1
        assert result["logs_closed"] == 0
        assert _log(db, log_id).status == "pending"
