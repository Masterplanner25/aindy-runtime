from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from AINDY.db.models.agent_registry import AgentRegistry
from AINDY.db.models.system_event import SystemEvent
from AINDY.agents.agent_message_bus import publish_operation_request
from AINDY.agents.runtime_guardrails import AgentRuntimeGuardrailViolation, enforce_delegation_guardrails
from AINDY.kernel.condition_codes import AgentRunStatus, is_agent_terminal
from AINDY.platform_layer.registry import get_agent_ranking_strategy
from AINDY.utils.uuid_utils import normalize_uuid


STALE_AGENT_MINUTES = 10


def _delegation_handshake_enabled() -> bool:
    """RTR-4: whether a delegated child is held for an accept/reject handshake."""
    try:
        from AINDY.config import settings

        return bool(getattr(settings, "AINDY_DELEGATION_HANDSHAKE", False))
    except Exception:
        return False


def _delegate_capability_ceiling(db, *, parent_run, selected_agent: dict) -> list[str]:
    """RTR-4 Gap (b): the capability set a delegated child may not exceed.

    Least privilege: the intersection of the parent's own granted capabilities
    (no escalation via delegation) and the delegate's registered capabilities.
    If the delegate declares no capabilities the ceiling is just the parent's
    grant (still no escalation). Never widens beyond the parent.
    """
    parent_caps = set(
        (getattr(parent_run, "capability_token", None) or {}).get("allowed_capabilities")
        or []
    )
    delegate_caps: set[str] = set(selected_agent.get("capabilities") or [])
    if not delegate_caps and db is not None:
        agent_id = normalize_uuid(selected_agent.get("agent_id"))
        if agent_id is not None:
            row = (
                db.query(AgentRegistry)
                .filter(AgentRegistry.agent_id == agent_id)
                .first()
            )
            if row is not None:
                delegate_caps = set(row.capabilities or [])
    if not parent_caps:
        # Parent grant unknown — fall back to the delegate's declared set so the
        # child is still bounded by what the delegate is registered to do.
        return sorted(delegate_caps)
    if not delegate_caps:
        return sorted(parent_caps)
    return sorted(parent_caps & delegate_caps)


def register_or_update_agent(
    db,
    *,
    agent_id: str,
    capabilities: list[str] | None = None,
    current_state: dict[str, Any] | None = None,
    load: float = 0.0,
    health_status: str = "healthy",
) -> dict[str, Any]:
    normalized_agent_id = normalize_uuid(agent_id)
    row = db.query(AgentRegistry).filter(AgentRegistry.agent_id == normalized_agent_id).first()
    if row is None:
        row = AgentRegistry(agent_id=normalized_agent_id)
        db.add(row)
    row.capabilities = capabilities or row.capabilities or []
    row.current_state = current_state or row.current_state or {}
    row.load = max(0.0, min(1.0, float(load or 0.0)))
    row.health_status = health_status or "healthy"
    row.last_seen = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return serialize_agent_registry(row)


def list_agents(db, *, include_stale: bool = False) -> list[dict[str, Any]]:
    query = db.query(AgentRegistry).order_by(AgentRegistry.last_seen.desc())
    rows = query.all()
    now = datetime.now(timezone.utc)
    results = []
    for row in rows:
        serialized = serialize_agent_registry(row)
        serialized["stale"] = _is_stale(row.last_seen, now)
        if include_stale or not serialized["stale"]:
            results.append(serialized)
    return results


def get_agent_status(db) -> dict[str, Any]:
    agents = list_agents(db, include_stale=True)
    healthy = sum(1 for agent in agents if agent["health_status"] == "healthy" and not agent["stale"])
    degraded = sum(1 for agent in agents if agent["health_status"] == "degraded" and not agent["stale"])
    critical = sum(1 for agent in agents if agent["health_status"] == "critical" or agent["stale"])
    return {
        "total_agents": len(agents),
        "healthy_agents": healthy,
        "degraded_agents": degraded,
        "critical_agents": critical,
        "agents": agents,
    }


