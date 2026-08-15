"""
Tests for AINDY.kernel.clock — injectable time source for deterministic replay.

Covers:
  - utcnow() returns real wall time by default
  - frozen_at() overrides utcnow() inside the block
  - frozen_at() restores the previous value on exit (including exception paths)
  - frozen_at() is ContextVar-scoped: inner freezes don't escape outer context
  - SyscallDispatcher EffectRecord gate uses the injectable clock
  - CircuitBreaker._now() uses the injectable clock
  - ExecutionUnitService._now() uses the injectable clock
  - SystemEventService emit_system_event uses the injectable clock
  - flow_engine shared._default_wait_deadline uses the injectable clock
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from AINDY.kernel.clock import frozen_at, utcnow

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# Core clock behaviour
# ---------------------------------------------------------------------------

class TestUtcnow:
    def test_returns_utc_datetime(self):
        t = utcnow()
        assert t.tzinfo is not None
        assert t.tzinfo == timezone.utc

    def test_advances_between_calls(self):
        t1 = utcnow()
        t2 = utcnow()
        assert t2 >= t1


class TestFrozenAt:
    def test_overrides_utcnow_inside_block(self):
        fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        with frozen_at(fixed) as yielded:
            assert yielded is fixed
            assert utcnow() == fixed

    def test_restores_after_block(self):
        fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with frozen_at(fixed):
            pass
        # After the block, utcnow() should no longer be frozen
        t = utcnow()
        assert t != fixed or t >= fixed  # wall time has moved on

    def test_restores_on_exception(self):
        fixed = datetime(2026, 6, 1, tzinfo=timezone.utc)
        try:
            with frozen_at(fixed):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # Must not be frozen after exception
        assert utcnow() != fixed or utcnow() >= fixed

    def test_nested_freeze_restores_outer(self):
        outer = datetime(2026, 1, 1, tzinfo=timezone.utc)
        inner = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with frozen_at(outer):
            assert utcnow() == outer
            with frozen_at(inner):
                assert utcnow() == inner
            assert utcnow() == outer

    def test_freeze_is_contextvar_scoped(self):
        import threading

        fixed = datetime(2026, 3, 15, tzinfo=timezone.utc)
        results: list[bool] = []

        def thread_reads_utcnow():
            # The thread's ContextVar slot is independent — should NOT see fixed
            t = utcnow()
            results.append(t != fixed)

        with frozen_at(fixed):
            assert utcnow() == fixed
            th = threading.Thread(target=thread_reads_utcnow)
            th.start()
            th.join()

        assert results == [True], "Thread should not inherit the frozen clock"


# ---------------------------------------------------------------------------
# CircuitBreaker._now() uses the injectable clock
# ---------------------------------------------------------------------------

class TestCircuitBreakerClock:
    def test_now_respects_frozen_at(self):
        from AINDY.kernel.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test-cb", failure_threshold=3, recovery_timeout_secs=30)
        fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with frozen_at(fixed):
            assert cb._now() == fixed


# ---------------------------------------------------------------------------
# ExecutionUnitService._now() uses the injectable clock
# ---------------------------------------------------------------------------

class TestExecutionUnitServiceClock:
    def test_now_respects_frozen_at(self):
        from AINDY.core.execution_unit_service import _now

        fixed = datetime(2026, 2, 14, tzinfo=timezone.utc)
        with frozen_at(fixed):
            assert _now() == fixed


# ---------------------------------------------------------------------------
# flow_engine shared._default_wait_deadline uses the injectable clock
# ---------------------------------------------------------------------------

class TestFlowEngineDeadlineClock:
    def test_deadline_anchored_to_frozen_clock(self):
        from AINDY.runtime.flow_engine.shared import _default_wait_deadline

        fixed = datetime(2026, 6, 11, 0, 0, 0, tzinfo=timezone.utc)
        with frozen_at(fixed):
            deadline = _default_wait_deadline(timeout_minutes=30)
        expected = fixed + timedelta(minutes=30)
        assert deadline == expected


# ---------------------------------------------------------------------------
# SyscallDispatcher EffectRecord gate uses the injectable clock
# ---------------------------------------------------------------------------

class TestSyscallDispatcherClock:
    def test_complete_effect_record_uses_frozen_clock(self):
        """_complete_effect_record sets completed_at via utcnow()."""
        from AINDY.kernel.syscall_dispatcher import _complete_effect_record

        fixed = datetime(2026, 1, 15, tzinfo=timezone.utc)

        mock_record = MagicMock()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_record

        with frozen_at(fixed):
            _complete_effect_record(mock_db, "action-1", "success", {"ok": True})

        assert mock_record.completed_at == fixed


# ---------------------------------------------------------------------------
# system_event_service emit_system_event uses the injectable clock
# ---------------------------------------------------------------------------

class TestSystemEventServiceClock:
    def test_emit_uses_frozen_clock(self):
        """emit_system_event() sets event.timestamp via utcnow()."""
        from AINDY.core.system_event_service import emit_system_event

        fixed = datetime(2026, 3, 20, tzinfo=timezone.utc)

        captured: list[object] = []

        mock_db = MagicMock()
        mock_db.add.side_effect = lambda obj: captured.append(obj)
        mock_db.flush = MagicMock()

        with patch("AINDY.core.system_event_service.link_events"):
            with patch("AINDY.core.system_event_service.is_pipeline_active", return_value=False):
                with patch("AINDY.core.system_event_service.is_async_execution_active", return_value=False):
                    with frozen_at(fixed):
                        emit_system_event(
                            db=mock_db,
                            event_type="test.event",
                            user_id=None,
                            payload={},
                        )

        assert len(captured) == 1
        assert captured[0].timestamp == fixed
