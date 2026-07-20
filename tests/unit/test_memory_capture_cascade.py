"""
RT-MEMTXN-LEAK-1 (third site) — the submit → capture → submit cascade.

Submitting an async job emits EXECUTION_STARTED; the capture engine turns that into
a memory node; saving a node enqueues an embedding job — another submission. The
recursion is synchronous and each level holds the session it opened, so unguarded it
drains the connection pool (60 conns `idle in transaction`, ~42s login) instead of
terminating.

These tests pin the three defects that combined to make it unbounded:
  1. the runtime's own memory-maintenance jobs were captured as memory,
  2. nothing bounded the nesting depth,
  3. dedup could never fire for the global (user_id IS NULL) nodes it produced.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from AINDY.core.memory_capture_guard import (
    RUNTIME_INTERNAL_TASK_NAMES,
    async_submit_depth,
    async_submit_scope,
    fresh_async_submit_depth,
    memory_capture_suppressed,
)
from AINDY.core.system_event_types import SystemEventTypes

pytestmark = pytest.mark.runtime_only


def _event(task_name: str | None = None, event_type: str | None = None):
    return SimpleNamespace(
        type=event_type or SystemEventTypes.EXECUTION_STARTED,
        payload={"task_name": task_name, "source": "memory"} if task_name else {},
        user_id=None,
        id="11111111-1111-1111-1111-111111111111",
        trace_id="trace-1",
        source="async",
    )


# ── 1. the cycle is cut at its origin ───────────────────────────────────────────

def test_runtime_internal_task_names_match_the_registered_jobs():
    """The guard's literals must not drift from the job names they exist to block."""
    from AINDY.memory.embedding_jobs import EMBEDDING_JOB_NAME, EMBEDDING_SWEEP_JOB_NAME

    assert EMBEDDING_JOB_NAME in RUNTIME_INTERNAL_TASK_NAMES
    assert EMBEDDING_SWEEP_JOB_NAME in RUNTIME_INTERNAL_TASK_NAMES


def test_embedding_job_lifecycle_event_is_not_captured_as_memory():
    from AINDY.memory.memory_capture_engine import capture_system_event_as_memory

    assert capture_system_event_as_memory(MagicMock(), _event("memory.generate_embedding")) is None
    assert capture_system_event_as_memory(MagicMock(), _event("memory.embedding_sweep")) is None


def test_ordinary_job_lifecycle_event_is_still_captured(monkeypatch):
    """The cut is scoped to runtime plumbing — real jobs keep closing the loop."""
    captured = {}

    def _fake_queue(**kwargs):
        captured.update(kwargs)
        return {"queued": True}

    monkeypatch.setattr(
        "AINDY.memory.memory_capture_engine.queue_memory_capture", _fake_queue
    )
    from AINDY.memory.memory_capture_engine import capture_system_event_as_memory

    result = capture_system_event_as_memory(MagicMock(), _event("agent.run"))
    assert result == {"queued": True}
    assert captured["event_type"] == SystemEventTypes.EXECUTION_STARTED


# ── 2. nesting is bounded ───────────────────────────────────────────────────────

def test_capture_allowed_at_the_outermost_submission_only():
    assert async_submit_depth() == 0
    assert memory_capture_suppressed() is False

    with async_submit_scope():
        # Depth 1: a real submission — its lifecycle event is still captured.
        assert async_submit_depth() == 1
        assert memory_capture_suppressed() is False

        with async_submit_scope():
            # Depth 2: this submission exists only because the depth-1 capture
            # enqueued an embedding job. Capturing it would close the cycle.
            assert async_submit_depth() == 2
            assert memory_capture_suppressed() is True

    assert async_submit_depth() == 0


def test_scope_depth_is_restored_on_exception():
    with pytest.raises(RuntimeError):
        with async_submit_scope():
            raise RuntimeError("boom")
    assert async_submit_depth() == 0


def test_nested_submission_event_is_dropped(monkeypatch):
    monkeypatch.setattr(
        "AINDY.memory.memory_capture_engine.queue_memory_capture",
        lambda **kwargs: {"queued": True},
    )
    from AINDY.memory.memory_capture_engine import capture_system_event_as_memory

    event = _event("agent.run")
    with async_submit_scope():
        with async_submit_scope():
            assert capture_system_event_as_memory(MagicMock(), event) is None


def test_executing_job_starts_at_a_fresh_depth():
    """A thread hand-off ends the synchronous chain, so capture resumes."""
    with async_submit_scope():
        with async_submit_scope():
            assert memory_capture_suppressed() is True
            with fresh_async_submit_depth():
                assert async_submit_depth() == 0
                assert memory_capture_suppressed() is False
            assert async_submit_depth() == 2


def test_execute_job_resets_depth(monkeypatch):
    seen: list[int] = []

    monkeypatch.setattr(
        "AINDY.platform_layer.async_job_service.SessionLocal", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "AINDY.platform_layer.async_job_service._execute_job_inline",
        lambda db, log_id, task_name, payload: seen.append(async_submit_depth()),
    )
    from AINDY.platform_layer.async_job_service import _execute_job

    with async_submit_scope():
        with async_submit_scope():
            _execute_job("log-1", "agent.run", {})
    assert seen == [0]


def test_submit_async_job_enters_the_scope(monkeypatch):
    """The scope must wrap the submission body, not merely exist."""
    seen: list[int] = []

    def _inner(**kwargs):
        seen.append(async_submit_depth())
        return "log-1"

    monkeypatch.setattr(
        "AINDY.platform_layer.async_job_service._submit_async_job_inner", _inner
    )
    from AINDY.platform_layer.async_job_service import submit_async_job

    assert submit_async_job(task_name="t", payload={}, user_id=None, source="s") == "log-1"
    assert seen == [1]
    assert async_submit_depth() == 0


# ── 3. dedup works for global (user_id IS NULL) captures ────────────────────────

def _engine_with_recording_db(user_id):
    from AINDY.memory.memory_capture_engine import MemoryCaptureEngine

    db = MagicMock()
    statements: list[str] = []

    def _execute(stmt, params=None):
        statements.append(str(stmt))
        result = MagicMock()
        result.fetchone.return_value = None
        return result

    db.execute.side_effect = _execute
    engine = MemoryCaptureEngine(db=db, user_id=user_id, agent_namespace="system")
    return engine, statements


def test_dedup_uses_is_null_for_global_captures():
    """`user_id = NULL` is never true — the cascade's nodes were never deduped."""
    engine, statements = _engine_with_recording_db(None)
    engine._is_duplicate("execution.started from async")

    assert "user_id IS NULL" in statements[0]
    assert "user_id = :uid" not in statements[0]


def test_dedup_still_scopes_to_the_owner_when_there_is_one():
    engine, statements = _engine_with_recording_db("user-1")
    engine._is_duplicate("something")

    assert "user_id = :uid" in statements[0]
