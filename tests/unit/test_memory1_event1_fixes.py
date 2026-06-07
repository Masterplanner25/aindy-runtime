"""
Regression tests for MEMORY-1 and EVENT-1 fixes.

MEMORY-1: persist_memory_ingest_payload uses a single transaction — if append_node
  fails, the entire write (trace + node) is rolled back rather than leaving orphaned rows.

EVENT-1: _safe_emit_event sets _emission_failed on the context when emission fails,
  preventing re-entrant emission attempts on a broken DB session.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
from sqlalchemy.exc import SQLAlchemyError

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# MEMORY-1 — atomic ingest transaction
# ---------------------------------------------------------------------------

class TestPersistMemoryIngestPayloadAtomic:
    """persist_memory_ingest_payload must be all-or-nothing."""

    def _make_payload(self):
        from AINDY.memory.memory_ingest_service import MemoryIngestPayload
        return MemoryIngestPayload(
            path="/test/path.md",
            user_id="user-123",
            content="test content",
            origin_kind="flow",
            title="Test Title",
            description=None,
            extra={},
            tags=["test"],
        )

    def test_success_path_commits_once(self):
        """On success all three DAO calls happen and a single db.commit() fires."""
        from AINDY.memory.memory_ingest_service import persist_memory_ingest_payload

        mock_db = MagicMock()
        fake_trace = {"id": "trace-id-1"}
        fake_node = {"id": "node-id-1"}

        with patch("AINDY.memory.memory_ingest_service.SessionLocal", return_value=mock_db):
            with patch("AINDY.memory.memory_ingest_service.MemoryTraceDAO") as MockTraceDAO:
                with patch("AINDY.memory.memory_ingest_service.MemoryNodeDAO") as MockNodeDAO:
                    trace_dao = MockTraceDAO.return_value
                    node_dao = MockNodeDAO.return_value
                    trace_dao.create_trace.return_value = fake_trace
                    node_dao.save.return_value = fake_node
                    trace_dao.append_node.return_value = {"id": "link-id-1"}

                    result = persist_memory_ingest_payload(self._make_payload())

        assert result.status == "ingested"
        assert result.trace_id == "trace-id-1"
        assert result.node_id == "node-id-1"
        # All three DAO calls passed commit=False
        trace_dao.create_trace.assert_called_once()
        _, kw = trace_dao.create_trace.call_args
        assert kw.get("commit") is False

        node_dao.save.assert_called_once()
        _, kw = node_dao.save.call_args
        assert kw.get("commit") is False

        trace_dao.append_node.assert_called_once()
        _, kw = trace_dao.append_node.call_args
        assert kw.get("commit") is False

        # Single commit at the end
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

    def test_append_node_failure_rolls_back_entire_transaction(self):
        """When append_node raises, rollback fires and status is 'failed'."""
        from AINDY.memory.memory_ingest_service import persist_memory_ingest_payload

        mock_db = MagicMock()

        with patch("AINDY.memory.memory_ingest_service.SessionLocal", return_value=mock_db):
            with patch("AINDY.memory.memory_ingest_service.MemoryTraceDAO") as MockTraceDAO:
                with patch("AINDY.memory.memory_ingest_service.MemoryNodeDAO") as MockNodeDAO:
                    trace_dao = MockTraceDAO.return_value
                    node_dao = MockNodeDAO.return_value
                    trace_dao.create_trace.return_value = {"id": "trace-id-1"}
                    node_dao.save.return_value = {"id": "node-id-1"}
                    trace_dao.append_node.side_effect = SQLAlchemyError("FK constraint")

                    result = persist_memory_ingest_payload(self._make_payload())

        assert result.status == "failed"
        assert result.trace_id is None
        assert result.node_id is None
        assert "FK constraint" in (result.message or "")
        # Must rollback, must NOT commit
        mock_db.rollback.assert_called_once()
        mock_db.commit.assert_not_called()

    def test_create_trace_failure_rolls_back(self):
        """When create_trace raises, rollback fires and status is 'failed'."""
        from AINDY.memory.memory_ingest_service import persist_memory_ingest_payload

        mock_db = MagicMock()

        with patch("AINDY.memory.memory_ingest_service.SessionLocal", return_value=mock_db):
            with patch("AINDY.memory.memory_ingest_service.MemoryTraceDAO") as MockTraceDAO:
                with patch("AINDY.memory.memory_ingest_service.MemoryNodeDAO"):
                    trace_dao = MockTraceDAO.return_value
                    trace_dao.create_trace.side_effect = SQLAlchemyError("DB down")

                    result = persist_memory_ingest_payload(self._make_payload())

        assert result.status == "failed"
        mock_db.rollback.assert_called_once()
        mock_db.commit.assert_not_called()

    def test_session_always_closed(self):
        """db.close() is called regardless of success or failure."""
        from AINDY.memory.memory_ingest_service import persist_memory_ingest_payload

        mock_db = MagicMock()

        with patch("AINDY.memory.memory_ingest_service.SessionLocal", return_value=mock_db):
            with patch("AINDY.memory.memory_ingest_service.MemoryTraceDAO") as MockTraceDAO:
                with patch("AINDY.memory.memory_ingest_service.MemoryNodeDAO"):
                    MockTraceDAO.return_value.create_trace.side_effect = RuntimeError("boom")
                    persist_memory_ingest_payload(self._make_payload())

        mock_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# EVENT-1 — explicit emission re-entrance guard
# ---------------------------------------------------------------------------

class TestEmissionReentranceGuard:
    """_safe_emit_event must skip re-emission when _emission_failed is set on ctx."""

    def _make_ctx(self, **meta):
        from AINDY.core.execution_pipeline.context import ExecutionContext
        ctx = ExecutionContext(
            user_id="user-1",
            route_name="test.route",
            request_id="req-1",
        )
        ctx.metadata.update(meta)
        return ctx

    def test_first_emission_failure_sets_flag(self):
        """When emit_system_event raises, _emission_failed is set on ctx.metadata."""
        from AINDY.core.execution_pipeline.pipeline import ExecutionPipeline

        pipeline = ExecutionPipeline()
        mock_db = MagicMock()
        ctx = self._make_ctx(db=mock_db)

        with patch(
            "AINDY.core.system_event_service.emit_system_event",
            side_effect=Exception("DB dead"),
        ):
            result = pipeline._safe_emit_event(ctx, event_type="execution.completed")

        assert result is None
        assert ctx.metadata.get("_emission_failed") is True

    def test_subsequent_emission_skipped_when_flag_set(self):
        """A second _safe_emit_event call is a no-op when _emission_failed is already True."""
        from AINDY.core.execution_pipeline.pipeline import ExecutionPipeline

        pipeline = ExecutionPipeline()
        mock_db = MagicMock()
        ctx = self._make_ctx(db=mock_db, _emission_failed=True)

        with patch(
            "AINDY.core.system_event_service.emit_system_event"
        ) as mock_emit:
            result = pipeline._safe_emit_event(ctx, event_type="execution.failed")

        assert result is None
        mock_emit.assert_not_called()

    def test_successful_emission_does_not_set_flag(self):
        """A successful emission must NOT set _emission_failed."""
        from AINDY.core.execution_pipeline.pipeline import ExecutionPipeline

        pipeline = ExecutionPipeline()
        mock_db = MagicMock()
        ctx = self._make_ctx(db=mock_db)

        with patch(
            "AINDY.core.system_event_service.emit_system_event",
            return_value="event-id-abc",
        ):
            result = pipeline._safe_emit_event(ctx, event_type="execution.started")

        assert result == "event-id-abc"
        assert not ctx.metadata.get("_emission_failed")

    def test_loop_scenario_terminates(self):
        """Simulates the loop scenario: completed fails → failed also skipped, no infinite loop."""
        from AINDY.core.execution_pipeline.pipeline import ExecutionPipeline

        pipeline = ExecutionPipeline()
        mock_db = MagicMock()
        ctx = self._make_ctx(db=mock_db)

        call_count = 0

        def flaky_emit(**kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("DB failure")

        with patch("AINDY.core.system_event_service.emit_system_event", side_effect=flaky_emit):
            # First: execution.completed fails
            r1 = pipeline._safe_emit_event(ctx, event_type="execution.completed")
            # Second: pipeline tries execution.failed — should be skipped by guard
            r2 = pipeline._safe_emit_event(ctx, event_type="execution.failed")

        assert r1 is None
        assert r2 is None
        # emit_system_event called exactly once (the failed attempt), not twice
        assert call_count == 1
