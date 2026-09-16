from __future__ import annotations

from AINDY.kernel.scheduler.common import PRIORITY_NORMAL, correlation_admits, logger


def _load_wait_entry_from_db(run_id: str):
    try:
        from AINDY.db import SessionLocal
        from AINDY.db.models.waiting_flow_run import WaitingFlowRun

        with SessionLocal() as db:
            return (
                db.query(WaitingFlowRun)
                .filter(WaitingFlowRun.run_id == str(run_id))
                .first()
            )
    except Exception:
        logger.warning(
            "_load_wait_entry_from_db failed for run_id=%s",
            run_id,
            exc_info=True,
        )
        return None


def _cross_instance_resume(
    engine: "SchedulerEngine",
    event_type: str,
    correlation_id: str | None,
    skip_run_ids: set[str],
    run_id: str | None = None,
) -> int:
    try:
        from AINDY.kernel.redis_wait_registry import RedisWaitRegistry
        from AINDY.kernel.resume_spec import build_callback_from_spec
        from AINDY.kernel.event_bus import get_redis_client

        registry = RedisWaitRegistry(get_redis_client())
        if registry._redis is None:
            return 0

        resumed = 0
        target_run = str(run_id) if run_id is not None else None
        for run_id, spec in registry.get_all_specs().items():
            import AINDY.kernel.scheduler_engine as compat

            if run_id in skip_run_ids:
                continue
            # RESUME-FANOUT-UNSCOPED-1 — a per-run wake wakes that run and nothing else.
            if target_run is not None and str(run_id) != target_run:
                continue
            with engine._lock:
                if run_id in engine._waiting:
                    continue

            wait_entry = compat._load_wait_entry_from_db(run_id)
            if wait_entry is None or getattr(wait_entry, "event_type", None) != event_type:
                continue

            # WAIT-PAYLOAD-PATH-1 (b) — the SAME rule the local scan applies. This used to be
            # `if correlation_id and wait_corr != correlation_id`, which vetoed a None-correlation
            # wait on every emit (an emit always carries one), so such a wait woke locally and
            # never here. One predicate, both paths.
            wait_corr = getattr(wait_entry, "correlation_id", None)
            if not correlation_admits(wait_corr, correlation_id, run_scoped=target_run is not None):
                continue

            try:
                callback = build_callback_from_spec(spec)
            except Exception:
                logger.warning("Failed to build callback for run_id=%s", run_id, exc_info=True)
                continue

            if not registry.unregister_if_exists(run_id):
                continue

            engine._enqueue_resume(
                run_id,
                callback,
                {
                    "priority": getattr(wait_entry, "priority", PRIORITY_NORMAL) or PRIORITY_NORMAL,
                    "tenant_id": getattr(spec, "tenant_id", None) or "system",
                    "eu_id": getattr(wait_entry, "eu_id", None) or spec.eu_id,
                    "correlation_id": wait_corr,
                    "trace_id": None,
                    "eu_type": getattr(spec, "eu_type", None) or "flow",
                },
            )
            resumed += 1
            logger.info("Cross-instance resume claimed run_id=%s on this instance", run_id)

        return resumed
    except Exception:
        logger.warning("_cross_instance_resume failed for event_type=%s", event_type, exc_info=True)
        return 0


def _cross_instance_tick(engine: "SchedulerEngine") -> int:
    try:
        from datetime import datetime, timezone

        from AINDY.kernel.redis_wait_registry import RedisWaitRegistry
        from AINDY.kernel.resume_spec import build_callback_from_spec
        from AINDY.kernel.event_bus import get_redis_client

        registry = RedisWaitRegistry(get_redis_client())
        if registry._redis is None:
            return 0

        now = datetime.now(timezone.utc)
        fired = 0
        for run_id, spec in registry.get_all_specs().items():
            import AINDY.kernel.scheduler_engine as compat

            with engine._lock:
                if run_id in engine._waiting:
                    continue

            wait_entry = compat._load_wait_entry_from_db(run_id)
            if wait_entry is None:
                continue

            timeout_at = getattr(wait_entry, "timeout_at", None)
            if timeout_at is None:
                continue
            if getattr(timeout_at, "tzinfo", None) is None:
                timeout_at = timeout_at.replace(tzinfo=timezone.utc)
            if timeout_at > now or not registry.unregister_if_exists(run_id):
                continue

            try:
                callback = build_callback_from_spec(spec)
            except Exception:
                logger.warning("[Scheduler] tick: build_callback failed run_id=%s", run_id, exc_info=True)
                continue

            engine._enqueue_resume(
                run_id,
                callback,
                {
                    "eu_id": getattr(wait_entry, "eu_id", None) or run_id,
                    "tenant_id": getattr(wait_entry, "tenant_id", None)
                    or getattr(spec, "tenant_id", None)
                    or "system",
                    "priority": getattr(wait_entry, "priority", None) or PRIORITY_NORMAL,
                    "eu_type": getattr(spec, "eu_type", None) or "flow",
                },
            )
            engine._delete_wait_backup(run_id)
            logger.info("[Scheduler] cross-instance time-wait fired run_id=%s", run_id)
            fired += 1

        return fired
    except Exception:
        logger.warning("[Scheduler] cross-instance tick failed", exc_info=True)
        return 0
