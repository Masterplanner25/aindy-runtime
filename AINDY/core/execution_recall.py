"""Recall-used event emitter (INFINITY-RUNTIME-1, Gaps 1 & 2).

The Infinity loop audit found that memory retrieval happens silently — the
learning loop improves but cannot explain what it recalled. This module emits a
single ``RECALL_USED`` SystemEvent describing what was pulled into a planning or
execution context (query, node ids, count), making recall auditable.

The event is emitted only when at least one node was recalled (an empty recall
is not signal). ``RECALL_USED`` deliberately does not carry the ``execution.``
prefix, so it is not subject to the pipeline execution-contract gate. The
emitter is best-effort: it never raises into the caller.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_QUERY_PREVIEW_LEN = 200


def emit_recall_used(
    *,
    db,
    node_ids: list[Any] | None,
    query: str | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    operation_type: str | None = None,
    source: str = "agent",
    parent_event_id: str | None = None,
) -> str | None:
    """Emit a ``RECALL_USED`` event for one recall into a planning/exec context.

    No-op (returns ``None``) when ``node_ids`` is empty. Best-effort — never
    raises into the caller's recall path.
    """
    ids = [str(n) for n in (node_ids or []) if n]
    if not ids:
        return None

    from AINDY.core.execution_signal_helper import queue_system_event
    from AINDY.core.system_event_types import SystemEventTypes

    payload: dict[str, Any] = {
        "query": str(query)[:_QUERY_PREVIEW_LEN] if query else None,
        "node_ids": ids,
        "count": len(ids),
        "operation_type": operation_type,
    }

    try:
        return queue_system_event(
            db=db,
            event_type=SystemEventTypes.RECALL_USED,
            user_id=user_id,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
            source=source,
            payload=payload,
            required=False,
        )
    except Exception as exc:  # recall must never break on observability
        logger.warning("[ExecutionRecall] emit failed: %s", exc)
        return None
