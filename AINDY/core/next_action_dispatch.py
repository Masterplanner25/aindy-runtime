"""Act on a post-execution NextAction decision (INFINITY-RUNTIME-1, Deliverable C).

Gap 4 shipped *record-first*: after an agent run finishes, ``_emit_agent_next_action``
emits ``NEXT_ACTION_CHOSEN`` but takes no action — the app orchestrator consumes the
event and decides. This module adds the **bounded, opt-in acting half**: when an app
completion hook explicitly returns a ``trigger_execution`` decision with a follow-up
objective, dispatch ONE follow-up run.

Design (verified-scope, altitude "direct bounded follow-up"):

- **Opt-in, default-off** (``AINDY_NEXT_ACTION_ACTING``). When off this is a pure no-op.
- **Only ``trigger_execution``.** ``retry`` / ``schedule_follow_up`` stay record-only
  (they touch RetryPolicy / scheduler semantics — deferred).
- **Never the runtime's own idea.** ``trigger_execution`` is never a runtime-default
  verb (`default_next_action` only emits done/retry/escalate/recommend), and we
  additionally require an app-sourced decision (``source != "runtime_default"``). So
  acting is gated twice: the flag *and* an explicit app decision.
- **Reuses the existing rails.** The follow-up goes through ``create_run`` — so the
  approval gate is structurally preserved (a high-risk / untrusted plan returns
  ``pending_approval`` and is NOT executed here), capability-token preflight applies,
  and admission is bounded by ``count_active_executions``.
- **One net-new rail: a chain-depth cap.** The window's max-iterations bounds a single
  window; it does not bound a NextAction→run→NextAction→run chain. Each follow-up is
  linked via ``parent_run_id``; depth is the number of ``parent_run_id`` hops, and a
  run already ``AINDY_NEXT_ACTION_MAX_CHAIN`` deep will not dispatch another.
- **Non-blocking.** The follow-up is enqueued as the ``agent.next_action_followup``
  async job rather than run inline in the completing run's daemon thread (which would
  chain depth-first and stack). It uses the plain ``submit_async_job`` path — NOT
  ``submit_autonomous_async_job`` — because the app decision *is* the execute
  decision; re-gating through ``evaluate_live_trigger`` would redundantly (and
  silently, when no evaluator is registered) nullify it.

Agent runs only — the async-job and flow paths have no NextAction emit seam.
All functions here are best-effort and never raise into the completion path.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

FOLLOWUP_JOB_NAME = "agent.next_action_followup"
_OBJECTIVE_PREVIEW = 200


def _acting_settings() -> tuple[bool, int, int]:
    """(enabled, max_chain, max_active) resolved from settings with safe floors."""
    from AINDY.config import settings

    return (
        bool(getattr(settings, "AINDY_NEXT_ACTION_ACTING", False)),
        max(1, int(getattr(settings, "AINDY_NEXT_ACTION_MAX_CHAIN", 3))),
        max(0, int(getattr(settings, "AINDY_NEXT_ACTION_MAX_ACTIVE", 1))),
    )


def _completing_run_depth(run, db, *, cap: int) -> int:
    """Number of ``parent_run_id`` hops from ``run`` to its root, bounded at ``cap``.

    A root/user-initiated run has depth 0. Walks defensively — cycle-guarded and
    hop-bounded so a malformed parent chain can never loop.
    """
    from AINDY.db.models import AgentRun

    depth = 0
    seen: set[Any] = set()
    parent_id = getattr(run, "parent_run_id", None)
    while parent_id is not None and depth <= cap:
        if parent_id in seen:  # cycle guard
            break
        seen.add(parent_id)
        depth += 1
        parent = db.query(AgentRun).filter(AgentRun.id == parent_id).first()
        if parent is None:
            break
        parent_id = getattr(parent, "parent_run_id", None)
    return depth


def maybe_act_on_next_action(run, next_action: dict[str, Any] | None, *, db, user_id) -> bool:
    """Dispatch a bounded follow-up run when the decision is an app ``trigger_execution``.

    Returns True if a follow-up was enqueued, else False. Best-effort — logs and
    returns False on any refusal or error; never raises into the completion path.
    """
    if not next_action:
        return False

    enabled, max_chain, max_active = _acting_settings()
    if not enabled:
        return False

    from AINDY.core.next_action import TRIGGER_EXECUTION

    if next_action.get("action") != TRIGGER_EXECUTION:
        return False

    # Belt-and-suspenders: never act on a runtime-invented decision. (trigger_execution
    # is never a runtime default, but we require an explicit non-default source.)
    if str(next_action.get("source") or "") == "runtime_default":
        return False

    objective = str((next_action.get("args") or {}).get("objective") or "").strip()
    if not objective:
        logger.info("[NextActionAct] trigger_execution with no objective — record-only")
        return False

    run_id = getattr(run, "id", None)
    try:
        depth = _completing_run_depth(run, db, cap=max_chain)
    except Exception as exc:  # depth walk must not break completion
        logger.warning("[NextActionAct] chain-depth walk failed run=%s: %s", run_id, exc)
        return False
    if depth >= max_chain:
        logger.info(
            "[NextActionAct] chain depth %d >= cap %d — declining follow-up (run=%s)",
            depth,
            max_chain,
            run_id,
        )
        return False

    try:
        from AINDY.agents.autonomous_controller import count_active_executions

        if max_active and count_active_executions(db, user_id=user_id) >= max_active:
            logger.info("[NextActionAct] active-run cap %d reached — declining (run=%s)", max_active, run_id)
            return False
    except Exception as exc:
        logger.warning("[NextActionAct] admission check failed run=%s: %s", run_id, exc)
        return False

    try:
        from AINDY.platform_layer.async_job_service import submit_async_job

        submit_async_job(
            task_name=FOLLOWUP_JOB_NAME,
            payload={
                "objective": objective,
                "user_id": str(user_id) if user_id is not None else None,
                "parent_run_id": str(run_id) if run_id is not None else None,
                "chain_depth": depth + 1,
                "trace_id": getattr(run, "trace_id", None),
            },
            user_id=user_id,
            source="next_action",
        )
        logger.info(
            "[NextActionAct] follow-up enqueued (parent=%s depth=%d→%d objective=%r)",
            run_id,
            depth,
            depth + 1,
            objective[:_OBJECTIVE_PREVIEW],
        )
        return True
    except Exception as exc:  # enqueue failure must not break completion
        logger.warning("[NextActionAct] follow-up enqueue failed run=%s: %s", run_id, exc)
        return False


def _link_parent(db, *, run_id: str, parent_run_id: str | None) -> None:
    """Set ``parent_run_id`` on the freshly-created follow-up run (schema-free chain link).

    ``create_run`` does not forward arbitrary columns, so the linkage is applied here.
    Best-effort — a missing link only means the *next* dispatch sees a shorter chain.
    """
    if not parent_run_id:
        return
    try:
        from AINDY.db.models import AgentRun

        row = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if row is not None and getattr(row, "parent_run_id", None) is None:
            row.parent_run_id = parent_run_id
            db.commit()
    except Exception as exc:
        logger.warning("[NextActionAct] parent link failed run=%s: %s", run_id, exc)
        try:
            db.rollback()
        except Exception:
            pass


def _register_followup_job():
    """Register the async job lazily to avoid an import cycle at module import time."""
    from AINDY.platform_layer.async_job_service import register_async_job

    @register_async_job(FOLLOWUP_JOB_NAME)
    def _next_action_followup_job(payload: dict[str, Any], db):
        """Create (and, if auto-approved, execute) one NextAction follow-up run.

        Respects the approval gate: a run that comes back ``pending_approval`` is
        left for a human — this job never force-approves or force-executes it.
        """
        objective = payload.get("objective") or ""
        user_id = payload.get("user_id")
        parent_run_id = payload.get("parent_run_id")
        if not objective:
            return {"error": "no_objective"}

        from AINDY.agents.agent_runtime import create_run, execute_run

        run = create_run(objective=objective, user_id=user_id, db=db)
        if not run:
            return {"error": "create_run_failed", "parent_run_id": parent_run_id}

        run_id = run.get("run_id")
        status = run.get("status")
        _link_parent(db, run_id=run_id, parent_run_id=parent_run_id)

        if status == "approved":
            result = execute_run(run_id=run_id, user_id=str(user_id), db=db) or {}
            status = result.get("status", status)

        return {"run_id": run_id, "status": status, "parent_run_id": parent_run_id}

    return _next_action_followup_job


_register_followup_job()
