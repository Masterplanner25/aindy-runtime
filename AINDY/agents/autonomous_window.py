"""Runtime-driven autonomous execute-window (RTR-5).

Today the runtime only *evaluates* an autonomous trigger (via the app-registered
evaluator) and then queues/defers/ignores it — nothing in the runtime turns an
``"execute"`` decision into a real planned+executed run. `run_execute_window`
closes that gap: a **bounded** trigger→plan→execute loop that composes the
existing primitives (`evaluate_live_trigger` → `create_run` → `execute_run`)
under guardrails, keeping the decision *policy* app-owned (the evaluator) while
the runtime owns the controlled execution window.

Opt-in and default-off (`AINDY_AUTONOMOUS_EXECUTE_WINDOW`): when disabled the
window is a no-op and the existing evaluate/defer/queue behavior is unchanged.
Bounds: max iterations, an active-run admission cap (`count_active_executions`),
and an optional inter-iteration cooldown. Human approval is respected — a run
that comes back ``pending_approval`` ends the window rather than being
force-executed.

Registered as async job ``agent.autonomous_window`` so the queue/defer path can
dispatch it; also directly callable as a runtime primitive.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from AINDY.platform_layer.async_job_service import register_async_job

logger = logging.getLogger(__name__)

AUTONOMOUS_WINDOW_JOB_NAME = "agent.autonomous_window"
_COOLDOWN_CAP_SECONDS = 30
_OBJECTIVE_PREVIEW = 200


def _window_settings() -> tuple[bool, int, int, int]:
    from AINDY.config import settings

    return (
        bool(getattr(settings, "AINDY_AUTONOMOUS_EXECUTE_WINDOW", False)),
        max(1, int(getattr(settings, "AINDY_AUTONOMOUS_MAX_ITERATIONS", 3))),
        max(0, int(getattr(settings, "AINDY_AUTONOMOUS_MAX_ACTIVE_RUNS", 1))),
        max(0, int(getattr(settings, "AINDY_AUTONOMOUS_COOLDOWN_SECONDS", 0))),
    )


def _emit_window_event(db, phase: str, *, user_id, trace_id, payload: dict[str, Any]) -> None:
    try:
        from AINDY.core.execution_signal_helper import queue_system_event
        from AINDY.core.system_event_types import SystemEventTypes

        queue_system_event(
            db=db,
            event_type=SystemEventTypes.AUTONOMY_WINDOW,
            user_id=user_id,
            trace_id=trace_id,
            source="autonomy",
            payload={"phase": phase, **payload},
            required=False,
        )
    except Exception as exc:  # observability must not break the window
        logger.debug("[AutonomousWindow] window event emit skipped: %s", exc)


def run_execute_window(
    db,
    *,
    user_id,
    objective: str,
    trigger: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    trace_id: str | None = None,
    max_iterations: int | None = None,
    max_active: int | None = None,
    cooldown_seconds: int | None = None,
) -> dict[str, Any]:
    """Run a bounded autonomous trigger→plan→execute window.

    Returns a summary ``{enabled, iterations: [...], stop_reason, count}``. A no-op
    (``enabled: False``) unless ``AINDY_AUTONOMOUS_EXECUTE_WINDOW`` is set.
    """
    enabled, cfg_iter, cfg_active, cfg_cooldown = _window_settings()
    if not enabled:
        return {
            "enabled": False,
            "reason": "AINDY_AUTONOMOUS_EXECUTE_WINDOW disabled",
            "iterations": [],
            "count": 0,
        }

    if not objective:
        return {"enabled": True, "iterations": [], "count": 0, "stop_reason": "no_objective"}

    max_iterations = cfg_iter if max_iterations is None else max(1, int(max_iterations))
    max_active = cfg_active if max_active is None else max(0, int(max_active))
    cooldown = cfg_cooldown if cooldown_seconds is None else max(0, int(cooldown_seconds))
    trigger = dict(trigger or {"trigger_type": "system"})
    ctx = dict(context or {})

    from AINDY.agents.agent_runtime import create_run, execute_run
    from AINDY.agents.autonomous_controller import (
        count_active_executions,
        evaluate_live_trigger,
        record_decision,
    )

    iterations: list[dict[str, Any]] = []
    stop_reason = "max_iterations"
    _emit_window_event(
        db,
        "started",
        user_id=user_id,
        trace_id=trace_id,
        payload={"objective": str(objective)[:_OBJECTIVE_PREVIEW], "max_iterations": max_iterations},
    )

    for i in range(max_iterations):
        # Admission: don't pile onto an already-busy tenant.
        if max_active and count_active_executions(db, user_id=user_id) >= max_active:
            stop_reason = "active_run_cap"
            break

        # Policy stays app-owned: the evaluator decides execute/defer/ignore.
        evaluation = evaluate_live_trigger(db=db, trigger=trigger, user_id=user_id, context=ctx)
        record_decision(
            db=db, trigger=trigger, evaluation=evaluation, user_id=user_id,
            trace_id=trace_id, context=ctx,
        )
        decision = str(evaluation.get("decision") or "defer")
        if decision != "execute":
            iterations.append({"iteration": i, "decision": decision, "reason": evaluation.get("reason")})
            stop_reason = decision
            break

        run = create_run(objective=objective, user_id=user_id, db=db)
        if not run:
            iterations.append({"iteration": i, "error": "create_run_failed"})
            stop_reason = "create_failed"
            break

        run_id = run.get("run_id")
        status = run.get("status")
        if status == "pending_approval":
            # Respect human approval — the window does not force high-risk work.
            iterations.append({"iteration": i, "run_id": run_id, "status": "pending_approval"})
            stop_reason = "approval_required"
            break
        if status == "approved":
            result = execute_run(run_id=run_id, user_id=str(user_id), db=db) or {}
            status = result.get("status", status)

        iterations.append({"iteration": i, "run_id": run_id, "status": status})
        if status in ("failed", "verify_failed", "cancelled"):
            stop_reason = f"run_{status}"
            break

        # Feed the outcome forward so the next evaluation can decide to stop.
        ctx = dict(ctx)
        ctx["last_run"] = {"run_id": run_id, "status": status}
        if cooldown and i < max_iterations - 1:
            time.sleep(min(cooldown, _COOLDOWN_CAP_SECONDS))

    summary = {
        "enabled": True,
        "iterations": iterations,
        "count": len(iterations),
        "stop_reason": stop_reason,
    }
    _emit_window_event(
        db,
        "completed",
        user_id=user_id,
        trace_id=trace_id,
        payload={"stop_reason": stop_reason, "iterations": len(iterations)},
    )
    return summary


@register_async_job(AUTONOMOUS_WINDOW_JOB_NAME)
def _autonomous_window_job(payload: dict[str, Any], db):
    """Async-job adapter — dispatch an execute-window from the queue/defer path."""
    return run_execute_window(
        db,
        user_id=payload.get("user_id"),
        objective=payload.get("objective") or "",
        trigger=payload.get("trigger"),
        context=payload.get("context"),
        trace_id=payload.get("trace_id"),
    )
