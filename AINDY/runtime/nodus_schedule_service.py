"""
nodus_schedule_service.py â€” Scheduled Nodus script execution.

Allows cron-scheduled execution of Nodus scripts via the APScheduler
background scheduler.  Every execution is audited via JobLog and
run through PersistentFlowRunner so the full Nodus runtime
(memory, event, WAIT/RESUME, retries) is available to scripts.

Leader election
===============
Each APScheduler callback emits a generic scheduler tick event. App-owned
handlers may veto execution, allowing multi-worker coordination without
hardcoded app job names in the runtime.

Retry handling
==============
* ``error_policy="retry"`` â†’ the ``nodus.execute`` flow node returns RETRY
  on script failure, and PersistentFlowRunner retries up to ``max_retries``
  times (exponential back-off managed by the flow engine).
* ``error_policy="fail"`` (default) â†’ script errors are recorded as
  ``last_run_status="failure"`` and the run ends immediately.
* Outer (infrastructure) exceptions (DB down, VM crash) are caught, logged,
  and recorded in the JobLog; they never propagate to APScheduler.

Persistence
===========
``create_nodus_scheduled_job()`` registers the job with APScheduler *and*
writes a ``NodusScheduledJob`` row to DB.  On server restart,
``restore_nodus_scheduled_jobs()`` reads all active rows and re-registers
them â€” call this from ``scheduler_service._register_system_jobs()``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from AINDY.db.models.job_log import JobLog

logger = logging.getLogger(__name__)

# APScheduler job ID prefix â€” makes jobs easy to identify in scheduler listings
_JOB_ID_PREFIX = "nodus_scheduled_"

# ECOGAP-5a — per-job downtime-misfire policy.
MISFIRE_SKIP = "skip"          # default: a fire due during downtime is dropped (prior behavior)
MISFIRE_RUN_ONCE = "run_once"  # dispatch ONE coalesced catch-up run at boot, then resume
_VALID_MISFIRE_POLICIES = (MISFIRE_SKIP, MISFIRE_RUN_ONCE)


# ---------------------------------------------------------------------------
# Internal job runner (APScheduler callback)
# ---------------------------------------------------------------------------

def _run_scheduled_nodus_job(job_id: str) -> None:
    """
    APScheduler callback executed on each cron tick for a Nodus scheduled job.

    Responsibilities
    ----------------
    1. Emit a scheduler tick lifecycle event; handlers may veto execution.
    2. Load ``NodusScheduledJob`` from DB; bail out if inactive.
    3. Create an ``JobLog`` entry for this execution.
    4. Run the Nodus script via ``PersistentFlowRunner(NODUS_SCRIPT_FLOW)``.
    5. Update ``JobLog`` and ``NodusScheduledJob`` with the outcome.

    All errors are caught â€” this function never raises so APScheduler does not
    disable the job due to an unhandled exception.
    """
    # â”€â”€ 1. Leader election â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        from AINDY.platform_layer.registry import emit_event

        tick_results = emit_event(
            "scheduler.tick",
            {"job_id": str(job_id), "source": "nodus_schedule"},
        )
        if any(result is False for result in tick_results):
            logger.debug("[NodusSchedule] Scheduler tick vetoed job %s", job_id)
            return
    except Exception as _le_exc:
        logger.warning("[NodusSchedule] Scheduler tick failed - skipping: %s", _le_exc)
        return
        return

    from AINDY.db.database import SessionLocal
    from AINDY.db.models.nodus_scheduled_job import NodusScheduledJob

    db = SessionLocal()
    log: Optional[JobLog] = None

    try:
        # â”€â”€ 2. Load job row â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        job = (
            db.query(NodusScheduledJob)
            .filter(NodusScheduledJob.id == job_id)
            .first()
        )
        if not job or not job.is_active:
            logger.info("[NodusSchedule] Job %s not found or inactive â€” skipping", job_id)
            return

        label = job.job_name or f"nodus_job_{job_id}"
        trace_id = str(uuid.uuid4())

        # â”€â”€ 3. Create JobLog â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        log = JobLog(
            source="nodus_schedule",
            task_name=label,
            payload={
                "job_id": str(job.id),
                "cron_expression": job.cron_expression,
                "error_policy": job.error_policy,
            },
            status="running",
            user_id=job.user_id,
            max_attempts=job.max_retries,
            trace_id=trace_id,
            started_at=datetime.now(timezone.utc),
        )
        db.add(log)
        db.commit()

        # â”€â”€ 4. Execute via the shared Nodus orchestration helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        from AINDY.runtime.nodus_execution_service import format_nodus_flow_result
        from AINDY.runtime.nodus_execution_service import run_nodus_script_via_flow

        user_id_str = str(job.user_id) if job.user_id else ""
        result = run_nodus_script_via_flow(
            script=job.script,
            input_payload=dict(job.input_payload or {}),
            error_policy=job.error_policy,
            db=db,
            user_id=user_id_str,
            workflow_type="nodus_schedule",
            trace_id=trace_id,
            node_max_retries=job.max_retries,  # honour per-job retry config
        )
        formatted_result = format_nodus_flow_result(result)

        # â”€â”€ 5. Record outcome â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        flow_succeeded = result.get("status") == "SUCCESS"
        nodus_ok = formatted_result.get("nodus_status") != "failure"

        run_status = "success" if (flow_succeeded and nodus_ok) else "failure"

        log.status = "success" if run_status == "success" else "failed"
        log.result = {
            "flow_status": formatted_result.get("status"),
            "nodus_status": formatted_result.get("nodus_status"),
            "run_id": formatted_result.get("run_id"),
            "trace_id": formatted_result.get("trace_id"),
            "events_emitted": formatted_result.get("events_emitted"),
            "memory_writes": formatted_result.get("memory_writes_count"),
            "error": formatted_result.get("error"),
            "execution_record": formatted_result.get("execution_record"),
        }
        log.completed_at = datetime.now(timezone.utc)

        job.last_run_at = log.completed_at
        job.last_run_status = run_status
        job.last_run_log_id = log.id
        db.commit()

        logger.info(
            "[NodusSchedule] Job %r (%s) completed â€” status=%s run_id=%s",
            label,
            job_id,
            run_status,
            result.get("run_id"),
        )

    except Exception as exc:
        logger.error("[NodusSchedule] Job %s raised: %s", job_id, exc)
        try:
            if log is not None:
                log.status = "failed"
                log.error_message = str(exc)
                log.completed_at = datetime.now(timezone.utc)
            # Also update job.last_run_status if we got that far
            if "job" in dir() and job is not None:
                job.last_run_at = datetime.now(timezone.utc)
                job.last_run_status = "error"
                if log is not None:
                    job.last_run_log_id = log.id
            db.commit()
        except Exception as _inner:
            logger.error("[NodusSchedule] Failed to persist error state: %s", _inner)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

def create_nodus_scheduled_job(
    *,
    db: Session,
    script: str,
    cron_expression: str,
    user_id: str,
    job_name: Optional[str] = None,
    script_name: Optional[str] = None,
    input_payload: Optional[dict] = None,
    error_policy: str = "fail",
    max_retries: int = 3,
    misfire_policy: str = MISFIRE_SKIP,
) -> dict:
    """
    Persist a new ``NodusScheduledJob`` and register it with APScheduler.

    Parameters
    ----------
    db:
        Active SQLAlchemy session (committed before APScheduler registration).
    script:
        Resolved Nodus source code (caller is responsible for resolving
        ``script_name`` â†’ content before calling this function).
    cron_expression:
        Standard 5-field cron string.  Validated via
        ``CronTrigger.from_crontab()`` before DB write.
    user_id:
        Job owner â€” used for memory scoping inside the script.
    job_name:
        Human-readable label (optional).
    script_name:
        Name of the uploaded script this job was created from (informational).
    input_payload:
        Initial ``nodus_input_payload`` dict passed to every execution.
    error_policy:
        ``"fail"`` (default) or ``"retry"``.
    max_retries:
        Maximum flow-engine retries when ``error_policy="retry"``.

    Returns
    -------
    dict
        Serialised job metadata (id, job_name, cron_expression, next_run_at, â€¦).

    Raises
    ------
    ValueError
        When ``cron_expression`` is invalid, APScheduler is not available,
        or the scheduler has not been started yet.
    """
    # Validate cron expression before touching DB
    _trigger = _parse_cron(cron_expression)

    _misfire = str(misfire_policy or MISFIRE_SKIP).strip().lower()
    if _misfire not in _VALID_MISFIRE_POLICIES:
        raise ValueError(
            f"invalid misfire_policy {misfire_policy!r}; expected one of {_VALID_MISFIRE_POLICIES}"
        )

    from AINDY.db.models.nodus_scheduled_job import NodusScheduledJob
    from AINDY.utils.uuid_utils import normalize_uuid

    uid = normalize_uuid(user_id) if user_id else None

    job_row = NodusScheduledJob(
        user_id=uid,
        job_name=job_name,
        script=script,
        script_name=script_name,
        cron_expression=cron_expression,
        input_payload=input_payload or {},
        error_policy=error_policy,
        max_retries=max_retries,
        misfire_policy=_misfire,
        is_active=True,
    )
    db.add(job_row)
    db.commit()
    db.refresh(job_row)

    job_id_str = str(job_row.id)

    # Register with APScheduler
    _register_with_scheduler(job_row, _trigger)

    logger.info(
        "[NodusSchedule] Created job %r id=%s cron=%r",
        job_name or job_id_str,
        job_id_str,
        cron_expression,
    )

    return _serialize_job(job_row, next_run_at=_next_run(_trigger))


def list_nodus_scheduled_jobs(*, db: Session, user_id: str) -> list[dict]:
    """
    Return all active scheduled Nodus jobs owned by ``user_id``.
    """
    from AINDY.db.models.nodus_scheduled_job import NodusScheduledJob
    from AINDY.utils.uuid_utils import normalize_uuid

    uid = normalize_uuid(user_id)
    rows = (
        db.query(NodusScheduledJob)
        .filter(
            NodusScheduledJob.user_id == uid,
            NodusScheduledJob.is_active.is_(True),
        )
        .order_by(NodusScheduledJob.created_at.desc())
        .all()
    )
    return [_serialize_job(r) for r in rows]


def delete_nodus_scheduled_job(
    *,
    db: Session,
    job_id: str,
    user_id: str,
) -> bool:
    """
    Soft-delete a scheduled job: set ``is_active=False`` and remove from
    APScheduler.

    Returns True on success, False if the job was not found or not owned by
    ``user_id``.
    """
    from AINDY.db.models.nodus_scheduled_job import NodusScheduledJob
    from AINDY.utils.uuid_utils import normalize_uuid

    uid = normalize_uuid(user_id)

    try:
        job_uuid = uuid.UUID(job_id)
    except (ValueError, AttributeError):
        return False

    row = (
        db.query(NodusScheduledJob)
        .filter(
            NodusScheduledJob.id == job_uuid,
            NodusScheduledJob.user_id == uid,
            NodusScheduledJob.is_active.is_(True),
        )
        .first()
    )
    if not row:
        return False

    row.is_active = False
    db.commit()

    # Remove from APScheduler (best-effort â€” may already be gone)
    _remove_from_scheduler(job_id)

    logger.info("[NodusSchedule] Deleted job %s", job_id)
    return True


def restore_nodus_scheduled_jobs() -> int:
    """
    Re-register all active ``NodusScheduledJob`` rows with APScheduler.

    Called from ``scheduler_service._register_system_jobs()`` on startup so
    schedules survive process restarts.

    Returns the number of jobs successfully restored.
    """
    from AINDY.db.database import SessionLocal
    from AINDY.db.models.nodus_scheduled_job import NodusScheduledJob

    db = SessionLocal()
    restored = 0
    try:
        rows = (
            db.query(NodusScheduledJob)
            .filter(NodusScheduledJob.is_active.is_(True))
            .all()
        )
        for row in rows:
            try:
                trigger = _parse_cron(row.cron_expression)
                _register_with_scheduler(row, trigger)
                restored += 1
                # ECOGAP-5a: if a fire was due while the process was down and this job
                # opted into run_once, dispatch one coalesced catch-up.
                _maybe_schedule_misfire_catchup(row, trigger)
            except Exception as exc:
                logger.warning(
                    "[NodusSchedule] Could not restore job %s (%r): %s",
                    row.id,
                    row.job_name,
                    exc,
                )
    except Exception as exc:
        logger.warning("[NodusSchedule] Restore scan failed: %s", exc)
    finally:
        db.close()

    if restored:
        logger.info("[NodusSchedule] Restored %d scheduled Nodus jobs", restored)
    return restored


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_cron(cron_expression: str):
    """
    Parse and validate a 5-field (UTC) cron expression into a CronTrigger.

    Prefers the real APScheduler ``CronTrigger`` — the runtime scheduler
    (``scheduler_service``) runs real APScheduler when installed, and it rejects a
    *foreign* trigger instance (``TypeError: Expected a trigger instance or string``),
    which previously left restored Nodus jobs unregistered. The real trigger also
    provides ``get_next_fire_time()`` (used for next-run reporting and ECOGAP-5a
    misfire detection), which the vendored fallback stub lacks. Falls back to the
    vendored trigger only when APScheduler is absent.

    Raises ValueError if APScheduler is unavailable or the expression is invalid.
    """
    # Import the SAME top-level ``apscheduler`` the runtime scheduler uses (scheduler_service).
    # In production that is real APScheduler; the test harness shadows it with a vendored
    # stub via pythonpath. Using the top-level name keeps the trigger type consistent with the
    # scheduler that will run it (a foreign trigger instance is rejected with a TypeError).
    try:
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        raise ValueError("APScheduler is not installed â€” cannot schedule Nodus jobs") from exc

    try:
        try:
            # Real APScheduler: pin to UTC so a "0 10 * * *" cron means 10:00 UTC regardless of
            # the host timezone.
            return CronTrigger.from_crontab(cron_expression, timezone="UTC")
        except TypeError:
            # The vendored fallback stub's from_crontab has no timezone parameter.
            return CronTrigger.from_crontab(cron_expression)
    except Exception as exc:
        raise ValueError(
            f"Invalid cron expression {cron_expression!r}: {exc}"
        ) from exc


def _next_run(trigger) -> Optional[str]:
    """Return the ISO 8601 next fire time for a trigger, or None."""
    try:
        from datetime import timezone as _tz
        next_dt = trigger.get_next_fire_time(None, datetime.now(_tz.utc))
        return next_dt.isoformat() if next_dt else None
    except Exception:
        return None


def _register_with_scheduler(job_row: Any, trigger: Any) -> None:
    """Add/replace the job in the live APScheduler instance."""
    from AINDY.platform_layer.scheduler_service import get_scheduler

    scheduler = get_scheduler()
    job_id_str = str(job_row.id)
    label = job_row.job_name or f"nodus_job_{job_id_str}"

    scheduler.add_job(
        _run_scheduled_nodus_job,
        args=[job_id_str],
        trigger=trigger,
        id=f"{_JOB_ID_PREFIX}{job_id_str}",
        name=f"Nodus: {label}",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )


def _has_missed_fire(trigger: Any, reference: Any, now: datetime) -> bool:
    """True if the cron *trigger* had a scheduled fire strictly after *reference* and at or
    before *now* — i.e. a fire was due while the process was down.

    Coalesced: we only need to know that *at least one* fire was missed (the first fire after
    the reference being <= now), not how many. Returns False if the reference is unknown or the
    trigger cannot compute fire times (the vendored fallback stub)."""
    if reference is None:
        return False
    getter = getattr(trigger, "get_next_fire_time", None)
    if getter is None:
        return False
    try:
        if getattr(reference, "tzinfo", None) is None:
            reference = reference.replace(tzinfo=timezone.utc)
        nxt = getter(reference, reference)  # first fire strictly after `reference`
    except Exception:
        return False
    return nxt is not None and nxt <= now


def _maybe_schedule_misfire_catchup(job_row: Any, trigger: Any) -> bool:
    """ECOGAP-5a: for a run_once job that missed a fire during downtime, schedule ONE
    immediate catch-up run. No-op for skip policy or when no fire was missed. Never raises."""
    policy = str(getattr(job_row, "misfire_policy", MISFIRE_SKIP) or MISFIRE_SKIP).strip().lower()
    if policy != MISFIRE_RUN_ONCE:
        return False

    now = datetime.now(timezone.utc)
    reference = job_row.last_run_at or job_row.created_at
    if not _has_missed_fire(trigger, reference, now):
        return False

    try:
        from datetime import timedelta

        from apscheduler.triggers.date import DateTrigger

        from AINDY.platform_layer.scheduler_service import get_scheduler

        scheduler = get_scheduler()
        job_id_str = str(job_row.id)
        label = job_row.job_name or f"nodus_job_{job_id_str}"
        scheduler.add_job(
            _run_scheduled_nodus_job,
            args=[job_id_str],
            trigger=DateTrigger(run_date=now + timedelta(seconds=5)),
            id=f"{_JOB_ID_PREFIX}{job_id_str}_catchup",
            name=f"Nodus catch-up: {label}",
            replace_existing=True,
            max_instances=1,
        )
        logger.warning(
            "[NodusSchedule] misfire catch-up scheduled for job %s (%r) — a fire was missed "
            "during downtime (last_run=%s)",
            job_id_str, label, reference,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[NodusSchedule] misfire catch-up could not be scheduled for job %s: %s",
            getattr(job_row, "id", "?"), exc,
        )
        return False


def _remove_from_scheduler(job_id: str) -> None:
    """Remove a job from APScheduler (best-effort â€” never raises)."""
    try:
        from AINDY.platform_layer.scheduler_service import get_scheduler
        scheduler = get_scheduler()
        aps_id = f"{_JOB_ID_PREFIX}{job_id}"
        try:
            from apscheduler.jobstores.base import JobLookupError
        except Exception:  # pragma: no cover - apscheduler absent entirely
            JobLookupError = ()  # type: ignore[assignment]

        try:
            scheduler.remove_job(aps_id)
        except JobLookupError:
            # The only benign case, and the one the old bare `except Exception` claimed to be
            # for. Everything else now surfaces.
            logger.debug("[NodusSchedule] job %s already absent from the scheduler", aps_id)
        except Exception as exc:
            # Found by the shim audit: this used to be swallowed under a comment saying
            # "Job may already be gone". An AttributeError from a renamed scheduler API — or
            # from a test shim that never implemented `remove_job` — was indistinguishable
            # from a job that was legitimately gone, so removal could be a silent no-op
            # forever while every test passed.
            logger.warning(
                "[NodusSchedule] failed to remove job %s from the scheduler: %s", aps_id, exc
            )
    except Exception:
        pass


def _serialize_job(row: Any, next_run_at: Optional[str] = None) -> dict:
    """Convert a ``NodusScheduledJob`` ORM row to a plain dict."""
    return {
        "id": str(row.id),
        "job_name": row.job_name,
        "script_name": row.script_name,
        "cron_expression": row.cron_expression,
        "error_policy": row.error_policy,
        "max_retries": row.max_retries,
        "misfire_policy": getattr(row, "misfire_policy", MISFIRE_SKIP),
        "is_active": row.is_active,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "last_run_status": row.last_run_status,
        "last_run_log_id": row.last_run_log_id,
        "next_run_at": next_run_at,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }




