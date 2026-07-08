"""Per-execution score record (INFINITY-RUNTIME-1, Gap 3).

The Infinity loop audit found that scoring is applied at the *memory-node*
level (``MemoryLearningEngine`` updates recalled nodes) but there is no single
``{run_id, score, dimensions}`` record written after each *execution*. This
module supplies the canonical scalar scorer and the emitter for a single
``SCORE_COMPUTED`` SystemEvent per finished execution.

The event row itself IS the durable, trace-queryable score record — no schema
table is required. ``SCORE_COMPUTED`` deliberately does not carry the
``execution.`` prefix, so it is not subject to the pipeline execution-contract
gate and can be emitted from background completion paths.

Both the scorer and the emitter are best-effort: they never raise into the
caller's completion path.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Terminal statuses that floor the score to 0.0 regardless of the result body.
_FAILURE_STATUSES = frozenset(
    {"failed", "error", "cancelled", "verify_failed", "dead_letter", "rejected"}
)
# Terminal statuses that should not score below neutral just because the
# result dict lacks explicit success markers.
_SUCCESS_STATUSES = frozenset({"completed", "success", "verified", "executed"})
_SUCCESS_FLOOR = 0.6


def compute_execution_score(*, status: str | None, result: Any) -> float:
    """Scalar 0.0–1.0 score for a finished execution.

    Failure statuses floor to 0.0. Otherwise defer to the shared
    ``evaluate_result`` heuristic, raising a terminal-success run to at least
    ``_SUCCESS_FLOOR`` so an unannotated success is not scored as a failure.
    """
    normalized = (status or "").strip().lower()
    if normalized in _FAILURE_STATUSES:
        return 0.0

    try:
        from AINDY.runtime.memory.memory_learning import evaluate_result

        heuristic = float(evaluate_result(result))
    except Exception:  # pragma: no cover - defensive; scorer must not break completion
        heuristic = _SUCCESS_FLOOR if normalized in _SUCCESS_STATUSES else 0.5

    if normalized in _SUCCESS_STATUSES:
        return max(heuristic, _SUCCESS_FLOOR)
    return heuristic


def emit_execution_score(
    *,
    db,
    run_id: str | None,
    score: float,
    status: str | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    duration_ms: int | float | None = None,
    dimensions: dict[str, Any] | None = None,
    source: str = "agent",
    parent_event_id: str | None = None,
) -> str | None:
    """Emit a single ``SCORE_COMPUTED`` event for one finished execution.

    The payload ``{run_id, score, status, dimensions[, duration_ms]}`` is the
    per-run score record consumed by the app-side Infinity scoring layer.
    Best-effort — never raises into the caller.
    """
    from AINDY.core.execution_signal_helper import queue_system_event
    from AINDY.core.system_event_types import SystemEventTypes

    payload: dict[str, Any] = {
        "run_id": str(run_id) if run_id else None,
        "score": round(float(score), 4),
        "status": status,
        "dimensions": dict(dimensions or {}),
    }
    if duration_ms is not None:
        try:
            payload["duration_ms"] = int(duration_ms)
        except (TypeError, ValueError):
            pass

    try:
        return queue_system_event(
            db=db,
            event_type=SystemEventTypes.SCORE_COMPUTED,
            user_id=user_id,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
            source=source,
            payload=payload,
            required=False,
        )
    except Exception as exc:  # scoring must never break the completion path
        logger.warning("[ExecutionScore] emit failed run=%s: %s", run_id, exc)
        return None
