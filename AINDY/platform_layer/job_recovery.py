"""Thread-mode async-job crash recovery (RTR-2 gap 3).

In ``EXECUTION_MODE=thread`` a job's ``JobLog`` row is committed ``pending`` and
its execution is handed to an in-process ``ThreadPoolExecutor`` future — nothing
about that future is durable. A process crash therefore strands the row in
``pending``/``running`` with no path back to execution: ``process_deferred_jobs``
only picks ``deferred`` rows, and the distributed path's ``requeue_stale_jobs``
covers only the Redis in-flight hash, which thread mode never populates.

This module closes that gap by re-dispatching orphaned thread-mode jobs **at
startup**. Startup is the only safe moment: the executor has no live futures yet,
so every ``pending``/``running`` ``JobLog`` is definitionally orphaned from the
dead process incarnation — re-dispatch cannot double-run a genuinely in-flight
job. (A periodic scanner can't distinguish the two: ``_ACTIVE_FUTURES`` tracks
``Future`` objects, not log ids, so a long-running job would look identical to a
crashed one.) This mirrors ``stuck_run_service``'s startup scan for FlowRuns.

No-op unless the effective execution mode is ``thread`` — the distributed path
already has worker-side stale recovery.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_MAX_RECOVER = 500
_ORPHAN_STATUSES = ("pending", "running")


def recover_orphaned_thread_jobs() -> int:
    """Re-dispatch thread-mode ``JobLog`` rows stranded by a prior crash.

    Returns the number of jobs re-dispatched. Best-effort — never raises; a
    per-row failure is logged and skipped so one bad row can't block boot.
    """
    try:
        from AINDY.config import resolve_execution_mode

        if resolve_execution_mode() != "thread":
            # Distributed mode: the worker's requeue_stale_jobs owns recovery.
            return 0

        from AINDY.db.database import SessionLocal
        from AINDY.db.models.job_log import JobLog
        from AINDY.platform_layer.async_job_service import _execute_job

        db = SessionLocal()
        recovered = 0
        try:
            orphans = (
                db.query(JobLog.id, JobLog.job_name, JobLog.payload)
                .filter(JobLog.status.in_(_ORPHAN_STATUSES))
                .limit(_MAX_RECOVER)
                .all()
            )
            for log_id, task_name, payload in orphans:
                # Light claim: only re-dispatch a row still orphaned. A row is left
                # at "pending" (a fresh execution) with started_at cleared; the
                # re-run increments attempt_count in _execute_job_inline as usual.
                claimed = (
                    db.query(JobLog)
                    .filter(
                        JobLog.id == log_id,
                        JobLog.status.in_(_ORPHAN_STATUSES),
                    )
                    .update(
                        {"status": "pending", "started_at": None},
                        synchronize_session=False,
                    )
                )
                db.commit()
                if not claimed:
                    continue

                lid, tname, pl = str(log_id), task_name, dict(payload or {})

                def _bg(lid=lid, tname=tname, pl=pl):
                    try:
                        _execute_job(lid, tname, pl)
                    except Exception as exc:
                        logger.warning(
                            "[job_recovery] re-dispatch failed for job %s: %s", lid, exc
                        )

                threading.Thread(target=_bg, daemon=True).start()
                recovered += 1

            if recovered:
                logger.warning(
                    "[job_recovery] re-dispatched %d orphaned thread-mode job(s) at startup",
                    recovered,
                )
            return recovered
        finally:
            db.close()
    except Exception as exc:
        logger.error("[job_recovery] startup recovery failed: %s", exc)
        return 0
