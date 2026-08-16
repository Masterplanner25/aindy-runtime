"""
Scheduler Service â€” APScheduler + tenacity

Replaces daemon thread background job execution with supervised,
retryable, auditable job execution.

Architecture:
- BackgroundScheduler: runs jobs in background threads managed by
  APScheduler (not raw daemon threads)
- tenacity: automatic retry with exponential backoff
- JobLog: every execution is recorded (started_at, status, result)
- Replay: any failed job can be retried via the automation router API

Lifecycle:
- start() called in main.py lifespan on startup
- stop() called in main.py lifespan on shutdown
- Never call directly from routes; use scheduled job registration or run_job_now()
"""
import logging
from datetime import datetime, timezone
import threading
from typing import Callable, Optional

from AINDY.db.models.job_log import JobLog
from AINDY.platform_layer.registry import get_scheduled_jobs

try:
    from apscheduler.executors.pool import ThreadPoolExecutor as _APSThreadPoolExecutor
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:  # pragma: no cover - optional dependency
    _APSThreadPoolExecutor = None  # type: ignore[assignment]
    class _FallbackJob:
        def __init__(self, *, func, trigger, id, name, replace_existing):
            self.func = func
            self.trigger = trigger
            self.id = id
            self.name = name
            self.replace_existing = replace_existing

    class BackgroundScheduler:  # type: ignore[no-redef]
        def __init__(self, job_defaults=None, executors=None):
            self.job_defaults = job_defaults or {}
            self.executors = executors or {}
            self.running = False
            self._jobs = []

        def add_job(self, func, trigger=None, id=None, name=None, replace_existing=False, **kwargs):
            if replace_existing and id is not None:
                self._jobs = [job for job in self._jobs if job.id != id]
            self._jobs.append(
                _FallbackJob(
                    func=func,
                    trigger=trigger,
                    id=id,
                    name=name,
                    replace_existing=replace_existing,
                )
            )

        def get_jobs(self):
            return list(self._jobs)

        def start(self):
            self.running = True

        def shutdown(self, wait=True):
            self.running = False

    class CronTrigger:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class IntervalTrigger:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.kwargs = kwargs
try:
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        before_sleep_log,
    )
except ImportError:  # pragma: no cover - optional dependency
    def retry(*args, **kwargs):
        def decorator(fn):
            return fn

        return decorator

    def stop_after_attempt(attempts):
        return attempts

    def wait_exponential(**kwargs):
        return kwargs

    def before_sleep_log(*args, **kwargs):
        return None

logger = logging.getLogger(__name__)

APScheduler_AVAILABLE = True
_STALE_WAIT_CLEANUP_COUNTER = 0

EFFECT_RECORD_TTL_DAYS = 90
EFFECT_RECORD_CLEANUP_INTERVAL_HOURS = 24
EFFECT_RECORD_DELETE_BATCH_SIZE = 10_000

# AgentRun rows in 'approved' status older than this threshold had their
# background execution thread die before committing 'executing'.
ORPHANED_APPROVED_THRESHOLD_MINUTES = 10

# Global scheduler instance â€” initialized once on startup
_scheduler: Optional[BackgroundScheduler] = None

# Job function registry for replay.
# Legacy task APIs still use these stored JobLog task_name values.
_TASK_REGISTRY: dict[str, Callable] = {}


# â”€â”€ Public lifecycle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def get_scheduler() -> BackgroundScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        raise RuntimeError(
            "Scheduler not started. Call scheduler_service.start() first."
        )
    return _scheduler


#: SYSMAX-5 — jobs whose value PEAKS when the scheduler is saturated, so they must not queue
#: behind ordinary cleanup. `queue_backend_reconnect` is the sharpest: if the queue backend is
#: down *and* the pool is saturated, the job that would reconnect it cannot run.
RECOVERY_EXECUTOR = "recovery"


