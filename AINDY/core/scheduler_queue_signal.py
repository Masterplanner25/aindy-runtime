"""FR-15 — make the wait to enter the execution pipeline visible.

Nothing was emitted between an item entering the scheduler queue and
``execution.started`` firing once it was actually claimed. The app team measured a
**177-second** window of that silence, during which a queued request and a hung process
are externally identical — which is what turned a diagnosis into a three-hour
investigation.

This module emits one ``scheduler.queued`` SystemEvent when an item enters the queue,
carrying the queue depth it landed behind. The complementary *duration* signal is a
Prometheus histogram observed at dispatch (``aindy_scheduler_queue_wait_seconds``) —
events answer *"is it queued or hung?"*, the histogram answers *"how long do things
wait?"*, and neither is the right tool for the other question.

Three constraints shape this, each of which the repo has already paid for once:

1. **Named ``scheduler.``, not ``execution.``.** The execution-contract gate in
   ``system_event_service`` raises for any ``execution.*`` event emitted outside a
   pipeline, and the two hottest enqueue callers — the event-bus subscriber thread and
   wait expiry — have no pipeline active. The app team asked for ``execution.queued``;
   that exact name would raise in the paths that matter most.

2. **``skip_memory_capture=True``, non-negotiable.** RT-MEMTXN-LEAK-1's rule is that a
   memory capture must never enqueue work whose own lifecycle events are capturable —
   *any* capture → job → capture edge is a cycle. This event fires **on the enqueue path
   itself**, so capturing it would close exactly that loop.

3. **Its own short-lived session, opened and closed around the write.** The scheduler
   holds no session, and the callers here are background threads. RT-MEMTXN-LEAK-1 also
   established that a request-shared session must never be borrowed across this kind of
   boundary.

Best-effort throughout: observability must never break the path it observes, and this
path is the one already under load when the signal matters most.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

#: Off switch. The event is one row per enqueued item — the same order of magnitude as
#: the ``execution.started`` row that already exists per item — but an operator drowning
#: in a queue storm should be able to stop the writes without a redeploy.
_ENV_FLAG = "AINDY_SCHEDULER_QUEUE_EVENTS"


def scheduler_queue_events_enabled() -> bool:
    """True unless explicitly disabled. Resolved per call, never cached at import.

    Import-time env reads are invisible to behavioural tests — the standing rule in
    CLAUDE.md, learned from FR-10, ``ResourceManager._get_backend()`` and the
    ``AINDY_REDIS_URL`` alias.
    """
    return os.getenv(_ENV_FLAG, "true").strip().lower() not in {"0", "false", "no"}


def emit_scheduler_queued(
    *,
    execution_unit_id: str,
    tenant_id: str,
    priority: str,
    eu_type: str,
    queue_depth: int,
    run_id: str | None = None,
) -> None:
    """Record that an execution unit entered the scheduler queue.

    Never raises. Returns nothing — no caller should branch on whether the signal
    landed, because the work must proceed either way.
    """
    if not scheduler_queue_events_enabled():
        return

    payload: dict[str, Any] = {
        "execution_unit_id": str(execution_unit_id or ""),
        "tenant_id": str(tenant_id or ""),
        "priority": str(priority or ""),
        "eu_type": str(eu_type or ""),
        # The depth this item landed behind — the number that distinguishes "queued
        # behind 40 things" from "queued alone and the dispatcher is wedged". Those have
        # the same external symptom and completely different causes.
        "queue_depth": int(queue_depth),
        "run_id": str(run_id) if run_id else None,
    }

    db = None
    try:
        from AINDY.core.system_event_service import emit_system_event
        from AINDY.core.system_event_types import SystemEventTypes
        from AINDY.db.database import SessionLocal

        db = SessionLocal()
        emit_system_event(
            db=db,
            event_type=SystemEventTypes.SCHEDULER_QUEUED,
            payload=payload,
            source="scheduler",
            required=False,
            # See constraint 2 in the module docstring — this is the cycle guard.
            skip_memory_capture=True,
        )
        db.commit()
    except Exception as exc:
        logger.warning("[SchedulerQueueSignal] emit failed eu=%s: %s", execution_unit_id, exc)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # pragma: no cover - defensive
                logger.debug("[SchedulerQueueSignal] session close failed", exc_info=True)


def observe_queue_wait(seconds: float, *, priority: str) -> None:
    """Record how long a dispatched item spent in the queue. Never raises."""
    try:
        from AINDY.platform_layer.metrics import scheduler_queue_wait_seconds

        scheduler_queue_wait_seconds.labels(priority=str(priority or "")).observe(max(0.0, float(seconds)))
    except Exception:  # pragma: no cover - metrics must never break dispatch
        logger.debug("[SchedulerQueueSignal] wait observation skipped", exc_info=True)
