from __future__ import annotations

import threading
from collections import deque
from typing import Callable

from AINDY.kernel.scheduler.common import (
    PRIORITY_NORMAL,
    PRIORITY_ORDER,
    ScheduledItem,
    logger,
)


def _distributed_execution_mode() -> bool:
    import os

    return os.getenv("EXECUTION_MODE", "").strip().lower() == "distributed"


def _count_forwarded_resume() -> None:
    try:
        from AINDY.platform_layer.metrics import scheduler_resume_forwarded_total

        scheduler_resume_forwarded_total.inc()
    except Exception:  # noqa: BLE001 — observability never decides
        pass


class SchedulerCoreMixin:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, deque[ScheduledItem]] = {p: deque() for p in PRIORITY_ORDER}
        self._rr_cursor: dict[str, str | None] = {p: None for p in PRIORITY_ORDER}
        self._waiting: dict[str, dict] = {}
        self._seq = 0
        self._total_enqueued = 0
        self._total_dispatched = 0
        self._total_dropped = 0
        self._last_stale_wait_check_monotonic = 0.0
        self._rehydration_complete = threading.Event()
        self._pre_rehydration_buffer: list[tuple[str, str | None]] = []

    def get_metrics_snapshot(self) -> dict:
        with self._lock:
            return {
                "queue_depth": {p: len(self._queues[p]) for p in PRIORITY_ORDER},
                "waiting_count": len(self._waiting),
            }

    def mark_rehydration_complete(self) -> None:
        self._rehydration_complete.set()
        with self._lock:
            buffered = list(self._pre_rehydration_buffer)
            self._pre_rehydration_buffer.clear()

        for event_type, correlation_id, run_id in buffered:
            logger.info("[Scheduler] replaying buffered event post-rehydration: %s", event_type)
            try:
                self.notify_event(
                    event_type, correlation_id=correlation_id, run_id=run_id, broadcast=False
                )
            except Exception:
                logger.warning(
                    "[Scheduler] buffered event replay failed event=%s corr=%s",
                    event_type,
                    correlation_id,
                    exc_info=True,
                )

    def is_rehydrated(self) -> bool:
        return self._rehydration_complete.is_set()

    def _enqueue_resume(
        self,
        run_id: str,
        callback: Callable[[], None],
        entry: dict,
    ) -> None:
        item = ScheduledItem(
            execution_unit_id=str(entry.get("eu_id") or ""),
            tenant_id=str(entry.get("tenant_id") or "system"),
            priority=entry.get("priority") or PRIORITY_NORMAL,
            run_callback=callback,
            run_id=run_id,
            eu_type=entry.get("eu_type", "flow"),
        )
        # FR-15 silent loss #5 — an item enqueued on a process with no heartbeat is an item
        # lost. Under distributed mode a woken resume is forwarded NOW through the dispatcher
        # (the async hint routes it to the durable queue for a worker process); a leader with a
        # running heartbeat keeps the queue, which its own `schedule()` drains as before.
        if _distributed_execution_mode() and not self._local_drainer_running():
            logger.info(
                "[Scheduler] no local drainer (follower process); forwarding woken resume run=%s to the dispatcher",
                run_id,
            )
            _count_forwarded_resume()
            try:
                self._dispatch_item_now(item)
                return
            except Exception as exc:  # noqa: BLE001 — never lose it silently: fall back to the queue and SAY so
                logger.error(
                    "[Scheduler] forwarding woken resume run=%s failed (%s); queued locally where "
                    "no heartbeat drains it",
                    run_id, exc,
                )
        self.enqueue(item)

    def _unregister_redis_wait(self, run_id: str) -> None:
        try:
            from AINDY.kernel.redis_wait_registry import RedisWaitRegistry
            from AINDY.kernel.event_bus import get_redis_client

            RedisWaitRegistry(get_redis_client()).unregister(str(run_id))
        except Exception:
            logger.debug("[Scheduler] Redis wait unregister skipped for run=%s", run_id, exc_info=True)

    def enqueue(self, item: ScheduledItem) -> None:
        import time as _time

        with self._lock:
            self._seq += 1
            item.enqueued_at_seq = self._seq
            # FR-15 — monotonic, not wall clock: this is a duration measurement and must
            # not be corrupted by an NTP step. Read under the lock so it cannot be
            # attributed to the wrong queue position.
            item.enqueued_at_monotonic = _time.monotonic()
            self._queues[item.priority].append(item)
            self._total_enqueued += 1
            depth = sum(len(q) for q in self._queues.values())
            logger.debug(
                "[Scheduler] enqueued eu=%s tenant=%s priority=%s seq=%d depth=%d",
                item.execution_unit_id,
                item.tenant_id,
                item.priority,
                self._seq,
                depth,
            )

        # FR-15 — emitted OUTSIDE the lock, deliberately. This opens a DB session, and
        # holding the scheduler lock across a database write would serialise every
        # enqueue behind it — the same class of defect this signal exists to make
        # visible. Best-effort; never raises into the enqueue path.
        from AINDY.core.scheduler_queue_signal import emit_scheduler_queued

        emit_scheduler_queued(
            execution_unit_id=item.execution_unit_id,
            tenant_id=item.tenant_id,
            priority=item.priority,
            eu_type=item.eu_type,
            queue_depth=depth,
            run_id=item.run_id,
        )

    def dequeue_next(self) -> ScheduledItem | None:
        with self._lock:
            for priority in PRIORITY_ORDER:
                q = self._queues[priority]
                if not q:
                    continue
                last_tenant = self._rr_cursor[priority]
                if last_tenant is not None and len(q) > 1 and q[0].tenant_id == last_tenant:
                    q.rotate(-1)
                item = q.popleft()
                self._rr_cursor[priority] = item.tenant_id
                self._total_dispatched += 1
                return item
        return None

    def queue_depth(self) -> dict[str, int]:
        with self._lock:
            return {p: len(q) for p, q in self._queues.items()}

    def stats(self) -> dict:
        with self._lock:
            return {
                "queues": {p: len(q) for p, q in self._queues.items()},
                "waiting": len(self._waiting),
                "total_enqueued": self._total_enqueued,
                "total_dispatched": self._total_dispatched,
                "total_dropped": self._total_dropped,
            }

    def reset(self) -> None:
        with self._lock:
            cleared_run_ids = list(self._waiting.keys())
            for q in self._queues.values():
                q.clear()
            self._waiting.clear()
            self._rr_cursor = {p: None for p in PRIORITY_ORDER}
            self._seq = 0
            self._total_enqueued = 0
            self._total_dispatched = 0
            self._total_dropped = 0
            self._last_stale_wait_check_monotonic = 0.0
            self._pre_rehydration_buffer.clear()
            self._rehydration_complete.clear()
        for run_id in cleared_run_ids:
            self._unregister_redis_wait(run_id)
