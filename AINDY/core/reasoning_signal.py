"""First-class reasoning-signal emitter (RTR-6 — reasoning at the memory layer).

The memory layer already recalls context and captures significance-scored
insights, but the *capture* signal was only observable indirectly — its reasoning
attributes (significance, impact, causal depth, memory type) were buried in the
``MEMORY_WRITE`` payload + ``MemoryNode`` columns. RTR-6 promotes it to a
first-class ``reasoning.signal`` the learning loop can consume directly.

``kind="capture"`` — a significance-scored insight derived and stored (output).
``kind="recall"``  — reserved: recall inputs are already first-class via
``RECALL_USED`` (INFINITY-RUNTIME-1), so nothing wires this today; the
discriminator keeps the surface open for future memory-derived reasoning kinds.

Event-row-as-record: no new table. ``REASONING_SIGNAL`` is deliberately not
prefixed ``execution.`` so it can be emitted outside a pipeline/async context
(mirrors the Infinity ledger events). The emitter is best-effort and never raises
into the caller — reasoning observability must not break recall or capture.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_RECALL = "recall"
_CAPTURE = "capture"
_VALID_KINDS = frozenset({_RECALL, _CAPTURE})


def emit_reasoning_signal(
    *,
    db,
    kind: str,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    source: str = "memory",
    parent_event_id: str | None = None,
) -> str | None:
    """Emit one ``reasoning.signal`` event. Best-effort; returns the event id.

    ``kind`` must be ``"recall"`` or ``"capture"``. Returns ``None`` on an
    unknown kind or on any emission failure — never raises into the caller.
    """
    if kind not in _VALID_KINDS:
        logger.warning("[ReasoningSignal] unknown kind %r; skipping", kind)
        return None

    from AINDY.core.execution_signal_helper import queue_system_event
    from AINDY.core.system_event_types import SystemEventTypes

    body: dict[str, Any] = {"kind": kind, **(payload or {})}
    try:
        return queue_system_event(
            db=db,
            event_type=SystemEventTypes.REASONING_SIGNAL,
            user_id=user_id,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
            source=source,
            payload=body,
            required=False,
        )
    except Exception as exc:  # reasoning observability must never break the loop
        logger.warning("[ReasoningSignal] emit failed: %s", exc)
        return None
