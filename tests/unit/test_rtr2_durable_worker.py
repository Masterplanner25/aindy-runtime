"""RTR-2 — durable worker model (gaps 1+2).

Gap 1: `resolve_execution_mode()` — explicit EXECUTION_MODE always wins; when
unset, production defaults to "distributed" (durable, fail-fast) while dev/test
stay "thread". Gap 2: thread-mode orphaned-job recovery re-dispatches JobLog rows
stranded by a crash, at startup only, and is a no-op in distributed mode.
"""
from __future__ import annotations

import uuid
from unittest.mock import PropertyMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import tests.fixtures.db  # noqa: F401  — registers JSONB/UUID/Vector SQLite compilers
import AINDY.db.model_registry  # noqa: F401  — populate metadata
from AINDY.db.database import Base

pytestmark = pytest.mark.runtime_only


# ── Gap 1: execution-mode resolution ──────────────────────────────────────────

def _set_prod(is_prod: bool):
    from AINDY.config import settings

    return patch.object(type(settings), "is_prod", new_callable=PropertyMock, return_value=is_prod)


def test_explicit_mode_always_wins(monkeypatch):
    from AINDY.config import resolve_execution_mode

    monkeypatch.setenv("EXECUTION_MODE", "Distributed")
    with _set_prod(False):
        assert resolve_execution_mode() == "distributed"  # case-normalized
    monkeypatch.setenv("EXECUTION_MODE", "thread")
    with _set_prod(True):
        assert resolve_execution_mode() == "thread"  # explicit thread honored even in prod


def test_unset_defaults_thread_in_dev_distributed_in_prod(monkeypatch):
    from AINDY.config import resolve_execution_mode

    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    with _set_prod(False):
        assert resolve_execution_mode() == "thread"
    with _set_prod(True):
        assert resolve_execution_mode() == "distributed"


def test_distributed_enabled_helper_tracks_resolver(monkeypatch):
    from AINDY.platform_layer import async_job_service

    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    with _set_prod(True):
        assert async_job_service._distributed_execution_enabled() is True
    with _set_prod(False):
        assert async_job_service._distributed_execution_enabled() is False


# ── Gap 2: thread-mode orphaned-job recovery ──────────────────────────────────

@pytest.fixture
def job_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_off(dbapi_connection, _rec):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.close()

    Base.metadata.tables["job_logs"].create(bind=engine)
    return sessionmaker(bind=engine)


def _mk_job(session, *, status):
    from AINDY.db.models.job_log import JobLog

    log = JobLog(id=str(uuid.uuid4()), source="test", job_name="test.task", payload={}, status=status)
    session.add(log)
    session.commit()
    return str(log.id)


class _SyncThread:
    """Drop-in for threading.Thread that runs the target synchronously."""

    def __init__(self, target=None, daemon=None, **_):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def test_recovery_noop_in_distributed_mode():
    from AINDY.platform_layer import job_recovery

    with patch("AINDY.config.resolve_execution_mode", return_value="distributed"):
        assert job_recovery.recover_orphaned_thread_jobs() == 0


def test_recovery_redispatches_orphans_and_skips_terminal(job_session_factory):
    from AINDY.platform_layer import job_recovery

    seed = job_session_factory()
    pending_id = _mk_job(seed, status="pending")
    running_id = _mk_job(seed, status="running")
    done_id = _mk_job(seed, status="success")
    seed.close()

    dispatched: list[str] = []

    with (
        patch("AINDY.config.resolve_execution_mode", return_value="thread"),
        patch("AINDY.db.database.SessionLocal", job_session_factory),
        patch(
            "AINDY.platform_layer.async_job_service._execute_job",
            side_effect=lambda lid, *a, **k: dispatched.append(lid),
        ),
        patch.object(job_recovery.threading, "Thread", _SyncThread),
    ):
        count = job_recovery.recover_orphaned_thread_jobs()

    assert count == 2
    assert set(dispatched) == {pending_id, running_id}
    assert done_id not in dispatched

    # started_at cleared on the re-claimed orphans; terminal row untouched.
    check = job_session_factory()
    from AINDY.db.models.job_log import JobLog

    done = check.query(JobLog).filter(JobLog.id == done_id).one()
    assert done.status == "success"
    check.close()
