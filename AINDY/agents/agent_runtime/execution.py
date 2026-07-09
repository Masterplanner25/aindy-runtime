from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from AINDY.agents.agent_coordinator import decide_execution_mode, register_or_update_agent
from AINDY.agents.runtime_guardrails import AgentRuntimeGuardrailViolation
from AINDY.core.execution_signal_helper import record_agent_event
from AINDY.core.system_event_service import emit_error_event
from AINDY.platform_layer.trace_context import get_parent_event_id, get_trace_id, reset_parent_event_id, set_parent_event_id

from AINDY.agents.agent_runtime.shared import LOCAL_AGENT_ID, get_runtime_compat_module, logger


def execute_run(run_id: str, user_id: str, db: Session) -> Optional[dict]:
    try:
        compat = get_runtime_compat_module()
        from AINDY.db.models import AgentRun
        from AINDY.runtime.nodus_execution_service import execute_agent_run_via_nodus

        user_db_id = compat._db_user_id(user_id)
        db_run_id = compat._db_run_id(run_id)
        run = db.query(AgentRun).filter(AgentRun.id == db_run_id).first()
        if not run:
            logger.warning("[AgentRuntime] Run %s not found", run_id)
            return None
        if run.status not in ("approved",):
            logger.warning("[AgentRuntime] Run %s cannot execute — status=%s", run_id, run.status)
            return compat._run_to_dict(run)
        if not compat._user_matches(run.user_id, user_db_id):
            logger.warning("[AgentRuntime] Run %s owner mismatch", run_id)
            return None

        capability_token = getattr(run, "capability_token", None)
        if not isinstance(capability_token, dict):
            logger.warning("[AgentRuntime] Run %s cannot execute without scoped capability token", run_id)
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.error_message = "Missing scoped capability token."
            db.commit()
            record_agent_event(
                run_id=str(run.id),
                user_id=user_db_id,
                event_type="CAPABILITY_DENIED",
                db=db,
                correlation_id=getattr(run, "correlation_id", None),
                payload={"error": "missing scoped capability token"},
                required=True,
            )
            return compat._run_to_dict(run)

        run_type = getattr(run, "agent_type", "default")
        tools = compat._get_tools_for_run(run_type, user_id=user_db_id, db=db)
        local_capabilities = sorted(
            {
                tool.get("required_capability")
                for tool in tools
                if isinstance(tool, dict) and tool.get("required_capability")
            }
        )
        register_or_update_agent(
            db,
            agent_id=LOCAL_AGENT_ID,
            capabilities=local_capabilities,
            current_state={"run_id": str(run.id), "status": "executing"},
            load=min(1.0, max(0.1, (run.steps_total or 1) / 10.0)),
            health_status="healthy",
        )
        coordination = decide_execution_mode(
            db,
            local_agent_id=LOCAL_AGENT_ID,
            operation={
                "name": compat._run_objective(run),
                "description": run.executive_summary,
                "request": compat._run_objective(run),
                "required_capabilities": capability_token.get("allowed_capabilities", []),
            },
            user_id=str(user_db_id),
        )
        if coordination["mode"] in {"delegate", "collaborate"}:
            from AINDY.agents.agent_coordinator import dispatch_delegated_run

            try:
                child_run = dispatch_delegated_run(
                    db,
                    parent_run=run,
                    selected_agent=coordination["selected_agent"],
                    delegation_mode=coordination["mode"],
                    user_id=str(user_db_id),
                    trace_id=run.trace_id or get_trace_id(),
                )
            except AgentRuntimeGuardrailViolation as exc:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                run.error_message = str(exc)
                run.result = {
                    "coordination_mode": coordination["mode"],
                    "selected_agent": coordination["selected_agent"],
                    "candidates": coordination["candidates"],
                    "guardrail_code": exc.code,
                    "child_dispatched": False,
                }
                db.commit()
                record_agent_event(
                    run_id=str(run.id),
                    user_id=user_db_id,
                    event_type="DELEGATION_BLOCKED",
                    db=db,
                    correlation_id=getattr(run, "correlation_id", None),
                    payload={"coordination": coordination, "guardrail_code": exc.code, "error": str(exc)},
                    required=True,
                )
                return compat._run_to_dict(run)

            if child_run is None:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                run.error_message = "Delegation dispatch failed before a child run was created."
                run.result = {
                    "coordination_mode": coordination["mode"],
                    "selected_agent": coordination["selected_agent"],
                    "candidates": coordination["candidates"],
                    "child_dispatched": False,
                }
                db.commit()
                record_agent_event(
                    run_id=str(run.id),
                    user_id=user_db_id,
                    event_type="DELEGATION_BLOCKED",
                    db=db,
                    correlation_id=getattr(run, "correlation_id", None),
                    payload={"coordination": coordination, "error": run.error_message},
                    required=True,
                )
                return compat._run_to_dict(run)

            run.result = {
                "coordination_mode": coordination["mode"],
                "selected_agent": coordination["selected_agent"],
                "candidates": coordination["candidates"],
                "child_run_id": child_run["run_id"],
                "child_dispatched": True,
                "next_action": {
                    "type": coordination["mode"],
                    "selected_agent": coordination["selected_agent"],
                    "child_run_id": child_run["run_id"],
                },
            }
            run.status = "delegated"
            run.completed_at = None
            db.commit()
            record_agent_event(
                run_id=str(run.id),
                user_id=user_db_id,
                event_type="DELEGATION_DISPATCHED"
                if coordination["mode"] == "delegate"
                else "COLLABORATION_STARTED",
                db=db,
                correlation_id=getattr(run, "correlation_id", None),
                payload={
                    "coordination": coordination,
                    "child_run_id": child_run["run_id"],
                },
                required=True,
            )
            return compat._run_to_dict(run)

        if not getattr(run, "trace_id", None) and get_trace_id():
            run.trace_id = get_trace_id()
        run.status = "executing"
        run.started_at = datetime.now(timezone.utc)
        execution_memory_context = _build_execution_memory_context(
            objective=compat._run_objective(run),
            plan=run.plan or {},
            user_id=user_db_id,
            trace_id=run.trace_id or get_trace_id() or getattr(run, "correlation_id", None),
            db=db,
        )
        execution_plan = dict(run.plan or {})
        execution_plan["memory_context"] = execution_memory_context
        run.plan = execution_plan
        db.commit()
        try:
            from AINDY.core.execution_unit_service import ExecutionUnitService

            eu = ExecutionUnitService(db).get_by_source("agent_run", str(run.id))
            if eu:
                ExecutionUnitService(db).update_status(eu.id, "executing")
        except Exception:
            logger.debug("[EU] agent execute hook start skipped", exc_info=True)

        execution_started_event_id = record_agent_event(
            run_id=str(run.id),
            user_id=user_db_id,
            event_type="EXECUTION_STARTED",
            db=db,
            correlation_id=getattr(run, "correlation_id", None),
            payload={},
            required=True,
        )
        parent_token = set_parent_event_id(execution_started_event_id)
        try:
            execute_agent_run_via_nodus(
                run_id=str(run.id),
                plan=execution_plan,
                user_id=user_id,
                db=db,
                correlation_id=getattr(run, "correlation_id", None),
                execution_token=capability_token,
            )
        finally:
            reset_parent_event_id(parent_token)

        db.refresh(run)
        _emit_agent_run_score(run, db=db, user_id=user_db_id)
        hook_next_action = None
        if run.status == "completed":
            hook_results = compat._run_completion_hooks(
                getattr(run, "agent_type", "default"),
                {
                    "run": run,
                    "db": db,
                    # run_id is a string, so it survives the extension-boundary
                    # sanitizer (which strips db and redacts the run ORM). A
                    # first-party completion hook re-fetches the run by this id
                    # with its own session — the runtime never leaks a db/ORM
                    # handle across the boundary. See INFINITY-COMPLETION-HOOK-
                    # BOUNDARY-1 / PLANNER-SUBPROC-1.
                    "run_id": str(run.id),
                    "user_id": user_db_id,
                    "run_type": getattr(run, "agent_type", "default"),
                    "trace_id": run.trace_id or get_trace_id(),
                },
            )
            db.refresh(run)
            hook_next_action = _select_completion_hook_next_action(hook_results)
        _emit_agent_next_action(run, db=db, user_id=user_db_id, hook_action=hook_next_action)
        logger.info("[AgentRuntime] Run %s %s (%d/%d steps)", run_id, run.status, run.steps_completed, run.steps_total)
        try:
            from AINDY.core.execution_unit_service import ExecutionUnitService

            eu = ExecutionUnitService(db).get_by_source("agent_run", str(run.id))
            if eu:
                final_status = "completed" if run.status == "completed" else "failed" if run.status == "failed" else None
                if final_status:
                    ExecutionUnitService(db).update_status(eu.id, final_status)
        except Exception:
            logger.debug("[EU] agent execute hook finish skipped", exc_info=True)
        return compat._run_to_dict(run)
    except Exception as exc:
        compat = get_runtime_compat_module()
        logger.warning("[AgentRuntime] execute_run failed for %s: %s", run_id, exc)
        try:
            compat.emit_error_event(
                db=db,
                error_type="agent_execution",
                message=str(exc),
                user_id=user_id,
                trace_id=get_trace_id(),
                parent_event_id=get_parent_event_id(),
                source="agent",
                payload={"run_id": run_id},
                required=True,
            )
        except Exception:
            logger.exception("[AgentRuntime] failed to emit required error event for %s", run_id)
        return None


