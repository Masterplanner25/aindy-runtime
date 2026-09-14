"""`ASYNC-JOB-UNREGISTERED-STORM-1` — an unregistered handler fails once, terminally.

Observed live (2026-09-13): a bare runtime booted against a database holding two
`openclaw.reminder` rows from a consumer not loaded in the process re-dispatched each of them
**~87 times per second, forever** — 45,000 log lines in ten minutes. Three faults stacked:

1. `raise RuntimeError("… is not registered")` came BEFORE `log.attempt_count += 1`, so the
   except branch saw `0 < max_attempts (1)`, set `pending`, and re-dispatched. `max_attempts`
   meant nothing for this error.
2. The increment, when it did run, was only ever committed as a side effect of the
   started-event emit; the except branch's `db.rollback()` discarded it otherwise. So a
   registered handler that failed without a started event was uncounted too.
3. The thread-mode retry called `dispatch()` inline — executor submit, no delay. The
   distributed path had a backoff (`_compute_retry_delay` → `enqueue_delayed`); this one did not.

What is pinned: an unregistered handler is a TERMINAL error (one attempt, `failed`, whatever
the budget); an attempt is counted even when the rollback discards its increment; a retryable
failure with budget left is re-dispatched AFTER the dispatcher's backoff, and stops at
`max_attempts`.

★ These tests drive `_execute_job_inline` — the function boot recovery
(`recover_orphaned_thread_jobs`) and the thread pool both run — against a real JobLog row on
SQLite. The retry hand-off is observed at `threading.Timer` (thread mode's backoff carrier) and
at the dispatcher, both spied, never no-op'd blind: a test asserting "not re-dispatched" needs
a liveness control that CAN see a re-dispatch, and `test_a_retryable_failure_backs_off` is it.

Mutation-checked: move the increment back below the handler lookup → the unregistered tests
fail on `attempt_count`; drop `_is_terminal_job_error` → they fail on the re-dispatch; drop the
post-rollback restore → the retryable test fails on the count; call `_fire()` directly instead
of the Timer → the backoff test fails.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.platform_layer import async_job_service as ajs  # noqa: E402


@pytest.fixture
def db_session():
    """A private SQLite engine with the full schema, NOT the shared `db_session` fixture.

    The code under test calls `db.rollback()` in its except branch. The shared fixture binds
    its session to a connection holding an outer transaction, so that rollback undoes the
    test's own inserted row as well — the row "disappears" and every assertion reads
    `NoResultFound`. A real session against a real (in-memory) database is what the worker
    thread has, so it is what this test uses."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import tests.fixtures.db  # noqa: F401  — registers the JSONB/UUID/Vector SQLite compilers
    import AINDY.db.model_registry  # noqa: F401  — populates Base.metadata
    from AINDY.db.database import Base

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
def no_dispatch():
    """Spy on the two places a retry can leave this function: the dispatcher (immediate) and
    the Timer (backed off). Neither is a no-op for the code under test — the dispatcher
    would run the retry on the executor, the Timer would run it later; the tests assert on
    what was handed to them."""
    with patch("AINDY.core.execution_dispatcher.dispatch") as dispatch, \
         patch("threading.Timer") as timer:
        yield dispatch, timer


def _row(db, task_name, *, max_attempts=1):
    from AINDY.db.models.job_log import JobLog

    log_id = str(uuid.uuid4())
    db.add(JobLog(
        id=log_id, source="test", task_name=task_name, payload={}, status="pending",
        max_attempts=max_attempts, user_id=uuid.uuid4(), trace_id=log_id,
    ))
    db.commit()
    return log_id


def _reload(db, log_id):
    from AINDY.db.models.job_log import JobLog

    db.expire_all()
    return db.query(JobLog).filter(JobLog.id == log_id).one()


# ── unregistered → terminal ──────────────────────────────────────────────────


@pytest.mark.parametrize("max_attempts", [1, 5])
def test_an_unregistered_handler_fails_once_and_is_not_re_dispatched(
    db_session, no_dispatch, max_attempts
):
    """★ THE storm, inverted. One attempt, `failed`, whatever the budget — a retry cannot
    register a handler. `max_attempts=1` is the live case (the default); `5` proves the class
    is terminal rather than merely counted."""
    dispatch, timer = no_dispatch
    task = f"ghost.handler.{uuid.uuid4().hex[:6]}"
    assert task not in ajs._JOB_REGISTRY
    log_id = _row(db_session, task, max_attempts=max_attempts)

    ajs._execute_job_inline(db_session, log_id, task, {})

    log = _reload(db_session, log_id)
    assert log.status == "failed", f"status {log.status!r} — the row was left retryable"
    assert log.attempt_count == 1, "the attempt was not counted; `max_attempts` means nothing"
    assert "not registered" in (log.error_message or "")
    dispatch.assert_not_called()
    timer.assert_not_called()


