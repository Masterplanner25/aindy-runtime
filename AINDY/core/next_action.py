"""Post-execution Next-Action decision (INFINITY-RUNTIME-1, Gap 4).

The Infinity loop audit found there is no runtime-owned Next-Action engine: after
a run the system records what happened, but "what should happen next" is decided
only by the flow graph or an app-registered completion hook whose return value is
discarded. This module defines the Next-Action contract, a runtime-default
decision, coercion of a completion-hook return into that contract, and the
``NEXT_ACTION_CHOSEN`` emitter.

**Record-first (Gap 4, PR D):** the runtime *records* the decision (emits
``NEXT_ACTION_CHOSEN``) and exposes the hook-return contract, but takes NO
autonomous action — the app orchestrator consumes the event and decides. This
lifts the app-side Infinity Phase 2 gate. A later PR may let the runtime act.

``NEXT_ACTION_CHOSEN`` deliberately does not carry the ``execution.`` prefix, so
it is not subject to the pipeline execution-contract gate. All functions here are
best-effort and never raise into the caller's completion path.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Canonical next-action verbs. This set is the cross-repo contract the app-side
# orchestrator matches on; extend additively (never renumber/rename).
DONE = "done"
RETRY = "retry"
ASK_USER = "ask_user"
ESCALATE = "escalate"
SCHEDULE_FOLLOW_UP = "schedule_follow_up"
CREATE_MEMORY = "create_memory"
RECOMMEND = "recommend"
TRIGGER_EXECUTION = "trigger_execution"

VALID_ACTIONS = frozenset(
    {DONE, RETRY, ASK_USER, ESCALATE, SCHEDULE_FOLLOW_UP, CREATE_MEMORY, RECOMMEND, TRIGGER_EXECUTION}
)

_SUCCESS_STATUSES = frozenset({"completed", "success", "verified", "executed"})
_FAILURE_STATUSES = frozenset({"failed", "error", "verify_failed", "dead_letter"})


def make_next_action(
    action: str,
    *,
    reason: str | None = None,
    args: dict[str, Any] | None = None,
    confidence: float | None = None,
    source: str = "runtime_default",
) -> dict[str, Any] | None:
    """Build a validated NextAction dict, or ``None`` for an unknown verb."""
    if action not in VALID_ACTIONS:
        return None
    out: dict[str, Any] = {
        "action": action,
        "reason": reason,
        "args": dict(args or {}),
        "source": source,
    }
    if confidence is not None:
        try:
            out["confidence"] = round(float(confidence), 4)
        except (TypeError, ValueError):
            pass
    return out


def coerce_next_action(value: Any, *, source: str = "completion_hook") -> dict[str, Any] | None:
    """Normalize a completion-hook return into a NextAction dict, or ``None``.

    Accepts a bare verb string, a dict with an ``action`` key, or any object
    exposing an ``action`` attribute. Unknown/False-y verbs yield ``None``.
    """
    if not value:
        return None
    if isinstance(value, str):
        return make_next_action(value.strip().lower(), source=source)
    if isinstance(value, dict):
        action = str(value.get("action") or "").strip().lower()
        return make_next_action(
            action,
            reason=value.get("reason"),
            args=value.get("args"),
            confidence=value.get("confidence"),
            source=source,
        )
    action_attr = getattr(value, "action", None)
    if action_attr:
        return make_next_action(
            str(action_attr).strip().lower(),
            reason=getattr(value, "reason", None),
            args=getattr(value, "args", None),
            confidence=getattr(value, "confidence", None),
            source=source,
        )
    return None


def select_hook_next_action(hook_results: Any) -> dict[str, Any] | None:
    """Pick the first completion-hook return that coerces to a valid NextAction."""
    if not hook_results:
        return None
    for result in hook_results:
        action = coerce_next_action(result)
        if action:
            return action
    return None


def default_next_action(
    *, status: str | None, result: Any = None, attempts_remaining: bool = False
) -> dict[str, Any]:
    """Runtime-default post-run decision when no hook supplies one."""
    normalized = (status or "").strip().lower()
    if normalized in _SUCCESS_STATUSES:
        return make_next_action(DONE, reason="run completed", source="runtime_default")
    if normalized in _FAILURE_STATUSES:
        if attempts_remaining:
            return make_next_action(
                RETRY, reason="run failed; retries remain", source="runtime_default"
            )
        return make_next_action(
            ESCALATE, reason="run failed; no retries remain", source="runtime_default"
        )
    return make_next_action(
        RECOMMEND, reason=f"non-terminal status={normalized}", source="runtime_default"
    )


def emit_next_action_chosen(
    *,
    db,
    run_id: str | None,
    next_action: dict[str, Any] | None,
    status: str | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    source: str = "agent",
    parent_event_id: str | None = None,
) -> str | None:
    """Emit one ``NEXT_ACTION_CHOSEN`` event for a finished execution.

    No-op when ``next_action`` is empty. Best-effort — never raises.
    """
    if not next_action:
        return None

    from AINDY.core.execution_signal_helper import queue_system_event
    from AINDY.core.system_event_types import SystemEventTypes

    payload: dict[str, Any] = {
        "run_id": str(run_id) if run_id else None,
        "status": status,
        "action": next_action.get("action"),
        "reason": next_action.get("reason"),
        "args": next_action.get("args") or {},
        "decision_source": next_action.get("source"),
    }
    if "confidence" in next_action:
        payload["confidence"] = next_action["confidence"]

    try:
        return queue_system_event(
            db=db,
            event_type=SystemEventTypes.NEXT_ACTION_CHOSEN,
            user_id=user_id,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
            source=source,
            payload=payload,
            required=False,
        )
    except Exception as exc:  # next-action recording must not break completion
        logger.warning("[NextAction] emit failed run=%s: %s", run_id, exc)
        return None
