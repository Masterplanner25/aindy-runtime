from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from AINDY.agents.agent_runtime import (
    approve_run,
    create_run,
    execute_run,
    get_run_events,
    reject_run,
    replay_run,
    run_to_dict,
    to_execution_response,
)
from AINDY.agents.runtime_guardrails import AgentRuntimeGuardrailViolation
from AINDY.agents.agent_tools import TOOL_REGISTRY, suggest_tools
from AINDY.agents.autonomous_controller import (
    build_decision_response,
    evaluate_live_trigger,
    record_decision,
)
from AINDY.agents.capability_service import get_auto_grantable_tools
from AINDY.agents.stuck_run_service import recover_stuck_agent_run
from AINDY.core.execution_dispatcher import async_heavy_execution_enabled
from AINDY.db.models import AgentRun, AgentStep, AgentTrustSettings
from AINDY.platform_layer.async_job_service import defer_async_job, submit_autonomous_async_job
from AINDY.platform_layer.trace_context import trace_scope
from AINDY.utils.uuid_utils import normalize_uuid


def _normalize_run_id(run_id: str) -> Any:
    try:
        return normalize_uuid(run_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid run_id") from exc


def _get_owned_run(*, db, user_id, run_id: str) -> AgentRun:
    normalized_run_id = _normalize_run_id(run_id)
    run = db.query(AgentRun).filter(AgentRun.id == normalized_run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return run


def _decision_or_defer_response(
    *,
    db,
    user_id,
    trigger: dict[str, Any],
    trigger_context: dict[str, Any],
    task_name: str,
    payload: dict[str, Any],
):
    # TEST-ORDER-CONTEXTVAR-1 — `trace_scope`, not `ensure_trace_id`: the only caller today is
    # the agent route, where the middleware already owns the trace, so this reads; but if this
    # is ever reached from a thread with no ambient trace, what it establishes must not
    # outlive the call (the flow runner pinned a scheduler thread's trace id that way).
    with trace_scope() as trace_id:
        evaluation = evaluate_live_trigger(
            db=db,
            trigger=trigger,
            user_id=user_id,
            context=trigger_context,
        )
        record_decision(
            db=db,
            trigger=trigger,
            evaluation=evaluation,
            user_id=user_id,
            trace_id=trace_id,
            context=trigger_context,
        )

        if evaluation["decision"] == "ignore":
            return {"_decision_response": build_decision_response(evaluation, trace_id=trace_id)}

        if evaluation["decision"] == "defer":
            log_id = defer_async_job(
                task_name=task_name,
                payload=payload,
                user_id=user_id,
                source="agent_router",
                decision=evaluation,
            )
            return {
                "_http_status": 202,
                "_http_response": build_decision_response(
                    evaluation,
                    trace_id=log_id,
                    result={
                        "automation_log_id": log_id,
                        "decision": "defer",
                        "reason": evaluation["reason"],
                    },
                    next_action={"type": "poll_automation_log", "automation_log_id": log_id},
                ),
            }


    return None


def create_agent_run_runtime(*, goal: str, db, user_id):
    goal = goal.strip()
    if async_heavy_execution_enabled():
        with trace_scope() as trace_id:
            trigger_context = {"goal": goal, "importance": 0.95, "trace_id": trace_id}
            return {
                "_http_status": 202,
                "_http_response": submit_autonomous_async_job(
                    task_name="agent.create_run",
                    payload={"goal": goal, "user_id": str(user_id), "trace_id": trace_id},
                    user_id=user_id,
                    source="agent_router",
                    trigger_type="user",
                    trigger_context=trigger_context,
                    db=db,
                ),
            }

    decision = _decision_or_defer_response(
        db=db,
        user_id=user_id,
        trigger={"trigger_type": "user", "source": "agent_router", "goal": goal},
        trigger_context={"goal": goal, "importance": 0.95},
        task_name="agent.create_run",
        payload={
            "goal": goal,
            "user_id": str(user_id),
            "__autonomy": {
                "trigger_type": "user",
                "source": "agent_router",
                "context": {"goal": goal, "importance": 0.95},
            },
        },
    )
    if decision is not None:
        return decision

    try:
        run = create_run(goal=goal, user_id=user_id, db=db)
    except AgentRuntimeGuardrailViolation as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if not run:
        # COST-GOVERNOR-1 phase 4 — a budget refusal is "rejected", not "broken": 429 with the
        # RESOURCE_LIMIT_EXCEEDED reason, the same status the pipeline gives a tenant at its
        # concurrency limit. Found live: the first refused planner call surfaced as a 500.
        from AINDY.agents.agent_runtime.shared import get_runtime_compat_module
        from AINDY.platform_layer.llm_client import find_budget_refusal

        # The same compat module generate_plan wrote to, so the two cannot disagree; and the
        # cause chain is walked because an app planner rewraps the seam's error before the
        # runtime sees it (found live: `AnthropicPlannerError(...) from LLMBudgetExceededError`).
        refusal = find_budget_refusal(getattr(get_runtime_compat_module()._plan_failure, "error", None))
        if refusal is not None:
            raise HTTPException(status_code=429, detail=str(refusal))
        raise HTTPException(status_code=500, detail="Failed to generate plan")
    if run["status"] == "approved":
        run = execute_run(run_id=run["run_id"], user_id=user_id, db=db) or run
    return to_execution_response(run, db)


def list_agent_runs_runtime(*, db, user_id, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    query = db.query(AgentRun).filter(AgentRun.user_id == user_id)
    if status:
        query = query.filter(AgentRun.status == status)
    runs = query.order_by(AgentRun.created_at.desc()).limit(limit).all()
    rows = []
    for run in runs:
        row = run_to_dict(run)
        row["goal"] = row.get("objective")
        rows.append(row)
    return rows


def get_agent_run_runtime(*, db, user_id, run_id: str) -> dict[str, Any]:
    row = run_to_dict(_get_owned_run(db=db, user_id=user_id, run_id=run_id))
    row["goal"] = row.get("objective")
    return row


def approve_agent_run_runtime(*, db, user_id, run_id: str):
    trigger_context = {"goal": f"approve_run:{run_id}", "importance": 0.9}
    decision = _decision_or_defer_response(
        db=db,
        user_id=user_id,
        trigger={
            "trigger_type": "user",
            "source": "agent_router.approve",
            "goal": f"approve_run:{run_id}",
        },
        trigger_context=trigger_context,
        task_name="agent.approve_run",
        payload={
            "run_id": run_id,
            "user_id": str(user_id),
            "__autonomy": {
                "trigger_type": "user",
                "source": "agent_router.approve",
                "context": trigger_context,
            },
        },
    )
    if decision is not None:
        return decision

    run = approve_run(run_id=run_id, user_id=user_id, db=db)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found or not approvable")
    return to_execution_response(run, db)


def reject_agent_run_runtime(*, db, user_id, run_id: str):
    run = reject_run(run_id=run_id, user_id=user_id, db=db)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found or not rejectable")
    return to_execution_response(run, db)


def resume_agent_run_runtime(*, db, user_id, run_id: str, payload: dict | None = None):
    """Resume a run parked on a mid-plan WAIT step (RTR-1 Phase 2e).

    Publishes the event the run is waiting on — scoped to the run's correlation —
    so the scheduler fires the resume callback and the next plan segment runs. This
    is the "approval" action a human (or an external system) takes to release a
    waiting agent run. The run must belong to ``user_id`` and be ``status="waiting"``.

    Correlation must match the wait's registration exactly (live-path, rehydration,
    and this route all resolve ``wait_state.correlation_key or run.correlation_id``).

    ★ FR-38 / DEC-069 — a run parked at the AUTHORITY GATE (``wait_state.authority_gate``)
    needs a decision, ``{"decision": "skip" | "abort", "note": …}``; without one the request
    is refused (409) and the run stays parked. The bus carries no payload (DEC-013), so the
    decision's durable home is the ROW: ``skip`` writes the gated step's `agent_steps` row as
    `skipped` — the row the tool would have written — and the re-driven segment replays it;
    ``abort`` fails the run here and publishes nothing. An unknown decision is refused (422)
    and the run stays parked, recorded on the gate.
    """
    from AINDY.kernel.event_bus import publish_event

    run = (
        db.query(AgentRun)
        .filter(
            AgentRun.id == normalize_uuid(run_id),
            AgentRun.user_id == normalize_uuid(user_id),
        )
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "waiting":
        raise HTTPException(
            status_code=409, detail=f"Run is not waiting (status={run.status})"
        )
    wait_state = run.wait_state or {}
    event_type = wait_state.get("event_type")
    if not event_type:
        raise HTTPException(status_code=409, detail="Run has no wait event to resume")

    correlation = wait_state.get("correlation_key") or run.correlation_id
    gate = wait_state.get("authority_gate")
    decision_record = None
    if isinstance(gate, dict):
        decision_record = _decide_authority_gate(db=db, run=run, gate=gate, payload=payload)
        if decision_record["decision"] == "abort":
            return {
                "run_id": str(run.id), "status": run.status, "resumed_event": None,
                "correlation_id": correlation, "waiters_notified": 0, "authority_gate": decision_record,
            }
    waiters_notified = publish_event(event_type, correlation_id=correlation, run_id=str(run.id))
    return {
        "run_id": str(run.id),
        "status": run.status,
        "resumed_event": event_type,
        "correlation_id": correlation,
        "waiters_notified": waiters_notified,
        "authority_gate": decision_record,
    }


def _decide_authority_gate(*, db, run, gate: dict, payload: dict | None) -> dict:
    """Apply the operator's decision to a run parked at the nodus_vm authority gate."""
    from AINDY.agents.authority_negotiation import (
        AUTHORITY_DECISION_SCHEMA,
        DECISION_ABORT,
        DECISION_SKIP,
        OUTCOME_WAITING,
        count_gate_outcome,
        read_gate_decision,
    )
    from AINDY.core.execution_signal_helper import record_agent_event
    from AINDY.kernel.syscall_versioning import validate_payload

    if not isinstance(payload, dict) or "decision" not in payload:
        raise HTTPException(
            status_code=409,
            detail="Run is parked at the authority gate; resume needs a decision: "
                   + " | ".join(gate.get("decisions") or [DECISION_SKIP, DECISION_ABORT]),
        )
    # the gate's typed resume (WAIT-TYPED-CONTRACT-1), the same validator the flow route uses
    errors = validate_payload(AUTHORITY_DECISION_SCHEMA, dict(payload))
    if errors:
        raise HTTPException(status_code=422, detail={"resume_schema": AUTHORITY_DECISION_SCHEMA, "errors": errors})
    decision, note = read_gate_decision(payload)
    if decision is None:
        refused = dict(gate, last_refused_decision=payload.get("decision"))
        run.wait_state = dict(run.wait_state or {}, authority_gate=refused)
        db.commit()
        raise HTTPException(
            status_code=422,
            detail=f"unknown authority-gate decision {payload.get('decision')!r}; "
                   f"the run stays parked (decisions: {', '.join(gate.get('decisions') or [])})",
        )
    step_index = int(gate.get("step_index"))
    tool_name = str(gate.get("tool") or "")
    now = datetime.now(timezone.utc)
    row = (
        db.query(AgentStep)
        .filter(AgentStep.run_id == run.id, AgentStep.step_index == step_index)
        .first()
    )
    if row is None:
        row = AgentStep(run_id=run.id, step_index=step_index, tool_name=tool_name)
        db.add(row)
    row.tool_name = tool_name
    row.tool_args = gate.get("tool_args") or {}
    row.result = {"authority_gate": decision, "note": note}
    row.executed_at = now
    if not row.correlation_id and run.correlation_id:
        row.correlation_id = run.correlation_id
    record_agent_event(
        run_id=str(run.id), user_id=str(run.user_id), event_type="AUTHORITY_NEGOTIATED", db=db,
        correlation_id=run.correlation_id,
        payload={"step_index": step_index, "denied_tool": tool_name, "outcome": OUTCOME_WAITING,
                 "decision": decision, "note": note},
        required=False,
    )
    count_gate_outcome(f"gate_{decision}")
    if decision == DECISION_ABORT:
        error_msg = f"Step {step_index} ({tool_name}) aborted by operator at the authority gate" + (f": {note}" if note else "")
        row.status = "failed"
        row.error_message = error_msg
        run.status = "failed"
        run.error_message = error_msg
        run.completed_at = now
        run.wait_state = None
        db.commit()
        record_agent_event(
            run_id=str(run.id), user_id=str(run.user_id), event_type="FAILED", db=db,
            correlation_id=run.correlation_id,
            payload={"steps_completed": run.steps_completed or 0, "steps_total": run.steps_total or 0,
                     "error": error_msg},
            required=False,
        )
        try:
            from AINDY.runtime.nodus_execution_service import _sync_agent_eu_status

            _sync_agent_eu_status(db, str(run.id), "failed")
        except Exception:  # noqa: BLE001 — the EU mirror is best-effort, as it is on the chain
            pass
        return {"decision": decision, "note": note, "step_index": step_index, "run_status": "failed"}
    # skip — the row is the decision's durable home; the re-driven segment replays it (DEC-069)
    row.status = "skipped"
    row.error_message = None
    db.commit()
    return {"decision": decision, "note": note, "step_index": step_index, "run_status": "resuming"}


def recover_agent_run_runtime(*, db, user_id, run_id: str, force: bool = False):
    result = recover_stuck_agent_run(run_id=run_id, user_id=user_id, db=db, force=force)
    if result["ok"]:
        return to_execution_response(result["run"], db)

    http_map = {"not_found": 404, "forbidden": 403, "wrong_status": 409, "too_recent": 409}
    raise HTTPException(
        status_code=http_map.get(result.get("error_code", "internal_error"), 500),
        detail=result.get("detail", result.get("error_code", "internal_error")),
    )


def replay_agent_run_runtime(*, db, user_id, run_id: str):
    try:
        new_run = replay_run(run_id=run_id, user_id=user_id, db=db)
    except AgentRuntimeGuardrailViolation as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if not new_run:
        raise HTTPException(status_code=404, detail="Run not found or not replayable")
    return to_execution_response(new_run, db)


def list_agent_run_steps_runtime(*, db, user_id, run_id: str) -> list[dict[str, Any]]:
    run = _get_owned_run(db=db, user_id=user_id, run_id=run_id)
    steps = (
        db.query(AgentStep)
        .filter(AgentStep.run_id == run.id)
        .order_by(AgentStep.step_index.asc())
        .all()
    )
    return [
        {
            "step_index": step.step_index,
            "tool_name": step.tool_name,
            "description": step.description,
            "risk_level": step.risk_level,
            "status": step.status,
            "result": step.result,
            "error_message": step.error_message,
            "execution_ms": step.execution_ms,
            "executed_at": step.executed_at.isoformat() if step.executed_at else None,
        }
        for step in steps
    ]


def list_agent_run_events_runtime(*, db, user_id, run_id: str) -> dict[str, Any]:
    normalized_run_id = _normalize_run_id(run_id)
    result = get_run_events(run_id=run_id, user_id=user_id, db=db)
    if result is not None:
        return result

    run = db.query(AgentRun).filter(AgentRun.id == normalized_run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    raise HTTPException(status_code=403, detail="Not authorized")


def list_agent_tools_runtime() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "risk": entry["risk"],
            "description": entry["description"],
            "capability": entry.get("capability"),
            "required_capability": entry.get("required_capability"),
            "category": entry.get("category"),
            "egress_scope": entry.get("egress_scope"),
        }
        for name, entry in TOOL_REGISTRY.items()
    ]


def get_agent_trust_runtime(*, db, user_id) -> dict[str, Any]:
    trust = db.query(AgentTrustSettings).filter(AgentTrustSettings.user_id == user_id).first()
    return {
        "user_id": str(user_id),
        "auto_execute_low": trust.auto_execute_low if trust else False,
        "auto_execute_medium": trust.auto_execute_medium if trust else False,
        "allowed_auto_grant_tools": (
            trust.allowed_auto_grant_tools
            if trust and trust.allowed_auto_grant_tools is not None
            else get_auto_grantable_tools(user_id=user_id, db=db)
        ),
        "note": "High-risk plans always require approval regardless of trust settings.",
    }


def update_agent_trust_runtime(
    *,
    db,
    user_id,
    auto_execute_low: bool | None = None,
    auto_execute_medium: bool | None = None,
    allowed_auto_grant_tools: list[str] | None = None,
) -> dict[str, Any]:
    trust = db.query(AgentTrustSettings).filter(AgentTrustSettings.user_id == user_id).first()
    if not trust:
        trust = AgentTrustSettings(user_id=user_id)
        db.add(trust)

    if auto_execute_low is not None:
        trust.auto_execute_low = auto_execute_low
    if auto_execute_medium is not None:
        trust.auto_execute_medium = auto_execute_medium
    if allowed_auto_grant_tools is not None:
        trust.allowed_auto_grant_tools = sorted(
            {
                tool_name
                for tool_name in allowed_auto_grant_tools
                if tool_name in TOOL_REGISTRY
                and TOOL_REGISTRY[tool_name]["risk"] in {"low", "medium"}
                and tool_name != "genesis.message"
            }
        )

    trust.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(trust)
    return {
        "user_id": str(user_id),
        "auto_execute_low": trust.auto_execute_low,
        "auto_execute_medium": trust.auto_execute_medium,
        "allowed_auto_grant_tools": trust.allowed_auto_grant_tools or [],
        "note": "High-risk plans always require approval regardless of trust settings.",
    }


def get_agent_tool_suggestions_runtime(*, db, user_id) -> list[dict[str, Any]]:
    return suggest_tools(user_id=user_id, db=db)
