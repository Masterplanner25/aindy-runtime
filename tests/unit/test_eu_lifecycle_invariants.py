"""
Kernel hardening — EU lifecycle invariants.

Verifies three guarantees introduced/hardened by EXEC-EU-1:
  1. _safe_finalize_eu is idempotent: eu_finalized flag prevents double-close.
  2. ExecutionPipeline.run() always calls finalize on any exit path
     (success, HTTPException, unexpected Exception).
  3. The finally block does NOT call finalize when eu_status == "waiting"
     (the EU stays open, waiting for a resume event).
"""
from __future__ import annotations

import asyncio
import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock

from AINDY.core.execution_pipeline.context import ExecutionContext
from AINDY.core.execution_pipeline.pipeline import ExecutionPipeline
from AINDY.core.execution_pipeline.resources import _safe_finalize_eu

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(**meta):
    ctx = ExecutionContext(
        request_id="trace-test",
        route_name="test.route",
        user_id="user-test",
        metadata={"eu_id": "eu-test", "db": MagicMock(), **meta},
    )
    return ctx


class _PipelineSpy(ExecutionPipeline):
    """Minimal instrumented pipeline: stubs all side-effectful methods,
    exposes finalize_calls list for assertions."""

    def __init__(self):
        self.finalize_calls: list[str] = []

    def _requires_route_side_effects(self, ctx): return False
    def _safe_emit_event(self, ctx, *, event_type, **kw): return f"evt-{event_type}"
    def _safe_set_parent_event(self, event_id): return None
    def _safe_reset_parent_event(self, token): pass
    def _safe_set_pipeline_active(self): return None
    def _safe_reset_pipeline_active(self, token): pass
    def _safe_set_current_execution_context(self, ctx): return None
    def _safe_reset_current_execution_context(self, token): pass
    def _safe_require_eu(self, ctx): ctx.metadata.setdefault("eu_id", "eu-test")
    def _safe_check_quota(self, ctx, started_event_id=None): return True
    def _safe_rm_mark_started(self, ctx): pass
    def _safe_rm_record_and_complete(self, ctx, duration_ms): pass
    def _safe_rm_mark_completed(self, ctx): pass
    def _set_event_refs(self, ctx, started_id, terminal_event_id=None, completed=False): pass
    def _detect_wait(self, result): return None
    def _safe_transition_eu_waiting(self, ctx, wait_for=None, wait_condition=None): pass
    def _inject_execution_envelope(self, ctx, result, duration_ms): return result
    def _extract_execution_result_and_signals(self, result): return result, {}
    def _merge_queued_signals(self, ctx, signals): return signals
    def _apply_execution_signals(self, ctx, signals): return 0
    def _apply_memory_signals(self, ctx, signals): pass
    def _apply_event_signals(self, ctx, signals): pass
    def _apply_log_signal(self, ctx, signals): pass
    def _apply_execution_hints(self, ctx, result): pass
    def _extract_memory_context_count(self, result): return 0
    def _safe_recall_memory_count(self, ctx): return 0
    def _safe_capture_memory_hint(self, ctx, result): pass
    def _handle_contract_violation(self, message): pass
    def _record_side_effect(self, ctx, name, *, status, required=False, error=None): pass

    def _safe_finalize_eu(self, ctx, status: str) -> None:
        self.finalize_calls.append(status)
        ctx.metadata["eu_finalized"] = True


# ---------------------------------------------------------------------------
# 1. _safe_finalize_eu idempotency (pure unit, no pipeline needed)
# ---------------------------------------------------------------------------

class _MinimalPipeline(ExecutionPipeline):
    def _record_side_effect(self, ctx, name, *, status, required=False, error=None): pass


def test_safe_finalize_eu_is_idempotent():
    pipe = _MinimalPipeline()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    ctx = _ctx(db=mock_db)

    from unittest.mock import patch
    with patch("AINDY.core.execution_unit_service.ExecutionUnitService") as mock_svc:
        mock_svc.return_value.update_status.return_value = True
        _safe_finalize_eu(pipe, ctx, "completed")
        assert ctx.metadata.get("eu_finalized") is True

        # Second call — guard fires, update_status not called again
        call_count_before = mock_svc.return_value.update_status.call_count
        _safe_finalize_eu(pipe, ctx, "failed")
        assert mock_svc.return_value.update_status.call_count == call_count_before


