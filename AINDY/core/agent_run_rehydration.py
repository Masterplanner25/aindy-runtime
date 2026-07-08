"""Cross-restart rehydration of WAITING agent runs (RTR-1 Phase 2e durability).

The VM-backed agent path parks a run at ``status="waiting"`` on a mid-plan WAIT
step and registers an in-memory scheduler wait whose callback resumes the next
segment (see ``nodus_execution_service._register_agent_segment_wait``). That
in-memory registration is lost on a process restart. This module re-registers
those waits at startup from durable state on the ``AgentRun`` row — the analog of
``flow_run_rehydration.rehydrate_waiting_flow_runs`` for agent runs.

Everything needed to rebuild the resume is durable:
  * ``AgentRun.plan``            → segments (``split_agent_plan``)
  * ``AgentRun.result["steps"]`` → accumulated step results (completed segments)
  * ``AgentRun.wait_state``      → {event_type, correlation_key, resume_segment_index}
  * ``AgentRun.capability_token``→ the self-verifying scoped token (reloaded)
  * ``AgentRun.correlation_id`` / ``trace_id`` / ``user_id``

The reconstructed callback carries only plain values and does an atomic
``waiting → executing`` claim, so a duplicate rehydration / event-fire / watchdog
trigger cannot resume a run twice.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def rehydrate_waiting_agent_runs(
    db: Session,
    run_ids: Optional[Iterable[str]] = None,
) -> int:
    """Re-register scheduler waits for every ``AgentRun`` in ``status="waiting"``.

    Returns the number of runs re-registered. Never raises for a single bad run —
    per-run failures are logged and skipped so one stranded row can't block boot.
    """
    from AINDY.db.models import AgentRun
    from AINDY.kernel.scheduler_engine import get_scheduler_engine
    from AINDY.runtime.agent_plan_compiler import split_agent_plan
    from AINDY.runtime.nodus_execution_service import (
        _build_agent_resume_callback,
        _register_agent_wait,
    )

    from AINDY.kernel.condition_codes import AgentRunStatus

    scheduler = get_scheduler_engine()
    query = db.query(AgentRun).filter(AgentRun.status == AgentRunStatus.WAITING.value)
    scoped = {str(r) for r in (run_ids or []) if r}
    if scoped:
        query = query.filter(AgentRun.id.in_(scoped))
    waiting_runs = query.all()

    registered = 0
    for run in waiting_runs:
        run_id = str(run.id)
        try:
            # Guard: a live registration already survived (or a prior rehydration ran).
            if scheduler.waiting_for(run_id) is not None:
                continue

            wait_state = run.wait_state or {}
            event_type = wait_state.get("event_type")
            if not event_type:
                logger.warning(
                    "[AgentRunRehydration] run %s is waiting but has no wait_state.event_type; skipping",
                    run_id,
                )
                continue

            try:
                segments = split_agent_plan(run.plan or {})
            except ValueError:
                logger.warning(
                    "[AgentRunRehydration] run %s has no splittable plan; skipping", run_id
                )
                continue

            next_segment_index = int(wait_state.get("resume_segment_index") or 0)
            if next_segment_index >= len(segments):
                logger.warning(
                    "[AgentRunRehydration] run %s resume_segment_index %d out of range (%d segments); skipping",
                    run_id, next_segment_index, len(segments),
                )
                continue

            total_tool_steps = sum(len(s["tool_steps"]) for s in segments)
            accumulated = list((run.result or {}).get("steps") or [])
            # Use run.correlation_id as the base (not trace_id) so the re-registered
            # wait's effective correlation matches the live-path registration and the
            # resume route: effective = wait_state.correlation_key or run.correlation_id.
            correlation_id = run.correlation_id

            callback = _build_agent_resume_callback(
                run_id=run_id,
                segments=segments,
                next_segment_index=next_segment_index,
                accumulated=accumulated,
                user_id=str(run.user_id),
                correlation_id=correlation_id,
                scoped_token=run.capability_token,  # durable self-verifying token
                total_tool_steps=total_tool_steps,
            )
            _register_agent_wait(
                run_id=run_id,
                event_type=event_type,
                correlation_key=wait_state.get("correlation_key"),
                user_id=str(run.user_id),
                correlation_id=correlation_id,
                resume_callback=callback,
            )
            registered += 1
        except Exception as exc:
            logger.warning("[AgentRunRehydration] rehydration failed for run %s: %s", run_id, exc)

    if registered:
        logger.info("[AgentRunRehydration] re-registered %d waiting agent run(s)", registered)
    return registered
