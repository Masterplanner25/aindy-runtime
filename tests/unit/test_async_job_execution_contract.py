"""FR-17 — the async-job path is an execution boundary, and the gate must know it.

``emit_system_event`` refuses any ``execution.*`` event emitted with neither a pipeline
nor the async-execution context active. That guard is right for a route that skipped the
pipeline; it is wrong for an async job, which *is* an execution and frequently has no
HTTP request behind it — a scheduler tick, the event-bus subscriber thread, an app
bootstrap. The app team saw the refusal as a warning on a live 2.3.0 stack:

    WARNING [AsyncJob] Emitting execution.started … ExecutionContract violation:
            execution event 'execution.started' emitted outside …

The event was caught and dropped, so nothing broke and the job's start was simply never
recorded. Two sites were affected — submission, and the worker thread, where the context
was gated on ``AINDY_ASYNC_JOB_LOOP_CLOSURE`` (default off) so *every* async job's
``execution.completed`` / ``execution.failed`` was discarded too.

These tests drive the real emit path rather than reading it: the whole defect was that a
call that looked right was answered by a gate the caller could not see.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from AINDY.core.system_event_service import emit_system_event
from AINDY.core.system_event_types import SystemEventTypes
from AINDY.db.models.system_event import SystemEvent
from AINDY.platform_layer import async_job_service
from AINDY.platform_layer.async_execution_context import (
    async_execution_scope,
    is_async_execution_active,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _no_pipeline(monkeypatch):
    """Every test here is the no-pipeline case; the ContextVar is known to leak."""
    monkeypatch.setattr(
        "AINDY.core.system_event_service.is_pipeline_active", lambda: False
    )


@pytest.fixture
def _no_memory_capture(monkeypatch):
    """Capture is a separate subsystem with its own cycle guards — out of scope here."""
    monkeypatch.setattr(
        "AINDY.memory.memory_capture_engine.capture_system_event_as_memory",
        lambda db, event: None,
    )


class TestScope:
    def test_sets_and_restores(self):
        assert is_async_execution_active() is False
        with async_execution_scope():
            assert is_async_execution_active() is True
        assert is_async_execution_active() is False

    def test_nesting_restores_the_outer_value_not_the_default(self):
        with async_execution_scope():
            with async_execution_scope():
                assert is_async_execution_active() is True
            assert is_async_execution_active() is True
        assert is_async_execution_active() is False

    def test_restores_on_exception(self):
        with pytest.raises(ValueError):
            with async_execution_scope():
                raise ValueError("boom")
        assert is_async_execution_active() is False


class TestTheGateItself:
    """The liveness control: without the scope the gate really does refuse."""

    def test_execution_event_outside_a_pipeline_is_refused(self, db_session, _no_memory_capture):
        with pytest.raises(RuntimeError, match="ExecutionContract violation"):
            emit_system_event(
                db=db_session,
                event_type=SystemEventTypes.EXECUTION_STARTED,
                trace_id=str(uuid.uuid4()),
                source="async",
                payload={},
                required=True,
            )

    def test_the_scope_makes_the_same_call_land(self, db_session, _no_memory_capture):
        trace_id = str(uuid.uuid4())
        with async_execution_scope():
            emit_system_event(
                db=db_session,
                event_type=SystemEventTypes.EXECUTION_STARTED,
                trace_id=trace_id,
                source="async",
                payload={"run_id": trace_id},
                required=True,
            )
        assert (
            db_session.query(SystemEvent)
            .filter(
                SystemEvent.trace_id == trace_id,
                SystemEvent.type == SystemEventTypes.EXECUTION_STARTED,
            )
            .first()
            is not None
        )


class TestSubmitRecordsItsStart:
    """The reported symptom: a job submitted with no pipeline had no start row."""

    def test_submission_persists_execution_started(
        self, db_session, db_session_factory, monkeypatch, _no_memory_capture
    ):
        monkeypatch.setattr(async_job_service, "SessionLocal", db_session_factory)
        monkeypatch.setattr(async_job_service, "_execute_job_inline", lambda *a, **kw: None)
        monkeypatch.setitem(
            async_job_service._JOB_REGISTRY, "fr17_probe", lambda payload, db: {"ok": True}
        )

        log_id = async_job_service.submit_async_job(
            task_name="fr17_probe",
            payload={"probe": True},
            user_id=None,
            source="test",
        )

        started = (
            db_session.query(SystemEvent)
            .filter(
                SystemEvent.trace_id == str(log_id),
                SystemEvent.type == SystemEventTypes.EXECUTION_STARTED,
            )
            .first()
        )
        assert started is not None, "async job submitted outside a pipeline recorded no start"
        assert started.payload.get("task_name") == "fr17_probe"

    def test_the_context_does_not_leak_past_the_emit(
        self, db_session, db_session_factory, monkeypatch, _no_memory_capture
    ):
        """A submission must not silently exempt whatever the caller does next."""
        monkeypatch.setattr(async_job_service, "SessionLocal", db_session_factory)
        monkeypatch.setattr(async_job_service, "_execute_job_inline", lambda *a, **kw: None)
        monkeypatch.setitem(
            async_job_service._JOB_REGISTRY, "fr17_probe", lambda payload, db: {"ok": True}
        )

        async_job_service.submit_async_job(
            task_name="fr17_probe", payload={}, user_id=None, source="test"
        )

        assert is_async_execution_active() is False


class TestWorkerThreadRecordsItsEnd:
    """The half the report did not name: with the flag off, so was every completion."""

    def test_inline_execution_activates_the_context_without_the_loop_closure_flag(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            async_job_service, "_async_job_loop_closure_enabled", lambda: False
        )
        seen: list[bool] = []

        def _spy(db, trace_id):
            seen.append(is_async_execution_active())
            return None

        monkeypatch.setattr(async_job_service, "_ensure_root_execution_event_id", _spy)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        # No `automation_logs` attribute → the fake-db lookup returns None and the
        # function returns early, right after the context work under test.
        del db.automation_logs

        async_job_service._execute_job_inline(db, str(uuid.uuid4()), "fr17_probe", {})

        assert seen == [True], "worker thread emits execution.* with the gate closed"
        assert is_async_execution_active() is False