def _install_starvation_listener(scheduler) -> None:
    """Count job runs skipped because the scheduler was saturated (SYSMAX-5).

    APScheduler reports this only as a per-job log line — *"maximum number of running instances
    reached"* — which is exactly what the FR-15 incident printed once per starved second while
    nobody could see it as a signal. `EVENT_JOB_MAX_INSTANCES` means the previous run is still
    going; `EVENT_JOB_MISSED` means the run time passed without a worker. Both are starvation,
    and the distinction is worth a label because they have different causes.

    Best-effort: a metrics failure must never prevent the scheduler from starting.
    """
    try:
        from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
    except Exception:  # pragma: no cover - the vendored test shim has no events module
        logger.debug("[Scheduler] starvation listener unavailable (no apscheduler.events)")
        return

    reasons = {EVENT_JOB_MAX_INSTANCES: "max_instances", EVENT_JOB_MISSED: "missed"}

    def _on_starved(event) -> None:
        reason = reasons.get(getattr(event, "code", None), "unknown")
        job_id = str(getattr(event, "job_id", "") or "unknown")
        try:
            from AINDY.platform_layer.metrics import scheduler_job_starved_total

            scheduler_job_starved_total.labels(job_id=job_id, reason=reason).inc()
        except Exception:  # pragma: no cover - observability must not break the scheduler
            logger.debug("[Scheduler] starvation metric skipped", exc_info=True)
        # Logged at WARNING deliberately: a starved *recovery* job means the runtime cannot
        # clean up after the condition that starved it, which is worth waking someone for.
        logger.warning(
            "[Scheduler] job '%s' skipped a run (%s) — the scheduler is saturated", job_id, reason
        )

    try:
        scheduler.add_listener(_on_starved, EVENT_JOB_MAX_INSTANCES | EVENT_JOB_MISSED)
    except Exception:  # pragma: no cover - shim schedulers may not implement listeners
        logger.debug("[Scheduler] could not register starvation listener", exc_info=True)