def test_safe_finalize_eu_noop_without_eu_id():
    pipe = _MinimalPipeline()
    ctx = ExecutionContext(
        request_id="r1", route_name="test", user_id="u1",
        metadata={"db": MagicMock()},  # no eu_id
    )
    _safe_finalize_eu(pipe, ctx, "completed")
    assert not ctx.metadata.get("eu_finalized")


def test_safe_finalize_eu_noop_without_db():
    pipe = _MinimalPipeline()
    ctx = ExecutionContext(
        request_id="r1", route_name="test", user_id="u1",
        metadata={"eu_id": "eu-1"},  # no db
    )
    _safe_finalize_eu(pipe, ctx, "completed")
    assert not ctx.metadata.get("eu_finalized")


# ---------------------------------------------------------------------------
# 2. Pipeline always finalizes EU on every exit path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_finalizes_eu_on_success():
    pipe = _PipelineSpy()
    ctx = _ctx()

    await pipe.run(ctx, lambda c: {"result": "ok"})

    assert ctx.metadata.get("eu_finalized") is True
    assert "completed" in pipe.finalize_calls


@pytest.mark.asyncio
async def test_pipeline_finalizes_eu_on_http_exception():
    pipe = _PipelineSpy()
    ctx = _ctx()

    def _raise_http(c):
        raise HTTPException(status_code=422, detail="bad input")

    await pipe.run(ctx, _raise_http)

    assert ctx.metadata.get("eu_finalized") is True
    assert "failed" in pipe.finalize_calls


@pytest.mark.asyncio
async def test_pipeline_finalizes_eu_on_unexpected_exception():
    pipe = _PipelineSpy()
    ctx = _ctx()

    def _raise_generic(c):
        raise RuntimeError("unexpected crash")

    await pipe.run(ctx, _raise_generic)

    assert ctx.metadata.get("eu_finalized") is True
    assert "failed" in pipe.finalize_calls


# ---------------------------------------------------------------------------
# 3. Waiting path: finally block must NOT call finalize
# ---------------------------------------------------------------------------

class _WaitingPipelineSpy(_PipelineSpy):
    """Simulates the EU entering the waiting state — _detect_wait returns a signal."""

    def _detect_wait(self, result):
        return ("some.event", {}, None)

    def _safe_transition_eu_waiting(self, ctx, wait_for=None, wait_condition=None):
        ctx.metadata["eu_status"] = "waiting"


@pytest.mark.asyncio
async def test_pipeline_skips_finalize_on_waiting_path():
    pipe = _WaitingPipelineSpy()
    ctx = _ctx()

    await pipe.run(ctx, lambda c: {"status": "WAITING"})

    assert ctx.metadata.get("eu_status") == "waiting"
    # finalize must not have been called — EU stays open for resume
    assert not ctx.metadata.get("eu_finalized")
    assert pipe.finalize_calls == []


# ---------------------------------------------------------------------------
# 4. ExecutionContract self-consistency: the pipeline marks itself active
#    BEFORE emitting its own execution.started (#152).
# ---------------------------------------------------------------------------

class _OrderTrackingPipeline(_PipelineSpy):
    """Records the order of pipeline_active vs the execution.started emit."""

    def __init__(self):
        super().__init__()
        self.calls: list[str] = []
        self.pipeline_active_at_started_emit: bool | None = None

    def _safe_set_pipeline_active(self):
        self.calls.append("set_pipeline_active")
        self._active = True
        return "pipeline-token"

    def _safe_emit_event(self, ctx, *, event_type, **kw):
        if event_type == "execution.started":
            self.calls.append("emit:execution.started")
            # The pipeline's own active flag must already be set when its first
            # execution.* event is emitted, or the ExecutionContract guard rejects
            # it (and on PostgreSQL the failed INSERT aborts the transaction, #152).
            self.pipeline_active_at_started_emit = getattr(self, "_active", False)
        return f"evt-{event_type}"


@pytest.mark.asyncio
async def test_pipeline_active_set_before_own_execution_started():
    """#152 reopened: a pipeline with no ambient pipeline/async context (e.g. an
    app tool run from a scheduler-driven agent resume) must not depend on an outer
    context to emit its own execution.started. It marks itself active first."""
    pipe = _OrderTrackingPipeline()
    ctx = _ctx()

    await pipe.run(ctx, lambda c: {"ok": True})

    assert pipe.pipeline_active_at_started_emit is True
    assert pipe.calls.index("set_pipeline_active") < pipe.calls.index(
        "emit:execution.started"
    )