def assign_operation(
    db,
    operation: dict[str, Any],
    *,
    user_id: str | None = None,
    trace_id: str | None = None,
    sender_agent_id: str | None = None,
) -> dict[str, Any] | None:
    candidates = _rank_candidate_agents(db, operation, user_id=user_id)
    if not candidates:
        return None
    best = candidates[0]
    if sender_agent_id:
        publish_operation_request(
            db=db,
            sender_agent_id=sender_agent_id,
            recipient_agent_id=best["agent_id"],
            operation=operation,
            user_id=user_id,
            trace_id=trace_id,
        )
    return best


def broadcast_operation(
    db,
    operation: dict[str, Any],
    *,
    user_id: str | None = None,
    trace_id: str | None = None,
    sender_agent_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    ranked = _rank_candidate_agents(db, operation, user_id=user_id)[:limit]
    if sender_agent_id:
        for candidate in ranked:
            publish_operation_request(
                db=db,
                sender_agent_id=sender_agent_id,
                recipient_agent_id=candidate["agent_id"],
                operation=operation,
                user_id=user_id,
                trace_id=trace_id,
            )
    return ranked


def decide_execution_mode(
    db,
    *,
    local_agent_id: str | None,
    operation: dict[str, Any],
    user_id: str | None = None,
) -> dict[str, Any]:
    ranked = _rank_candidate_agents(db, operation, user_id=user_id)
    if not ranked:
        return {"mode": "local", "selected_agent": None, "candidates": []}

    best = resolve_conflict(ranked)
    if local_agent_id and str(best["agent_id"]) == str(local_agent_id):
        return {"mode": "local", "selected_agent": best, "candidates": ranked[:3]}
    if len(ranked) >= 2 and abs(ranked[0]["coordination_score"] - ranked[1]["coordination_score"]) <= 0.05:
        return {"mode": "collaborate", "selected_agent": best, "candidates": ranked[:3]}
    return {"mode": "delegate", "selected_agent": best, "candidates": ranked[:3]}


def coordination_graph(db, *, user_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    query = (
        db.query(SystemEvent)
        .filter(SystemEvent.agent_id.isnot(None))
        .order_by(SystemEvent.timestamp.desc())
    )
    if user_id:
        query = query.filter(SystemEvent.user_id == normalize_uuid(user_id))
    rows = query.limit(limit).all()
    nodes = {}
    edges = []
    for row in rows:
        agent_key = str(row.agent_id)
        nodes[agent_key] = {
            "id": agent_key,
            "health_status": None,
            "load": None,
        }
        payload = row.payload or {}
        recipient = payload.get("recipient_agent_id")
        if recipient:
            edges.append(
                {
                    "source": agent_key,
                    "target": str(recipient),
                    "event_type": row.type,
                    "trace_id": row.trace_id,
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                }
            )
    for agent in list_agents(db, include_stale=True):
        nodes[str(agent["agent_id"])] = {
            "id": str(agent["agent_id"]),
            "health_status": agent["health_status"],
            "load": agent["load"],
        }
    return {"nodes": list(nodes.values()), "edges": edges}


def _rank_candidate_agents(db, operation: dict[str, Any], *, user_id: str | None = None) -> list[dict[str, Any]]:
    operation_capabilities = set(operation.get("required_capabilities") or operation.get("capabilities") or [])
    candidates = [
        _enrich_candidate_for_coordination(db, row, operation_capabilities=operation_capabilities, user_id=user_id)
        for row in list_agents(db, include_stale=False)
    ]
    context = {
        "db": db,
        "operation": operation,
        "user_id": user_id,
        "required_capabilities": sorted(operation_capabilities),
    }

    ranking_strategy = get_agent_ranking_strategy()
    if ranking_strategy is not None:
        ranked = ranking_strategy(candidates, context)
        if isinstance(ranked, list):
            return ranked

    ranked = list(candidates)
    ranked.sort(key=lambda item: item["coordination_score"], reverse=True)
    return ranked


def _enrich_candidate_for_coordination(
    db,
    row: dict[str, Any],
    *,
    operation_capabilities: set[str],
    user_id: str | None = None,
) -> dict[str, Any]:
    capability_overlap = 0.0
    capabilities = set(row.get("capabilities") or [])
    if operation_capabilities:
        capability_overlap = len(operation_capabilities & capabilities) / max(1, len(operation_capabilities))
    elif capabilities:
        capability_overlap = 0.5

    past_performance = _agent_performance_score(db, row["agent_id"], user_id=user_id)
    score = (
        capability_overlap * 0.45
        + (1.0 - float(row.get("load") or 0.0)) * 0.20
        + past_performance * 0.20
        + (0.15 if row.get("health_status") == "healthy" else 0.05 if row.get("health_status") == "degraded" else 0.0)
    )
    enriched = dict(row)
    enriched["coordination_score"] = round(max(0.0, min(1.0, score)), 4)
    enriched["capability_overlap"] = round(capability_overlap, 4)
    enriched["past_performance"] = round(past_performance, 4)
    return enriched


def resolve_conflict(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise ValueError("resolve_conflict requires at least one candidate")
    if len(candidates) == 1:
        return candidates[0]

    top_score = float(candidates[0].get("coordination_score") or 0.0)
    tied = [
        candidate for candidate in candidates
        if abs(float(candidate.get("coordination_score") or 0.0) - top_score) <= 0.05
    ]
    tied.sort(
        key=lambda item: (
            -(float(item.get("capability_overlap") or 0.0)),
            float(item.get("load") or 1.0),
            -(float(item.get("past_performance") or 0.0)),
        )
    )
    return tied[0]


def _agent_performance_score(db, agent_id: str, *, user_id: str | None = None) -> float:
    query = db.query(SystemEvent).filter(SystemEvent.agent_id == normalize_uuid(agent_id))
    if user_id:
        query = query.filter(SystemEvent.user_id == normalize_uuid(user_id))
    rows = query.order_by(SystemEvent.timestamp.desc()).limit(50).all()
    if not rows:
        return 0.5
    successes = sum(1 for row in rows if row.type.endswith(".completed") or row.type == "execution.completed")
    failures = sum(1 for row in rows if row.type.endswith(".failed") or row.type.startswith("error."))
    total = max(1, successes + failures)
    return max(0.1, min(1.0, (successes + 0.5) / (total + 1.0)))


def _is_stale(last_seen: datetime | None, now: datetime) -> bool:
    if not last_seen:
        return True
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return last_seen < now - timedelta(minutes=STALE_AGENT_MINUTES)


def serialize_agent_registry(row: AgentRegistry) -> dict[str, Any]:
    return {
        "agent_id": str(row.agent_id),
        "capabilities": row.capabilities or [],
        "current_state": row.current_state or {},
        "load": float(row.load or 0.0),
        "health_status": row.health_status,
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
    }


def dispatch_delegated_run(
    db,
    *,
    parent_run,
    selected_agent: dict,
    delegation_mode: str,
    user_id: str,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    try:
        import uuid as _uuid

        from AINDY.agents.agent_runtime.shared import _OBJECTIVE_ATTR, _run_objective
        from AINDY.agents.agent_runtime.shared import LOCAL_AGENT_ID
        from AINDY.agents.capability_service import mint_token
        from AINDY.db.models import AgentRun

        objective = _run_objective(parent_run)
        if not objective or getattr(parent_run, "user_id", None) is None:
            return None

        enforce_delegation_guardrails(
            db,
            parent_run=parent_run,
            selected_agent_id=selected_agent.get("agent_id"),
            trace_id=trace_id or parent_run.trace_id,
        )
        child_run_id = _uuid.uuid4()
        child_correlation_id = f"run_{_uuid.uuid4()}"
        selected_agent_id = normalize_uuid(selected_agent.get("agent_id"))
        child_agent_type = str(selected_agent.get("agent_id", "default"))[:64]

        # RTR-4 Gap (a): under the handshake opt-in the child is held pending the
        # delegate's accept/reject; otherwise it dispatches straight to approved.
        handshake = _delegation_handshake_enabled()
        initial_status = (
            AgentRunStatus.AWAITING_DELEGATION.value
            if handshake
            else AgentRunStatus.APPROVED.value
        )

        child_run = AgentRun(
            id=child_run_id,
            user_id=parent_run.user_id,
            agent_type=child_agent_type,
            plan=parent_run.plan,
            executive_summary=parent_run.executive_summary,
            overall_risk=parent_run.overall_risk or "high",
            status=initial_status,
            steps_total=parent_run.steps_total,
            correlation_id=child_correlation_id,
            trace_id=trace_id or parent_run.trace_id,
            parent_run_id=parent_run.id,
            spawned_by_agent_id=selected_agent_id,
            coordination_role=delegation_mode,
        )
        setattr(child_run, _OBJECTIVE_ATTR, objective)
        db.add(child_run)
        db.flush()

        # RTR-4 Gap (b): least-privilege delegation. The child token is clamped to
        # the intersection of the parent's grant (no escalation via delegation) and
        # the delegate's own registered capabilities, minted under the delegate's
        # agent_type rather than inheriting the parent's plan/identity verbatim.
        capability_ceiling = _delegate_capability_ceiling(
            db, parent_run=parent_run, selected_agent=selected_agent
        )
        child_token = mint_token(
            run_id=str(child_run_id),
            user_id=str(parent_run.user_id),
            plan=child_run.plan,
            db=db,
            approval_mode="manual",
            agent_type=child_agent_type,
            capability_ceiling=capability_ceiling,
            # RTR-4 gap (c): mark the token as a delegate so its memory writes
            # can be auto-scoped run-private (when the feature flag is on).
            parent_run_id=str(parent_run.id),
        )
        if child_token:
            child_run.capability_token = child_token
            child_run.execution_token = child_token.get("execution_token")

        parent_run.status = "delegated"
        parent_run.completed_at = None
        db.flush()
        db.refresh(child_run)

        assign_operation(
            db,
            operation={
                "name": objective,
                "description": parent_run.executive_summary or objective,
                "request": objective,
                "required_capabilities": list(
                    (child_token or {}).get("allowed_capabilities") or []
                ),
                "child_run_id": str(child_run.id),
                "parent_run_id": str(parent_run.id),
            },
            user_id=user_id,
            trace_id=trace_id or parent_run.trace_id,
            sender_agent_id=str(getattr(parent_run, "spawned_by_agent_id", None) or LOCAL_AGENT_ID),
        )
        return _serialize_delegated_run(child_run)
    except AgentRuntimeGuardrailViolation:
        raise
    except Exception as exc:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "[AgentCoordinator] dispatch_delegated_run failed: %s", exc
        )
        return None


def _serialize_delegated_run(run) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "spawned_by_agent_id": str(run.spawned_by_agent_id) if run.spawned_by_agent_id else None,
        "status": run.status,
        "coordination_role": run.coordination_role,
        "correlation_id": run.correlation_id,
    }


def respond_to_delegation(
    db,
    *,
    child_run_id: str,
    accept: bool,
    responder_agent_id: str | None = None,
    reason: str | None = None,
    request_message_id: str | None = None,
) -> dict[str, Any]:
    """RTR-4 Gap (a): the delegate's accept/reject of a delegated child run.

    Completes the inter-agent handshake: a child parked at ``awaiting_delegation``
    (created under ``AINDY_DELEGATION_HANDSHAKE``) is either promoted to
    ``approved`` (execution may now proceed via the normal approved path) or
    failed. On reject the waiting parent is failed with a ``delegation_rejected``
    reason so it does not hang in ``delegated`` forever. Publishes the matching
    ``operation_accept`` / ``operation_reject`` bus message.

    Returns ``{"ok": bool, "error_code"?: str, "run"?: dict}``. Idempotent in the
    sense that a child no longer awaiting delegation returns ``wrong_status``.
    """
    from AINDY.agents.agent_message_bus import (
        publish_operation_accept,
        publish_operation_reject,
    )
    from AINDY.agents.agent_runtime.shared import LOCAL_AGENT_ID
    from AINDY.core.execution_signal_helper import record_agent_event
    from AINDY.db.models import AgentRun

    child = db.query(AgentRun).filter(AgentRun.id == normalize_uuid(child_run_id)).first()
    if child is None:
        return {"ok": False, "error_code": "not_found"}
    if child.status != AgentRunStatus.AWAITING_DELEGATION.value:
        return {
            "ok": False,
            "error_code": "wrong_status",
            "detail": f"Child run is {child.status}, not awaiting delegation",
        }

    responder = str(
        responder_agent_id or getattr(child, "spawned_by_agent_id", None) or LOCAL_AGENT_ID
    )
    recipient = None
    parent = None
    if child.parent_run_id is not None:
        parent = db.query(AgentRun).filter(AgentRun.id == child.parent_run_id).first()
        if parent is not None:
            recipient = str(getattr(parent, "spawned_by_agent_id", None) or LOCAL_AGENT_ID)

    now = datetime.now(timezone.utc)
    if accept:
        child.status = AgentRunStatus.APPROVED.value
        child.approved_at = now
        event_type = "DELEGATION_ACCEPTED"
    else:
        child.status = AgentRunStatus.FAILED.value
        child.completed_at = now
        child.error_message = reason or "Delegation rejected by delegate"
        event_type = "DELEGATION_REJECTED"
        # Un-hang the parent: a rejected delegation is a terminal parent failure.
        if parent is not None and not is_agent_terminal(parent.status):
            parent.status = AgentRunStatus.FAILED.value
            parent.completed_at = now
            parent.error_message = "delegation_rejected"
    db.flush()

    if accept:
        publish_operation_accept(
            db=db,
            sender_agent_id=responder,
            recipient_agent_id=recipient,
            child_run_id=str(child.id),
            request_message_id=request_message_id,
            user_id=str(child.user_id) if child.user_id else None,
            trace_id=child.trace_id,
        )
    else:
        publish_operation_reject(
            db=db,
            sender_agent_id=responder,
            recipient_agent_id=recipient,
            child_run_id=str(child.id),
            reason=reason,
            request_message_id=request_message_id,
            user_id=str(child.user_id) if child.user_id else None,
            trace_id=child.trace_id,
        )

    record_agent_event(
        run_id=str(child.id),
        user_id=child.user_id,
        event_type=event_type,
        db=db,
        correlation_id=getattr(child, "correlation_id", None),
        payload={
            "responder_agent_id": responder,
            "reason": reason,
            "parent_run_id": str(child.parent_run_id) if child.parent_run_id else None,
        },
        required=True,
    )
    db.commit()
    return {"ok": True, "run": _serialize_delegated_run(child)}


def detect_run_conflict(
    db,
    *,
    user_id: str,
    objective: str,
    agent_id: str | None = None,
) -> dict[str, Any]:
    from AINDY.agents.agent_runtime.shared import _run_objective
    from AINDY.db.models import AgentRun

    uid = normalize_uuid(user_id)
    active_runs = (
        db.query(AgentRun)
        .filter(
            AgentRun.user_id == uid,
            AgentRun.status.in_(["approved", "executing", "delegated"]),
        )
        .order_by(AgentRun.created_at.desc())
        .limit(20)
        .all()
    )
    normalized_objective = str(objective or "").strip().lower()
    for run in active_runs:
        run_obj = str(_run_objective(run) or "").strip().lower()
        if run_obj == normalized_objective:
            if agent_id and getattr(run, "correlation_id", None) == agent_id:
                continue
            return {
                "conflict": True,
                "conflicting_run_id": str(run.id),
                "conflicting_status": run.status,
            }
    return {
        "conflict": False,
        "conflicting_run_id": None,
        "conflicting_status": None,
    }


def detect_memory_write_conflict(
    db,
    *,
    user_id: str,
    memory_path: str,
    agent_id: str | None = None,
) -> dict[str, Any]:
    uid = normalize_uuid(user_id)
    window = datetime.now(timezone.utc) - timedelta(seconds=30)
    recent = (
        db.query(SystemEvent)
        .filter(
            SystemEvent.user_id == uid,
            SystemEvent.type == "agent.message.memory_share",
            SystemEvent.timestamp >= window,
        )
        .order_by(SystemEvent.timestamp.desc())
        .limit(10)
        .all()
    )
    for event in recent:
        payload = event.payload or {}
        if payload.get("memory_path") == memory_path:
            conflicting_agent = str(event.agent_id) if event.agent_id else None
            if agent_id and conflicting_agent == agent_id:
                continue
            return {
                "conflict": True,
                "conflicting_agent_id": conflicting_agent,
                "message": (
                    f"Memory path '{memory_path}' was written by agent "
                    f"{conflicting_agent} within the last 30 seconds."
                ),
            }
    return {
        "conflict": False,
        "conflicting_agent_id": None,
        "message": "No conflict detected.",
    }