def _emit_agent_run_score(run, *, db: Session, user_id: str) -> None:
    """Emit a per-run SCORE_COMPUTED record for a finished agent run.

    Covers the terminal ``completed``/``failed`` states reached on the normal
    (non-exception) path. INFINITY-RUNTIME-1 Gap 3. Best-effort.
    """
    status = getattr(run, "status", None)
    if status not in ("completed", "failed"):
        return
    try:
        from AINDY.core.execution_score import compute_execution_score, emit_execution_score

        duration_ms = None
        started_at = getattr(run, "started_at", None)
        completed_at = getattr(run, "completed_at", None)
        if started_at and completed_at:
            duration_ms = (completed_at - started_at).total_seconds() * 1000

        score = compute_execution_score(status=status, result=getattr(run, "result", None))
        emit_execution_score(
            db=db,
            run_id=str(run.id),
            score=score,
            status=status,
            trace_id=getattr(run, "trace_id", None) or get_trace_id(),
            user_id=user_id,
            duration_ms=duration_ms,
            dimensions={
                "steps_completed": getattr(run, "steps_completed", None),
                "steps_total": getattr(run, "steps_total", None),
            },
            source="agent",
        )
    except Exception:  # best-effort; scoring must not break completion
        logger.debug("[AgentRuntime] score emit skipped for run %s", getattr(run, "id", "?"), exc_info=True)