def test_the_terminal_row_stays_failed_on_a_second_pass(db_session, no_dispatch):
    """Boot recovery re-dispatches anything still `pending`; a row that is `failed` is left
    alone — and if something DID re-run it, it must fail again rather than flip back."""
    dispatch, timer = no_dispatch
    task = f"ghost.handler.{uuid.uuid4().hex[:6]}"
    log_id = _row(db_session, task, max_attempts=3)

    ajs._execute_job_inline(db_session, log_id, task, {})
    ajs._execute_job_inline(db_session, log_id, task, {})

    log = _reload(db_session, log_id)
    assert log.status == "failed" and log.attempt_count == 2
    dispatch.assert_not_called()
    timer.assert_not_called()


def test_the_error_class_is_the_classification():
    """`RETRY-CLASSIFY-1` classifies by substring elsewhere; here it is the TYPE, so a handler
    that happens to raise a message containing "not registered" is still retryable."""
    assert ajs._is_terminal_job_error(ajs.AsyncJobHandlerNotRegistered("x"))
    assert not ajs._is_terminal_job_error(RuntimeError("handler 'x' is not registered"))
    assert issubclass(ajs.AsyncJobHandlerNotRegistered, RuntimeError), (
        "callers that caught RuntimeError before must still catch it"
    )


# ── retryable → counted, backed off, bounded ─────────────────────────────────


def test_a_retryable_failure_backs_off_and_stops_at_max_attempts(
    db_session, no_dispatch, monkeypatch
):
    """Liveness for the tests above (a re-dispatch CAN be observed), and the two other faults:
    the attempt is counted although nothing committed it before the rollback, and the retry
    goes through the Timer with the dispatcher's delay, not straight to the executor."""
    dispatch, timer = no_dispatch
    monkeypatch.setenv("AINDY_RETRY_BACKOFF_BASE_MS", "1000")
    task = f"flaky.handler.{uuid.uuid4().hex[:6]}"
    calls: list[int] = []

    def _handler(payload, db):
        calls.append(1)
        raise RuntimeError("transient")

    monkeypatch.setitem(ajs._JOB_REGISTRY, task, _handler)
    log_id = _row(db_session, task, max_attempts=2)

    # Attempt 1: retryable, budget left → pending, re-dispatch handed to a Timer with a delay.
    with patch.object(ajs, "_distributed_execution_enabled", return_value=False):
        ajs._execute_job_inline(db_session, log_id, task, {})
    log = _reload(db_session, log_id)
    assert calls == [1]
    assert log.status == "pending"
    assert log.attempt_count == 1, "the rollback discarded the attempt — the job is uncounted"
    assert not dispatch.called, "the retry went straight to the executor with no backoff"
    timer.assert_called_once()
    delay, fire = timer.call_args.args[0], timer.call_args.args[1]
    assert delay >= 1.0, f"backoff delay {delay}s — attempt 1 must wait at least the base"
    timer.return_value.start.assert_called_once()

    # The Timer's callable is the re-dispatch: fire it and it reaches the dispatcher.
    fire()
    dispatch.assert_called_once()
    assert dispatch.call_args.kwargs["context"]["retry"] is True

    # Attempt 2: budget exhausted → terminal.
    timer.reset_mock()
    dispatch.reset_mock()
    with patch.object(ajs, "_distributed_execution_enabled", return_value=False):
        ajs._execute_job_inline(db_session, log_id, task, {})
    log = _reload(db_session, log_id)
    assert calls == [1, 1]
    assert log.status == "failed" and log.attempt_count == 2
    timer.assert_not_called()
    dispatch.assert_not_called()


def test_a_zero_backoff_dispatches_immediately(db_session, no_dispatch, monkeypatch):
    """`AINDY_RETRY_BACKOFF_BASE_MS=0` keeps the old immediacy available on purpose — and
    proves the Timer path is a choice of delay, not a swallowed retry."""
    dispatch, timer = no_dispatch
    monkeypatch.setenv("AINDY_RETRY_BACKOFF_BASE_MS", "0")
    task = f"flaky.handler.{uuid.uuid4().hex[:6]}"
    monkeypatch.setitem(ajs._JOB_REGISTRY, task, lambda payload, db: (_ for _ in ()).throw(RuntimeError("t")))
    log_id = _row(db_session, task, max_attempts=2)

    with patch.object(ajs, "_distributed_execution_enabled", return_value=False):
        ajs._execute_job_inline(db_session, log_id, task, {})

    assert _reload(db_session, log_id).status == "pending"
    dispatch.assert_called_once()
    timer.assert_not_called()
