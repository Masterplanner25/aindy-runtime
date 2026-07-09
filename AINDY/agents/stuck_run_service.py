"""
StuckRunService — Sprint N+7 Agent Observability Phase 1

Detects and recovers FlowRun rows that are stranded in status="running"
after a process crash or unclean shutdown.

scan_and_recover_stuck_runs()
  └─ Query FlowRun.status="running" older than threshold
       ├─ workflow_type="agent_execution"
       │    Mark FlowRun + linked AgentRun as failed
       │    Populate AgentRun.result from completed AgentStep rows
       └─ all other types
            Mark FlowRun as failed (log only — no linked model to update)

The function never raises — startup must not be blocked by recovery errors.
Each stuck run is wrapped in its own try/except so one bad row cannot abort
the rest of the scan.

Configuration
=============
STUCK_RUN_THRESHOLD_MINUTES
  Runs whose FlowRun.updated_at is older than this many minutes are
  considered stuck. Exposed as a function parameter so tests and callers
  can override it explicitly.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
from AINDY.core.execution_signal_helper import record_agent_event
from AINDY.core.observability_events import emit_observability_event
from AINDY.kernel.condition_codes import (
    AgentRunStatus,
    FlowRunStatus,
    is_agent_terminal,
    is_flow_terminal,
)

_RECOVERY_ERROR_MSG = "Stuck run recovery: process terminated before completion"

# Non-waiting active FlowRun states a stale row can be stranded in (RTR-3).
_STUCK_FLOW_STATUSES = (FlowRunStatus.RUNNING.value, FlowRunStatus.EXECUTING.value)


def _recovery_error_detail(*, detected_at: datetime) -> dict[str, str]:
    return {
        "reason": "stuck_run_recovered",
        "detected_at": detected_at.isoformat(),
    }


def _default_threshold_minutes() -> int:
    from AINDY.config import settings

    return settings.STUCK_RUN_THRESHOLD_MINUTES


# ── Agent-execution recovery ──────────────────────────────────────────────────

def _recover_agent_run(flow_run, db: Session) -> None:
    """
    Recover one agent_execution FlowRun.

    Finds the linked AgentRun by flow_run_id, loads all completed
    AgentStep audit rows, then marks both FlowRun and AgentRun as failed.
    """
    from AINDY.db.models import AgentRun, AgentStep

    recovered_at = datetime.now(timezone.utc)
    # Mark the FlowRun terminal
    flow_run.status = FlowRunStatus.FAILED.value
    flow_run.waiting_for = None
    flow_run.wait_deadline = None
    flow_run.error_message = _RECOVERY_ERROR_MSG
    flow_run.error_detail = _recovery_error_detail(detected_at=recovered_at)
    flow_run.completed_at = recovered_at

    # Find the linked AgentRun
    agent_run = (
        db.query(AgentRun)
        .filter(AgentRun.flow_run_id == str(flow_run.id))
        .first()
    )
    if not agent_run:
        logger.warning(
            "[StuckRunService] No AgentRun linked to FlowRun %s — FlowRun marked failed only",
            flow_run.id,
        )
        return

    # RTR-3: only a genuinely terminal AgentRun is left alone. The historical
    # ``!= "executing"`` guard silently no-op'd recovery for runs parked in
    # ``delegated`` / ``waiting`` (VM WAIT), stranding them after the FlowRun
    # was already failed above. Recover any non-terminal linked run.
    if is_agent_terminal(agent_run.status):
        # Already finalised by another path; nothing to do
        return

    # Reconstruct result from whatever AgentStep rows were committed
    completed_steps = (
        db.query(AgentStep)
        .filter(AgentStep.run_id == agent_run.id)
        .order_by(AgentStep.step_index.asc())
        .all()
    )
    step_results = [
        {
            "step_index": s.step_index,
            "tool": s.tool_name,
            "status": s.status,
            "result": s.result,
            "error": s.error_message,
        }
        for s in completed_steps
    ]

    agent_run.status = AgentRunStatus.FAILED.value
    agent_run.completed_at = recovered_at
    agent_run.error_message = _RECOVERY_ERROR_MSG
    agent_run.result = {"steps": step_results}

    logger.warning(
        "[StuckRunService] Recovered AgentRun %s (flow_run=%s, %d steps committed)",
        agent_run.id,
        flow_run.id,
        len(step_results),
    )


# ── Generic recovery ──────────────────────────────────────────────────────────

def _recover_generic_run(flow_run, db: Session) -> None:
    """Mark a non-agent FlowRun as failed — log only, no linked model."""
    recovered_at = datetime.now(timezone.utc)
    flow_run.status = FlowRunStatus.FAILED.value
    flow_run.waiting_for = None
    flow_run.wait_deadline = None
    flow_run.error_message = _RECOVERY_ERROR_MSG
    flow_run.error_detail = _recovery_error_detail(detected_at=recovered_at)
    flow_run.completed_at = recovered_at
    logger.warning(
        "[StuckRunService] Recovered generic FlowRun %s (type=%s)",
        flow_run.id,
        flow_run.workflow_type,
    )


# ── Public entry point ────────────────────────────────────────────────────────

def recover_stuck_agent_run(
    run_id: str,
    user_id: str,
    db: Session,
    force: bool = False,
) -> dict:
    """
    Manually recover a single stuck AgentRun.

    Returns a result dict:
      {"ok": True,  "run": <run_dict>}
      {"ok": False, "error_code": "not_found"}
      {"ok": False, "error_code": "forbidden"}
      {"ok": False, "error_code": "wrong_status",
                    "detail": "Run is already in a terminal state"}
      {"ok": False, "error_code": "too_recent",
                    "detail": "Run started less than N minutes ago (use ?force=true to override)"}

    Callers map error_code to the appropriate HTTP status:
      not_found   → 404
      forbidden   → 403
      wrong_status / too_recent → 409
    """
    from AINDY.db.models import AgentRun, AgentStep
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.agents.agent_runtime import run_to_dict

    threshold_minutes = _default_threshold_minutes()

    try:
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if not run:
            return {"ok": False, "error_code": "not_found"}

        if run.user_id != user_id:
            return {"ok": False, "error_code": "forbidden"}

        # RTR-3: reject only a genuinely terminal run; any non-terminal state
        # (executing / delegated / waiting) is a recoverable stuck run.
        if is_agent_terminal(run.status):
            return {
                "ok": False,
                "error_code": "wrong_status",
                "detail": "Run is already in a terminal state",
            }

        if not force and run.started_at:
            age = datetime.now(timezone.utc) - run.started_at
            if age < timedelta(minutes=threshold_minutes):
                remaining = threshold_minutes - int(age.total_seconds() / 60)
                return {
                    "ok": False,
                    "error_code": "too_recent",
                    "detail": (
                        f"Run started less than {threshold_minutes} minutes ago "
                        f"(use ?force=true to override)"
                    ),
                }

        # Mark linked FlowRun failed if present
        if run.flow_run_id:
            flow_run = (
                db.query(FlowRun)
                .filter(FlowRun.id == run.flow_run_id)
                .first()
            )
            if flow_run and not is_flow_terminal(flow_run.status):
                recovered_at = datetime.now(timezone.utc)
                flow_run.status = FlowRunStatus.FAILED.value
                flow_run.waiting_for = None
                flow_run.wait_deadline = None
                flow_run.error_message = _RECOVERY_ERROR_MSG
                flow_run.error_detail = _recovery_error_detail(detected_at=recovered_at)
                flow_run.completed_at = recovered_at

        # Reconstruct result from committed AgentStep rows
        completed_steps = (
            db.query(AgentStep)
            .filter(AgentStep.run_id == run.id)
            .order_by(AgentStep.step_index.asc())
            .all()
        )
        step_results = [
            {
                "step_index": s.step_index,
                "tool": s.tool_name,
                "status": s.status,
                "result": s.result,
                "error": s.error_message,
            }
            for s in completed_steps
        ]

        run.status = AgentRunStatus.FAILED.value
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = _RECOVERY_ERROR_MSG
        run.result = {"steps": step_results}
        db.commit()

        logger.warning(
            "[StuckRunService] Manual recovery: AgentRun %s marked failed (%d steps)",
            run_id,
            len(step_results),
        )

        # Emit RECOVERED lifecycle event
        record_agent_event(
            run_id=str(run.id),
            user_id=run.user_id,
            event_type="RECOVERED",
            db=db,
            correlation_id=getattr(run, "correlation_id", None),
            payload={"recovered_at": run.completed_at.isoformat() if run.completed_at else None},
        )

        return {"ok": True, "run": run_to_dict(run)}

    except Exception as exc:
        logger.error(
            "[StuckRunService] recover_stuck_agent_run failed for %s: %s", run_id, exc
        )
        try:
            db.rollback()
        except Exception as rollback_exc:
            emit_observability_event(
                logger,
                event="stuck_agent_recovery_rollback_failed",
                run_id=run_id,
                error=str(rollback_exc),
            )
        return {"ok": False, "error_code": "internal_error", "detail": str(exc)}


def scan_and_recover_stuck_runs(
    db: Session,
    staleness_minutes: int | None = None,
    *,
    include_wait_timeouts: bool = False,
    return_stats: bool = False,
    continue_stranded: bool = False,
) -> int | dict[str, int]:
    """
    Scan for stuck FlowRun rows and mark them failed.

    A run is considered stuck when:
      - status in ("running", "executing")
      - updated_at < now() - staleness_minutes

    ``continue_stranded`` (ECOGAP-1): when True — set only by the startup caller,
    where no live runners exist — a continuation-safe flow is re-driven from its
    last-committed node instead of failed. The periodic watchdog leaves it False
    (failing a possibly-live run is safer than double-driving it).

    Returns counters for recovered, dead-lettered, and continued runs.
    Never raises — all exceptions are caught internally.
    """
    if staleness_minutes is None:
        staleness_minutes = _default_threshold_minutes()

    recovered = 0
    dead_lettered = 0
    continued = 0

    try:
        from AINDY.db.models.flow_run import FlowRun
        from AINDY.config import settings
        from AINDY.core.flow_continuation import try_continue_flow_run
        from AINDY.agents.dead_letter_service import move_to_dead_letter

        now = datetime.now(timezone.utc)
        threshold_dt = now - timedelta(minutes=staleness_minutes)
        timeout_threshold = now - timedelta(minutes=settings.FLOW_WAIT_TIMEOUT_MINUTES)

        # RTR-3: both non-waiting active states are recoverable. ``executing`` is
        # written by the resume claim; a row stale there (updated_at is bumped on
        # every node checkpoint) is a crash mid-step, not a live run.
        stuck_runs = (
            db.query(FlowRun)
            .filter(
                FlowRun.status.in_(_STUCK_FLOW_STATUSES),
                FlowRun.updated_at < threshold_dt,
            )
            .all()
        )
        waiting_runs = []
        if include_wait_timeouts:
            waiting_runs = (
                db.query(FlowRun)
                .filter(
                    FlowRun.status == FlowRunStatus.WAITING.value,
                    FlowRun.updated_at < timeout_threshold,
                )
                .all()
            )

        if not stuck_runs and not waiting_runs:
            logger.info(
                "[StuckRunService] Startup scan: no stuck or timed-out waiting runs "
                "(stuck_threshold=%dm wait_timeout=%dm)",
                staleness_minutes,
                settings.FLOW_WAIT_TIMEOUT_MINUTES,
            )
            return {"recovered": 0, "dead_lettered": 0, "continued": 0} if return_stats else 0

        logger.warning(
            "[StuckRunService] Startup scan: found %d stuck run(s) and %d timed-out waiting run(s) "
            "(stuck_threshold=%dm wait_timeout=%dm)",
            len(stuck_runs),
            len(waiting_runs),
            staleness_minutes,
            settings.FLOW_WAIT_TIMEOUT_MINUTES,
        )

        for flow_run in stuck_runs:
            try:
                # ECOGAP-1: try transparent crash continuation before failing —
                # ONLY at startup (continue_stranded=True). Continuing a run
                # mid-operation would risk double-driving a hung-but-alive runner;
                # at startup no live runners exist. Handled (continued or
                # dead-lettered) → skip the failure path.
                if continue_stranded and try_continue_flow_run(flow_run, db):
                    continued += 1
                    continue

                if flow_run.workflow_type == "agent_execution":
                    _recover_agent_run(flow_run, db)
                else:
                    _recover_generic_run(flow_run, db)

                db.commit()
                recovered += 1

            except Exception as exc:
                logger.error(
                    "[StuckRunService] Failed to recover FlowRun %s: %s",
                    flow_run.id,
                    exc,
                )
                try:
                    db.rollback()
                except Exception as rollback_exc:
                    emit_observability_event(
                        logger,
                        event="stuck_run_scan_rollback_failed",
                        flow_run_id=str(flow_run.id),
                        error=str(rollback_exc),
                    )

        if include_wait_timeouts:
            for flow_run in waiting_runs:
                try:
                    reason = f"wait_timeout:{settings.FLOW_WAIT_TIMEOUT_MINUTES}m"
                    moved = move_to_dead_letter(db, str(flow_run.id), reason=reason)
                    if moved:
                        dead_lettered += 1
                except Exception as exc:
                    logger.error(
                        "[StuckRunService] Failed to dead-letter waiting FlowRun %s: %s",
                        flow_run.id,
                        exc,
                    )
                    try:
                        db.rollback()
                    except Exception as rollback_exc:
                        emit_observability_event(
                            event_type="stuck_run_scan_rollback_failed",
                            payload={
                                "flow_run_id": str(flow_run.id),
                                "error": str(rollback_exc),
                            },
                        )

    except Exception as exc:
        logger.error(
            "[StuckRunService] Startup scan aborted with unexpected error: %s", exc
        )

    if continued:
        logger.info("[StuckRunService] Crash-continued %d stranded flow run(s)", continued)

    if return_stats:
        return {"recovered": recovered, "dead_lettered": dead_lettered, "continued": continued}
    return recovered

