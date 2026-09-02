from __future__ import annotations

from AINDY.kernel.scheduler.common import (
    MAX_PER_SCHEDULE_CYCLE,
    PRIORITY_LOW,
    ScheduledItem,
    _ResumedEUStub,
    _emit_dispatch_failure,
    logger,
)


class SchedulerDispatchMixin:
    def schedule(self, *, tick_waits: bool = True) -> int:
        """Drain up to ``MAX_PER_SCHEDULE_CYCLE`` queued items.

        ``tick_waits`` defaults to True so any direct caller keeps the historical
        behaviour (wait maintenance ran as a prelude to dispatch). The runtime's own
        scheduler passes **False** and drives ``tick_waits()`` from a separate job —
        FR-15 (b): with both on one ``max_instances=1`` tick, a slow INLINE execution
        skipped the next tick entirely and time-based waits stopped firing, so an
        unrelated busy flow could keep a parked flow parked.
        """
        import time as _time

        import AINDY.kernel.scheduler_engine as compat
        from AINDY.core.execution_dispatcher import (
            async_scheduler_dispatch_enabled,
            dispatch as _dispatch,
        )
        from AINDY.core.resume_reconstruction import RESUME_CONTEXT_KEY, resume_context

        if tick_waits:
            self._check_stale_waits()
            self.tick_time_waits()

        rm = compat.get_resource_manager()
        dispatched = 0
        saturated_tenants: set[str] = set()
        retry_items: list[ScheduledItem] = []
        processed = 0
        saturated_skips = 0

        while processed < MAX_PER_SCHEDULE_CYCLE:
            item = self.dequeue_next()
            if item is None:
                break

            if item.tenant_id in saturated_tenants:
                with self._lock:
                    self._queues[item.priority].appendleft(item)
                    self._total_dispatched -= 1
                    queue_size = sum(len(q) for q in self._queues.values())
                saturated_skips += 1
                if saturated_skips >= queue_size:
                    break
                continue

            saturated_skips = 0
            processed += 1
            ok, reason = rm.can_execute(item.tenant_id, item.execution_unit_id)
            if not ok:
                with self._lock:
                    self._queues[item.priority].appendleft(item)
                    self._total_dispatched -= 1
                saturated_tenants.add(item.tenant_id)
                logger.debug("[Scheduler] deferred eu=%s tenant=%s reason=%s", item.execution_unit_id, item.tenant_id, reason)
                continue

            # FR-15 — record how long this item sat in the queue, just before it is
            # dispatched. Observed here rather than inside dispatch() because only the
            # scheduler knows when the item was enqueued. A 0.0 stamp means the item did
            # not come through enqueue() (a dispatcher-reconstructed retry), which is
            # "unknown", not "waited zero" — so it is skipped rather than recorded as a
            # fast sample that would drag the histogram toward a flattering answer.
            if item.enqueued_at_monotonic:
                from AINDY.core.scheduler_queue_signal import observe_queue_wait

                observe_queue_wait(
                    _time.monotonic() - item.enqueued_at_monotonic, priority=item.priority
                )

            stub = _ResumedEUStub(id=item.execution_unit_id, type=item.eu_type, priority=item.priority)
            # FR-15 (a) — the scheduler asks its OWN question about async dispatch, not the
            # one two HTTP routes ask. ``async_hint`` is Rule 1 of ``_decide_mode``, which
            # bypasses ``async_heavy_execution_enabled()`` entirely; setting it only when
            # our own gate is on means that with the gate OFF this path is byte-identical
            # to the behaviour it has always had.
            #
            # ★ Everything the scheduler drains is a resume of already-admitted heavy work
            # (flow / agent / nodus), so there is no eu_type here for which INLINE is the
            # right answer once the gate is on — which is why the hint is unconditional
            # within the gate rather than re-deriving the type rules a second time.
            if async_scheduler_dispatch_enabled():
                stub.extra = {"async_hint": True}
            # FR-15 stage 2: carry a resume descriptor alongside the closure. In thread mode
            # the closure runs and this is unused; in distributed mode the closure cannot
            # cross the boundary and this is what the worker rebuilds from. Supplied
            # unconditionally because the dispatcher — not the scheduler — decides which
            # transport applies, and a descriptor that costs two strings is cheaper than a
            # scheduler that has to know.
            context = {
                "eu_id": item.execution_unit_id,
                "run_id": item.run_id,
                "source": "scheduler.resume",
            }
            if item.run_id:
                context[RESUME_CONTEXT_KEY] = resume_context(
                    run_id=item.run_id, eu_type=item.eu_type
                )
            try:
                _dispatch(stub, item.run_callback, context)
                dispatched += 1
                logger.debug("[Scheduler] dispatched eu=%s type=%s priority=%s", item.execution_unit_id, item.eu_type, item.priority)
            except Exception as exc:
                if item.retry_count < item.max_retries:
                    item.retry_count += 1
                    logger.warning(
                        "[Scheduler] dispatch failed eu=%s (attempt %d/%d), re-enqueueing: %s",
                        item.execution_unit_id,
                        item.retry_count,
                        item.max_retries + 1,
                        exc,
                    )
                    item.priority = PRIORITY_LOW
                    retry_items.append(item)
                else:
                    logger.error(
                        "[Scheduler] dispatch PERMANENTLY failed eu=%s after %d attempts: %s",
                        item.execution_unit_id,
                        item.retry_count + 1,
                        exc,
                    )
                    with self._lock:
                        self._total_dropped += 1
                    _emit_dispatch_failure(item, exc)

        for item in retry_items:
            self.enqueue(item)
        return dispatched
