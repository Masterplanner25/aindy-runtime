from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from AINDY.db.models import AgentRun
from AINDY.db.models.job_log import JobLog
from AINDY.utils.uuid_utils import normalize_uuid


ACTIVE_AGENT_RUN_STATUSES = ("pending_approval", "approved", "executing", "delegated", "waiting")
ACTIVE_JOB_STATUSES = ("pending", "running", "deferred")


class AgentRuntimeGuardrailViolation(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def enforce_run_creation_guardrails(
    db,
    *,
    user_id: str | None,
    objective: str | None,
    trace_id: str | None,
) -> None:
    user_uuid = _normalize_uuid_or_none(user_id)
    normalized_objective = _normalize_text(objective)
    trace_limit = _int_env("AINDY_AGENT_MAX_RUNS_PER_TRACE", 6)
    same_objective_limit = _int_env("AINDY_AGENT_MAX_SAME_OBJECTIVE_PER_TRACE", 1)

    if user_uuid is None or not trace_id:
        return

    active_trace_runs = (
        db.query(AgentRun)
        .filter(
            AgentRun.user_id == user_uuid,
            AgentRun.trace_id == str(trace_id),
            AgentRun.status.in_(ACTIVE_AGENT_RUN_STATUSES),
        )
        .all()
    )
    if trace_limit > 0 and len(active_trace_runs) >= trace_limit:
        raise AgentRuntimeGuardrailViolation(
            "trace_run_limit_exceeded",
            (
                f"Agent run creation blocked: trace {trace_id} already has "
                f"{len(active_trace_runs)} active run(s), limit={trace_limit}."
            ),
        )

    if normalized_objective and same_objective_limit > 0:
        duplicate_count = sum(
            1
            for run in active_trace_runs
            if _normalize_text(_run_objective(run)) == normalized_objective
        )
        if duplicate_count >= same_objective_limit:
            raise AgentRuntimeGuardrailViolation(
                "duplicate_trace_objective",
                (
                    "Agent run creation blocked: the same objective is already active "
                    f"for trace {trace_id}."
                ),
            )


def enforce_replay_guardrails(db, *, original_run) -> None:
    max_replay_depth = _int_env("AINDY_AGENT_MAX_REPLAY_DEPTH", 4)
    replay_depth = 0
    seen: set[str] = set()
    current = original_run

    while current is not None:
        current_id = str(getattr(current, "id", "") or "")
        if current_id:
            if current_id in seen:
                raise AgentRuntimeGuardrailViolation(
                    "replay_lineage_cycle",
                    f"Replay blocked: replay lineage for run {current_id} contains a cycle.",
                )
            seen.add(current_id)

        previous_id = getattr(current, "replayed_from_run_id", None)
        if not previous_id:
            break

        replay_depth += 1
        if max_replay_depth > 0 and replay_depth >= max_replay_depth:
            raise AgentRuntimeGuardrailViolation(
                "replay_depth_exceeded",
                (
                    "Replay blocked: replay lineage depth "
                    f"{replay_depth} reached limit={max_replay_depth}."
                ),
            )

        current = db.query(AgentRun).filter(AgentRun.id == _normalize_uuid_or_none(previous_id)).first()


def enforce_delegation_guardrails(
    db,
    *,
    parent_run,
    selected_agent_id: str | None,
    trace_id: str | None,
) -> dict[str, Any]:
    parent_chain = _load_parent_chain(db, parent_run)
    current_depth = max(0, len(parent_chain) - 1)
    max_depth = _int_env("AINDY_AGENT_MAX_DELEGATION_DEPTH", 3)
    max_children = _int_env("AINDY_AGENT_MAX_CHILD_RUNS_PER_PARENT", 8)
    normalized_selected_agent_id = _normalize_uuid_string_or_none(selected_agent_id)

    if max_depth > 0 and current_depth >= max_depth:
        raise AgentRuntimeGuardrailViolation(
            "delegation_depth_exceeded",
            (
                "Delegation blocked: parent run is already at delegation depth "
                f"{current_depth}, limit={max_depth}."
            ),
        )

    existing_children = (
        db.query(AgentRun)
        .filter(AgentRun.parent_run_id == getattr(parent_run, "id", None))
        .all()
    )
    if max_children > 0 and len(existing_children) >= max_children:
        raise AgentRuntimeGuardrailViolation(
            "child_run_limit_exceeded",
            (
                "Delegation blocked: parent run already has "
                f"{len(existing_children)} child run(s), limit={max_children}."
            ),
        )

    ancestor_agent_ids = {
        str(run.spawned_by_agent_id)
        for run in parent_chain
        if getattr(run, "spawned_by_agent_id", None) is not None
    }
    if normalized_selected_agent_id and normalized_selected_agent_id in ancestor_agent_ids:
        raise AgentRuntimeGuardrailViolation(
            "delegation_cycle_detected",
            (
                "Delegation blocked: selected agent already appears in the "
                "ancestor delegation chain."
            ),
        )

    if normalized_selected_agent_id:
        duplicate_child = next(
            (
                child
                for child in existing_children
                if child.status in ACTIVE_AGENT_RUN_STATUSES
                and str(getattr(child, "spawned_by_agent_id", "") or "") == normalized_selected_agent_id
                and (not trace_id or str(getattr(child, "trace_id", "") or "") == str(trace_id))
            ),
            None,
        )
        if duplicate_child is not None:
            raise AgentRuntimeGuardrailViolation(
                "duplicate_child_run",
                (
                    "Delegation blocked: an active child run already exists for this "
                    "parent, selected agent, and trace."
                ),
            )

    return {
        "delegation_depth": current_depth,
        "child_runs_existing": len(existing_children),
    }


def build_autonomous_submission_key(
    *,
    task_name: str,
    payload: dict[str, Any],
    user_id: str | None,
    source: str,
    trigger_context: dict[str, Any] | None,
) -> str | None:
    context = dict(trigger_context or {})
    signal = {
        "task_name": task_name,
        "source": source,
        "user_id": str(user_id) if user_id is not None else None,
        "goal": payload.get("goal") or context.get("goal"),
        "objective": payload.get("objective") or context.get("objective"),
        "run_id": payload.get("run_id") or context.get("run_id"),
        "parent_run_id": payload.get("parent_run_id") or context.get("parent_run_id"),
        "trace_id": payload.get("trace_id") or context.get("trace_id"),
        "correlation_id": payload.get("correlation_id") or context.get("correlation_id"),
    }
    if not any(signal.values()):
        return None
    encoded = json.dumps(signal, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def has_active_autonomous_duplicate(
    db,
    *,
    task_name: str,
    user_id: str | None,
    source: str,
    submission_key: str | None,
) -> bool:
    if not submission_key:
        return False

    user_uuid = _normalize_uuid_or_none(user_id)
    query = db.query(JobLog).filter(
        JobLog.job_name == task_name,
        JobLog.status.in_(ACTIVE_JOB_STATUSES),
    )
    if user_uuid is None:
        query = query.filter(JobLog.user_id.is_(None))
    else:
        query = query.filter(JobLog.user_id == user_uuid)

    for row in query.order_by(JobLog.created_at.desc()).limit(25).all():
        if str(getattr(row, "source", "") or "") != source:
            continue
        payload = getattr(row, "payload", None) or {}
        if payload.get("__runtime_submission_key") == submission_key:
            return True
    return False


def _load_parent_chain(db, run) -> list[Any]:
    chain = []
    current = run
    seen: set[str] = set()

    while current is not None:
        current_id = str(getattr(current, "id", "") or "")
        if current_id:
            if current_id in seen:
                raise AgentRuntimeGuardrailViolation(
                    "delegation_parent_cycle",
                    f"Delegation blocked: parent chain for run {current_id} contains a cycle.",
                )
            seen.add(current_id)
        chain.append(current)
        parent_id = getattr(current, "parent_run_id", None)
        if not parent_id:
            break
        current = db.query(AgentRun).filter(AgentRun.id == parent_id).first()

    chain.reverse()
    return chain


def _normalize_uuid_or_none(value: str | None):
    if value in (None, ""):
        return None
    try:
        return normalize_uuid(value)
    except Exception:
        return value


def _normalize_uuid_string_or_none(value: str | None) -> str | None:
    normalized = _normalize_uuid_or_none(value)
    if normalized is None:
        return None
    return str(normalized)


def _normalize_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _run_objective(run) -> str | None:
    return getattr(run, "objective", None) or getattr(run, "goal", None)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