def _select_completion_hook_next_action(hook_results):
    """Coerce completion-hook returns into a NextAction (INFINITY-RUNTIME-1 Gap 4)."""
    try:
        from AINDY.core.next_action import select_hook_next_action

        return select_hook_next_action(hook_results)
    except Exception:
        logger.debug("[AgentRuntime] next-action hook coercion skipped", exc_info=True)
        return None


def _emit_agent_next_action(run, *, db: Session, user_id: str, hook_action=None) -> None:
    """Emit a NEXT_ACTION_CHOSEN record for a finished agent run (Gap 4, record-only).

    Prefers a completion-hook decision; otherwise a runtime-default (done on
    success, retry/escalate on failure). Covers completed + failed. The runtime
    takes NO autonomous action — the app orchestrator consumes the event.
    """
    status = getattr(run, "status", None)
    if status not in ("completed", "failed"):
        return
    try:
        from AINDY.core.next_action import default_next_action, emit_next_action_chosen

        action = hook_action or default_next_action(
            status=status, result=getattr(run, "result", None), attempts_remaining=False
        )
        emit_next_action_chosen(
            db=db,
            run_id=str(run.id),
            next_action=action,
            status=status,
            trace_id=getattr(run, "trace_id", None) or get_trace_id(),
            user_id=user_id,
            source="agent",
        )
    except Exception:  # best-effort; must not break completion
        logger.debug("[AgentRuntime] next-action emit skipped for run %s", getattr(run, "id", "?"), exc_info=True)


def _build_execution_memory_context(*, objective: str, plan: dict, user_id: str, trace_id: str | None, db: Session) -> dict:
    try:
        from AINDY.db.dao.memory_node_dao import MemoryNodeDAO
        from AINDY.runtime.memory import MemoryOrchestrator, memory_items_to_dicts

        step_tools = [step.get("tool") for step in (plan or {}).get("steps", []) if isinstance(step, dict) and step.get("tool")]
        orchestrator = MemoryOrchestrator(MemoryNodeDAO)
        context = orchestrator.get_context(
            user_id=user_id,
            query=objective or "agent execution",
            db=db,
            max_tokens=900,
            metadata={
                "tags": [tool.replace(".", "_") for tool in step_tools[:3]],
                "limit": 9,
                "trace_id": trace_id,
                "node_types": ["outcome", "insight", "decision"],
            },
            operation_type="agent_execution",
        )
        try:
            from AINDY.core.execution_recall import emit_recall_used

            emit_recall_used(
                db=db,
                node_ids=context.ids,
                query=objective,
                trace_id=trace_id,
                user_id=user_id,
                operation_type="agent_execution",
                source="agent",
            )
        except Exception:
            logger.debug("[AgentRuntime] recall event emit skipped", exc_info=True)
        items = memory_items_to_dicts(context.items)
        return {
            "similar_past_outcomes": [item for item in items if item.get("memory_type") == "outcome"][:3],
            "relevant_failures": [item for item in items if item.get("memory_type") == "failure"][:3],
            "successful_patterns": [
                item
                for item in items
                if item.get("memory_type") in {"decision", "insight"} and (item.get("success_rate", 0.0) or 0.0) >= 0.5
            ][:3],
            "trace_id": trace_id,
        }
    except Exception as exc:
        logger.warning("[AgentRuntime] execution memory context failed: %s", exc)
        return {
            "similar_past_outcomes": [],
            "relevant_failures": [],
            "successful_patterns": [],
            "trace_id": trace_id,
        }
