"""Queue side effects from inside a request pipeline (EVENT-OUTBOX-1 for the event half).

★ Inside a pipeline a queued SYSTEM EVENT is written on the HANDLER'S SESSION without a commit
(DEC-060). It rides whatever the handler commits next — the FR-30 execution-unit finalize is the
last commit of every request — and rolls back with the handler's work if that work rolls back
(DEC-062). Before, the event was a dict in an in-memory bucket, written AFTER the handler on its
own commit under a swallowing `try`: a crash between the handler's commit and that flush kept
the work and lost the record. The post-handler pass (`_apply_event_signals`) now runs only the
DERIVED effects for a persisted entry. The id is client-assigned either way (DEC-061).

The non-pipeline branch is untouched: it already wrote on the caller's session and committed.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from AINDY.platform_layer.trace_context import get_current_execution_context, is_pipeline_active

logger = logging.getLogger(__name__)


def _ensure_signal_bucket(ctx: Any) -> dict[str, list[dict[str, Any]]]:
    queued = ctx.metadata.setdefault("queued_execution_signals", {})
    queued.setdefault("events", [])
    queued.setdefault("memory", [])
    return queued


def queue_system_event(
    *,
    db,
    event_type: str,
    user_id=None,
    trace_id: str | None = None,
    parent_event_id=None,
    source: str | None = None,
    agent_id=None,
    payload: dict[str, Any] | None = None,
    required: bool = False,
):
    ctx = get_current_execution_context()
    if is_pipeline_active() and ctx is not None:
        queued = _ensure_signal_bucket(ctx)
        entry = {
            "id": str(uuid.uuid4()),
            "type": event_type,
            "event_type": event_type,
            "payload": dict(payload or {}),
            "parent_event_id": str(parent_event_id) if parent_event_id else None,
            "source": source,
            "agent_id": str(agent_id) if agent_id else None,
            "required": required,
            "trace_id": str(trace_id) if trace_id else None,
            "user_id": str(user_id) if user_id else None,
        }
        session = db if db is not None else ctx.metadata.get("db")
        if session is not None:
            try:
                entry["id"] = str(
                    _persist_on_session(
                        session,
                        event_type=event_type,
                        user_id=user_id if user_id is not None else ctx.user_id,
                        trace_id=trace_id or ctx.request_id,
                        parent_event_id=parent_event_id,
                        source=source or ctx.metadata.get("source") or ctx.route_name,
                        agent_id=agent_id,
                        payload=payload,
                    )
                )
                entry["persisted"] = True
            except Exception as exc:  # noqa: BLE001 — see below
                # The row could not be added to the handler's session. Do NOT roll the session
                # back here — it is the handler's, and rolling it back mid-handler discards its
                # pending work (RT-MEMTXN-LEAK-1's rule). Fall back to the buffered post-handler
                # emit, which is exactly what every event got before this change; a required
                # event in fail-closed mode raises, as `emit_system_event` would.
                logger.warning(
                    "[SystemEvent] could not add %s to the handler's session (%s); deferring to the "
                    "post-handler flush",
                    event_type, exc,
                )
                if required:
                    from AINDY.core.system_event_service import SystemEventEmissionError, _fail_closed_in_current_mode

                    if _fail_closed_in_current_mode():
                        raise SystemEventEmissionError(
                            f"Required system event '{event_type}' could not be written"
                        ) from exc
        queued["events"].append(entry)
        return entry["id"]

    from AINDY.core.system_event_service import emit_system_event

    return emit_system_event(
        db=db,
        event_type=event_type,
        user_id=user_id,
        trace_id=trace_id,
        parent_event_id=parent_event_id,
        source=source,
        agent_id=agent_id,
        payload=payload,
        required=required,
    )


def _persist_on_session(
    db,
    *,
    event_type: str,
    user_id,
    trace_id,
    parent_event_id,
    source,
    agent_id,
    payload,
):
    """Add + flush the row on ``db``; never commit. The handler decides when (DEC-060)."""
    from AINDY.core.system_event_service import _persist_system_event

    return _persist_system_event(
        db=db,
        event_type=event_type,
        user_id=user_id,
        trace_id=trace_id,
        parent_event_id=parent_event_id,
        source=source,
        agent_id=agent_id,
        payload=payload,
        commit=False,
    )


def queue_memory_capture(
    *,
    db,
    user_id,
    agent_namespace: str,
    event_type: str,
    content: str,
    source: str,
    tags: list[str] | None = None,
    node_type: str | None = None,
    context: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    force: bool = False,
    allow_when_pipeline_active: bool = False,
):
    ctx = get_current_execution_context()
    ctx_metadata = getattr(ctx, "metadata", {}) or {}
    disable_flag = bool(ctx_metadata.get("disable_memory_capture"))
    if disable_flag:
        return {
            "skipped": True,
            "event_type": event_type,
            "source": source,
        }
    if is_pipeline_active() and ctx is not None and not allow_when_pipeline_active:
        queued = _ensure_signal_bucket(ctx)
        queued["memory"].append(
            {
                "event_type": event_type,
                "content": content,
                "source": source,
                "tags": list(tags or []),
                "node_type": node_type,
                "extra": dict(extra or {}),
                "force": force,
                "user_id": str(user_id) if user_id else None,
                "agent_namespace": agent_namespace,
                "context": dict(context or {}),
            }
        )
        return {
            "queued": True,
            "event_type": event_type,
            "content": content,
            "source": source,
        }

    from AINDY.memory.memory_capture_engine import MemoryCaptureEngine

    engine = MemoryCaptureEngine(
        db=db,
        user_id=str(user_id) if user_id else None,
        agent_namespace=agent_namespace,
    )
    return engine.evaluate_and_capture(
        event_type=event_type,
        content=content,
        source=source,
        tags=tags,
        node_type=node_type,
        context={**(context or {}), **({"disable_memory_capture": True} if disable_flag else {})},
        extra=extra,
        force=force,
        allow_when_pipeline_active=True,
    )


def record_agent_event(*args, **kwargs):
    from AINDY.agents.agent_event_service import emit_event

    return emit_event(*args, **kwargs)