def start() -> None:
    """
    Start the APScheduler background scheduler.
    Called from main.py lifespan on startup.
    Replaces threading.Thread(daemon=True) pattern.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running â€” start() called twice")
        return

    # FR-15 (b) + SYSMAX-5 — three lanes, sized against the DB pool rather than the job count.
    #
    # `max_instances` is per-job, but the thread *pool* is shared. A job competing for one of
    # ten shared workers has a probabilistic guarantee, not a real one — and two holders here
    # are unbounded: `scheduler_heartbeat_tick` occupies a worker for the whole duration of an
    # INLINE execution (~13 minutes in the FR-15 incident), and DB-heavy jobs can block for
    # `DB_POOL_TIMEOUT` (60s) when the connection pool is exhausted. So the lanes below are
    # about *isolation*, not capacity.
    #
    # ★ The instinct is to raise `default` until it exceeds the number of jobs. That is the
    # wrong fix and would trade one exhaustion for a worse one: `DB_POOL_SIZE` (10) +
    # `DB_MAX_OVERFLOW` (20) = **30 connections total, shared with request handling**. Twenty
    # concurrent scheduler jobs each holding a session would leave ten for the API — which is
    # precisely the shape RT-MEMTXN-LEAK-1 traced when a login took 42 seconds.
    #
    # The real defect was never the total; it was that a few unbounded holders could starve
    # everything else. `scheduler_heartbeat_tick` holds a worker for the whole duration of an
    # INLINE execution (~13 minutes in the FR-15 incident), and DB-heavy jobs can block for
    # `DB_POOL_TIMEOUT` (60s). Dedicated lanes fix that structurally at +3 threads, where
    # doubling `default` would not fix it at all — it would only raise the threshold.
    #
    #   default   (10)  ordinary maintenance and every app-registered job
    #   recovery  (2)   jobs whose value PEAKS when the scheduler is saturated
    #   waits     (1)   time-wait firing (FR-15 (b)) — a correctness guarantee
    _executors = None
    if _APSThreadPoolExecutor is not None:
        _executors = {
            "default": _APSThreadPoolExecutor(10),
            "recovery": _APSThreadPoolExecutor(2),
            "waits": _APSThreadPoolExecutor(1),
        }

    _scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce": True,        # Don't stack missed runs
            "max_instances": 1,      # One instance of each job at a time
            "misfire_grace_time": 60,  # 60s grace for missed scheduled runs
        },
        executors=_executors,
    )

    _install_starvation_listener(_scheduler)
    _register_system_jobs(_scheduler)
    _scheduler.start()
    logger.info("APScheduler started â€” daemon threads replaced")


def stop(*, timeout_seconds: float | None = None) -> None:
    """
    Stop the scheduler gracefully.
    Called from main.py lifespan on shutdown.
    """
    global _scheduler
    if _scheduler and _scheduler.running:
        if timeout_seconds is None:
            _scheduler.shutdown(wait=True)
        else:
            shutdown_error: list[Exception] = []

            def _shutdown() -> None:
                try:
                    _scheduler.shutdown(wait=True)
                except Exception as exc:
                    shutdown_error.append(exc)

            thread = threading.Thread(target=_shutdown, name="apscheduler-shutdown", daemon=True)
            thread.start()
            thread.join(timeout=max(0.0, float(timeout_seconds)))
            if thread.is_alive():
                logger.warning("APScheduler shutdown exceeded timeout; proceeding with shutdown")
                try:
                    _scheduler.shutdown(wait=False)
                except Exception:
                    pass
            elif shutdown_error:
                raise shutdown_error[0]
        logger.info("APScheduler stopped")
    _scheduler = None


# â”€â”€ System jobs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _register_system_jobs(scheduler: BackgroundScheduler) -> None:
    """Register recurring platform jobs and app-registered scheduled jobs."""
    scheduler.add_job(
        _scheduler_heartbeat_tick,
        trigger=IntervalTrigger(seconds=1),
        id="scheduler_heartbeat_tick",
        name="Scheduler heartbeat tick",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    # FR-15 (b) — wait firing runs on its OWN job, not as a prelude to dispatch.
    # ``max_instances`` is per-job, so a dispatch tick blocked inside a slow INLINE
    # execution no longer stops parked flows from waking on their timers. Keeping these
    # on one job made an unrelated busy flow able to hold a timer shut indefinitely.
    scheduler.add_job(
        _scheduler_wait_tick,
        trigger=IntervalTrigger(seconds=1),
        id="scheduler_wait_tick",
        name="Scheduler wait tick",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        # Dedicated executor — see the comment in start(). Without this the job still
        # competes for one of ten shared workers against 15 other jobs.
        executor="waits",
    )

    scheduler.add_job(
        _scrape_scheduler_metrics,
        trigger=IntervalTrigger(seconds=15),
        id="scrape_scheduler_metrics",
        name="Prometheus scheduler gauge scrape",
        replace_existing=True,
    )

    scheduler.add_job(
        _cleanup_stale_logs,
        trigger=IntervalTrigger(hours=1),
        id="cleanup_stale_logs",
        name="Cleanup stale automation logs",
        replace_existing=True,
    )

    scheduler.add_job(
        _cleanup_expired_effect_records,
        trigger=IntervalTrigger(hours=EFFECT_RECORD_CLEANUP_INTERVAL_HOURS),
        id="effect_record_cleanup",
        name="EffectRecord TTL cleanup",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        _process_deferred_async_jobs,
        trigger=IntervalTrigger(minutes=1),
        id="deferred_async_job_retry",
        executor="recovery",  # SYSMAX-5
        name="Deferred async job retry",
        replace_existing=True,
    )

    scheduler.add_job(
        _check_queue_backend_health,
        trigger=IntervalTrigger(seconds=60),
        id="queue_backend_reconnect",
        executor="recovery",  # SYSMAX-5
        name="Queue backend reconnect",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        _process_queue_delayed_jobs,
        trigger=IntervalTrigger(seconds=30),
        id="queue_maintenance_process_delayed",
        name="Queue delayed-job promotion",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        _expire_timed_out_waits,
        trigger=IntervalTrigger(minutes=5),
        id="expire_timed_out_waits",
        executor="recovery",  # SYSMAX-5
        name="Expire timed-out flow waits",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        _expire_timed_out_wait_flows,
        trigger=IntervalTrigger(seconds=60),
        id="expire_timed_out_wait_flows",
        executor="recovery",  # SYSMAX-5
        name="Expire timed-out WaitingFlowRun waits",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        _recover_stuck_flow_runs,
        trigger=IntervalTrigger(minutes=5),
        id="recover_stuck_flow_runs",
        executor="recovery",  # SYSMAX-5
        name="Recover stuck flow runs",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        _process_pending_memory_embeddings,
        trigger=IntervalTrigger(minutes=1),
        id="process_pending_memory_embeddings",
        name="Process pending memory embeddings",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        _recover_orphaned_approved_runs,
        trigger=IntervalTrigger(minutes=5),
        id="recover_orphaned_approved_runs",
        executor="recovery",  # SYSMAX-5
        name="Recover orphaned approved agent runs",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    for job in get_scheduled_jobs():
        scheduler.add_job(
            job["handler"],
            trigger=_build_trigger(job.get("trigger", "interval"), job.get("trigger_kwargs") or {}),
            id=job["id"],
            name=job.get("name") or job["id"],
            replace_existing=bool(job.get("replace_existing", True)),
        )

    try:
        from AINDY.runtime.nodus_schedule_service import restore_nodus_scheduled_jobs
        restore_nodus_scheduled_jobs()
    except Exception as _nodus_restore_exc:
        logger.warning(
            "Nodus scheduled job restore failed (non-fatal): %s",
            _nodus_restore_exc,
        )


def _build_trigger(trigger_type: str, trigger_kwargs: dict) -> object:
    if trigger_type == "cron":
        return CronTrigger(**trigger_kwargs)
    if trigger_type == "interval":
        return IntervalTrigger(**trigger_kwargs)
    raise ValueError(f"Unsupported scheduled job trigger: {trigger_type}")


def _scrape_scheduler_metrics() -> None:
    """Update Prometheus scheduler gauges from the live SchedulerEngine snapshot."""
    try:
        from AINDY.kernel.scheduler_engine import get_scheduler_engine
        from AINDY.platform_layer.metrics import scheduler_queue_depth, scheduler_waiting_count
        snapshot = get_scheduler_engine().get_metrics_snapshot()
        for priority, depth in snapshot["queue_depth"].items():
            scheduler_queue_depth.labels(priority=priority).set(depth)
        scheduler_waiting_count.set(snapshot["waiting_count"])
    except Exception as exc:
        logger.warning("Scheduler metrics scrape failed (non-fatal): %s", exc)


def _should_run_stale_wait_cleanup() -> bool:
    global _STALE_WAIT_CLEANUP_COUNTER
    _STALE_WAIT_CLEANUP_COUNTER += 1
    if _STALE_WAIT_CLEANUP_COUNTER >= 60:
        _STALE_WAIT_CLEANUP_COUNTER = 0
        return True
    return False


def _scheduler_heartbeat_tick() -> None:
    """Drive scheduler dispatch only.

    FR-15 (b) — wait maintenance moved to ``_scheduler_wait_tick`` on its own job. This
    tick can block for as long as a single INLINE execution takes (dispatch is INLINE by
    default; see FR-15), and with ``max_instances=1`` a blocked tick means the next one is
    skipped. Anything time-sensitive sharing this job inherits that stall, which is why
    wait firing no longer does.
    """
    try:
        from AINDY.kernel.scheduler_engine import get_scheduler_engine

        # tick_waits=False: the dedicated wait job owns that now. Passing True here would
        # reinstate the coupling this split exists to remove.
        get_scheduler_engine().schedule(tick_waits=False)
    except Exception as exc:
        logger.warning("Scheduler heartbeat tick failed: %s", exc)


def _scheduler_wait_tick() -> None:
    """Fire due time-waits and run amortized stale-wait cleanup.

    FR-15 (b) — deliberately a *separate* APScheduler job from dispatch. Both are
    ``max_instances=1``, but that limit is per-job, so a dispatch tick blocked inside a
    slow execution no longer prevents a parked flow from waking on its timer.

    Concurrency with ``schedule()`` is safe by construction: ``tick_time_waits`` claims a
    due wait by removing it from ``_waiting`` under the engine lock and fires it only after
    releasing, so a wait cannot be fired twice.
    """
    try:
        from AINDY.kernel.scheduler_engine import get_scheduler_engine

        engine = get_scheduler_engine()
        engine.tick_waits()
        if _should_run_stale_wait_cleanup():
            engine.cleanup_stale_waits()
    except Exception as exc:
        logger.warning("Scheduler wait tick failed: %s", exc)


def _cleanup_stale_logs() -> None:
    """Clean up JobLog entries stuck in 'pending' for > 1 hour."""
    try:
        from AINDY.db.database import SessionLocal
        from datetime import timedelta

        db = SessionLocal()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        stale = (
            db.query(JobLog)
            .filter(
                JobLog.status == "pending",
                JobLog.created_at < cutoff,
            )
            .all()
        )
        for log in stale:
            log.status = "failed"
            log.error_message = "Stale: never started within 1 hour of creation"
        db.commit()
        db.close()
        if stale:
            logger.info("Cleaned up %d stale automation logs", len(stale))
    except Exception as exc:
        logger.warning("Stale log cleanup failed: %s", exc)


def _cleanup_expired_effect_records() -> None:
    """Delete finalized EffectRecord rows older than EFFECT_RECORD_TTL_DAYS. Pending rows excluded."""
    try:
        from AINDY.db.database import SessionLocal
        from AINDY.db.models.effect_record import EffectRecord
        from sqlalchemy import func, text
        from datetime import timedelta

        t_start = datetime.now(timezone.utc)
        cutoff = t_start - timedelta(days=EFFECT_RECORD_TTL_DAYS)
        stale_pending_cutoff = t_start - timedelta(hours=1)

        db = SessionLocal()

        total_count = db.query(func.count(EffectRecord.id)).scalar()
        pending_count = (
            db.query(func.count(EffectRecord.id))
            .filter(EffectRecord.status == "pending")
            .scalar()
        )
        eligible_count = (
            db.query(func.count(EffectRecord.id))
            .filter(
                EffectRecord.completed_at.isnot(None),
                EffectRecord.completed_at < cutoff,
                EffectRecord.status != "pending",
            )
            .scalar()
        )
        logger.info(
            "[effect_record_cleanup] scan: total=%d pending=%d eligible=%d",
            total_count,
            pending_count,
            eligible_count,
        )

        stale_pending = (
            db.query(func.count(EffectRecord.id))
            .filter(
                EffectRecord.status == "pending",
                EffectRecord.created_at < stale_pending_cutoff,
            )
            .scalar()
        )
        if stale_pending:
            logger.warning(
                "[effect_record_cleanup] %d pending EffectRecord row(s) older than 1 hour"
                " — may indicate stuck handlers; investigate action_ids",
                stale_pending,
            )

        total_deleted = 0
        while True:
            result = db.execute(
                text("""
                    DELETE FROM effect_records
                    WHERE id IN (
                        SELECT id FROM effect_records
                        WHERE completed_at IS NOT NULL
                          AND completed_at < :cutoff
                          AND status != 'pending'
                        ORDER BY completed_at
                        LIMIT :batch_size
                    )
                """),
                {"cutoff": cutoff, "batch_size": EFFECT_RECORD_DELETE_BATCH_SIZE},
            )
            batch_count = result.rowcount
            db.commit()
            total_deleted += batch_count
            if batch_count < EFFECT_RECORD_DELETE_BATCH_SIZE:
                break

        elapsed_ms = int((datetime.now(timezone.utc) - t_start).total_seconds() * 1000)
        logger.info(
            "[effect_record_cleanup] done: deleted=%d elapsed_ms=%d",
            total_deleted,
            elapsed_ms,
        )
        db.close()
    except Exception as exc:
        logger.error("[effect_record_cleanup] failed: %s", exc)



def _recover_orphaned_approved_runs() -> None:
    """Re-dispatch execute_run for AgentRun rows stranded in 'approved' after a process crash."""
    try:
        from AINDY.db.database import SessionLocal
        from AINDY.db.models import AgentRun
        from AINDY.kernel.condition_codes import AgentRunStatus
        from datetime import timedelta
        import threading

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=ORPHANED_APPROVED_THRESHOLD_MINUTES)
        db = SessionLocal()
        orphaned = [
            (run.id, run.user_id)
            for run in db.query(AgentRun)
            .filter(
                AgentRun.status == AgentRunStatus.APPROVED.value,
                AgentRun.approved_at < cutoff,
            )
            .limit(50)
            .all()
        ]
        db.close()

        if not orphaned:
            return

        logger.warning(
            "[agent_watchdog] %d orphaned approved run(s) found (approved_at < %s); re-dispatching",
            len(orphaned),
            cutoff.isoformat(),
        )

        for run_id, user_id in orphaned:
            def _bg_recover(run_id=run_id, user_id=user_id):
                try:
                    from AINDY.db.database import SessionLocal
                    from AINDY.agents.agent_runtime import execute_run
                    bg_db = SessionLocal()
                    try:
                        execute_run(run_id=run_id, user_id=str(user_id), db=bg_db)
                    finally:
                        bg_db.close()
                except Exception as exc:
                    logger.warning(
                        "[agent_watchdog] re-dispatch failed for run_id=%s: %s", run_id, exc
                    )

            threading.Thread(target=_bg_recover, daemon=True).start()
    except Exception as exc:
        logger.error("[agent_watchdog] failed: %s", exc)


# Job execution

def _process_deferred_async_jobs() -> None:
    try:
        from AINDY.platform_layer.async_job_service import process_deferred_jobs

        resumed = process_deferred_jobs()
        if resumed:
            logger.info("Deferred async jobs resumed: %d", resumed)
    except Exception as exc:
        logger.warning("Deferred async job processing failed: %s", exc)


def _check_queue_backend_health() -> None:
    try:
        from AINDY.core.distributed_queue import attempt_queue_backend_reconnect

        if attempt_queue_backend_reconnect():
            logger.info("Distributed queue backend recovered to Redis")
    except Exception as exc:
        logger.warning("Queue backend health check failed: %s", exc)


def _process_queue_delayed_jobs() -> None:
    try:
        from AINDY.core.distributed_queue import InMemoryQueueBackend, get_queue

        queue_backend = get_queue()
        if isinstance(queue_backend, InMemoryQueueBackend):
            return
        promoted = int(queue_backend.process_delayed_jobs())
        if promoted > 0:
            logger.debug("Queue delayed-job promotion moved %d job(s)", promoted)
    except Exception as exc:
        logger.warning("Queue delayed-job promotion failed: %s", exc)


def _expire_timed_out_waits() -> None:
    try:
        from AINDY.platform_layer.recovery_jobs import run_expire_timed_out_waits_job

        run_expire_timed_out_waits_job()
    except Exception as exc:
        logger.warning("Timed-out WAIT recovery dispatch failed: %s", exc)


def _expire_timed_out_wait_flows() -> None:
    try:
        from AINDY.platform_layer.recovery_jobs import run_expire_timed_out_wait_flows_job

        run_expire_timed_out_wait_flows_job()
    except Exception as exc:
        logger.warning("Timed-out WaitingFlowRun recovery dispatch failed: %s", exc)


def _recover_stuck_flow_runs() -> None:
    try:
        from AINDY.platform_layer.recovery_jobs import run_recover_stuck_runs_job

        run_recover_stuck_runs_job()
    except Exception as exc:
        logger.warning("Periodic stuck-run recovery dispatch failed: %s", exc)


def _process_pending_memory_embeddings() -> None:
    try:
        from AINDY.memory.embedding_jobs import process_pending_embeddings

        result = process_pending_embeddings()
        processed = int(result.get("processed", 0))
        if processed:
            logger.info(
                "Pending memory embeddings processed=%s completed=%s deferred=%s",
                processed,
                result.get("completed", 0),
                result.get("deferred", 0),
            )
    except Exception as exc:
        logger.warning("Pending memory embedding sweep failed: %s", exc)


def run_task_now(
    task_fn: Callable,
    task_name: str,
    payload: dict = None,
    user_id: str = None,
    max_attempts: int = 3,
    source: str = "manual",
) -> str:
    """
    Run a job immediately in a supervised APScheduler thread.

    Creates an JobLog entry, schedules the job for immediate
    execution, and returns the log ID for tracking.

    Replaces:
        thread = threading.Thread(target=fn, daemon=True)
        thread.start()

    With:
        run_job_now(fn, "operation_name", payload)
    """
    from AINDY.db.database import SessionLocal

    db = SessionLocal()
    log = JobLog(
        source=source,
        task_name=task_name,
        payload=payload or {},
        status="pending",
        max_attempts=max_attempts,
        user_id=user_id,
    )
    db.add(log)
    db.commit()
    log_id = log.id
    db.close()

    scheduler = get_scheduler()
    scheduler.add_job(
        _supervised_execute,
        args=[log_id, task_fn, payload or {}],
        id=f"task_{log_id}",
        name=task_name,
        replace_existing=True,
    )

    return log_id


def _supervised_execute(log_id: str, task_fn: Callable, payload: dict) -> None:
    """
    Execute a job function with tenacity retry, updating the JobLog.

    This is the core replacement for daemon threads. Every execution is:
    - Logged (started_at, completed_at, attempt_count)
    - Retried on failure with exponential backoff (tenacity)
    - Auditable (status + error_message stored in JobLog)
    """
    from AINDY.db.database import SessionLocal

    db = SessionLocal()
    log = db.query(JobLog).filter(JobLog.id == log_id).first()

    if not log:
        logger.error("JobLog %s not found â€” cannot execute", log_id)
        db.close()
        return

    log.status = "running"
    log.started_at = datetime.now(timezone.utc)
    db.commit()

    max_attempts = log.max_attempts

    @retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def execute_with_retry():
        log.attempt_count += 1
        if log.attempt_count > 1:
            log.status = "retrying"
        db.commit()
        return task_fn(payload)

    try:
        result = execute_with_retry()
        log.status = "success"
        log.result = result if isinstance(result, dict) else {"result": str(result)}
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "Job %s succeeded (log: %s, attempts: %d)",
            log.task_name,
            log_id,
            log.attempt_count,
        )
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.error(
            "Job %s failed after %d attempt(s): %s",
            log.task_name,
            log.attempt_count,
            exc,
        )
    finally:
        db.close()


def replay_task(log_id: str) -> bool:
    """
    Replay a failed job execution from its JobLog.

    Resets the log to pending and re-runs the original job function
    with the original payload. Only failed or retrying logs can be replayed.

    Returns True if replay was scheduled, False if log not found / not failed
    or job function not in registry.
    """
    from AINDY.db.database import SessionLocal

    db = SessionLocal()
    log = db.query(JobLog).filter(JobLog.id == log_id).first()

    if not log:
        db.close()
        return False

    if log.status not in ("failed", "retrying"):
        db.close()
        return False

    task_fn = _TASK_REGISTRY.get(log.task_name)
    if not task_fn:
        logger.warning(
            "Job function '%s' not in registry; cannot replay log %s",
            log.task_name,
            log_id,
        )
        db.close()
        return False

    # Reset for replay
    log.status = "pending"
    log.attempt_count = 0
    log.error_message = None
    log.started_at = None
    log.completed_at = None
    log_payload = log.payload
    log_task_name = log.task_name
    db.commit()
    db.close()

    scheduler = get_scheduler()
    scheduler.add_job(
        _supervised_execute,
        args=[log_id, task_fn, log_payload or {}],
        id=f"replay_{log_id}",
        name=f"replay:{log_task_name}",
        replace_existing=True,
    )

    return True


# Job registry

def register_task(name: str):
    """
    Decorator to register a job function for supervised execution and replay.

    Usage:
        @register_job_function("my_background_job")
        def my_background_job(payload: dict):
            ...
            return {"status": "done"}
    """
    def wrapper(fn: Callable) -> Callable:
        _TASK_REGISTRY[name] = fn
        return fn

    return wrapper


register_task_function = register_task
register_job_function = register_task_function
run_job_now = run_task_now
replay_job = replay_task



