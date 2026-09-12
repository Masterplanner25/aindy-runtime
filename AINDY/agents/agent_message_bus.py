from __future__ import annotations

from typing import Any

from AINDY.core.execution_signal_helper import queue_system_event
from AINDY.db.models.system_event import SystemEvent
from AINDY.utils.uuid_utils import normalize_uuid


MESSAGE_TYPES = {
    "operation_request",
    "operation_result",
    "memory_share",
    "coordination_signal",
    # RTR-4 delegation handshake: the delegate's response to an operation_request.
    "operation_accept",
    "operation_reject",
}


def publish_message(
    *,
    db,
    message_type: str,
    sender_agent_id: str,
    recipient_agent_id: str | None = None,
    user_id: str | None = None,
    trace_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str | None:
    if message_type not in MESSAGE_TYPES:
        raise ValueError(f"Unsupported message_type: {message_type}")
    return str(
        queue_system_event(
            db=db,
            event_type=f"agent.message.{message_type}",
            user_id=user_id,
            trace_id=trace_id,
            source="coordination",
            agent_id=sender_agent_id,
            payload={
                "message_type": message_type,
                "sender_agent_id": sender_agent_id,
                "recipient_agent_id": recipient_agent_id,
                **(payload or {}),
            },
            required=True,
        )
    )


def publish_operation_request(
    *,
    db,
    sender_agent_id: str,
    recipient_agent_id: str,
    operation: dict[str, Any],
    user_id: str | None = None,
    trace_id: str | None = None,
) -> str | None:
    return publish_message(
        db=db,
        message_type="operation_request",
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        user_id=user_id,
        trace_id=trace_id,
        payload={"operation": operation},
    )


def publish_operation_result(
    *,
    db,
    sender_agent_id: str,
    recipient_agent_id: str | None,
    result: dict[str, Any],
    user_id: str | None = None,
    trace_id: str | None = None,
) -> str | None:
    return publish_message(
        db=db,
        message_type="operation_result",
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        user_id=user_id,
        trace_id=trace_id,
        payload={"result": result},
    )


def publish_operation_accept(
    *,
    db,
    sender_agent_id: str,
    recipient_agent_id: str | None,
    child_run_id: str,
    request_message_id: str | None = None,
    user_id: str | None = None,
    trace_id: str | None = None,
) -> str | None:
    """Delegate accepts a delegated operation — the child run may proceed (RTR-4)."""
    return publish_message(
        db=db,
        message_type="operation_accept",
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        user_id=user_id,
        trace_id=trace_id,
        payload={
            "child_run_id": str(child_run_id),
            "request_message_id": str(request_message_id) if request_message_id else None,
        },
    )


def publish_operation_reject(
    *,
    db,
    sender_agent_id: str,
    recipient_agent_id: str | None,
    child_run_id: str,
    reason: str | None = None,
    request_message_id: str | None = None,
    user_id: str | None = None,
    trace_id: str | None = None,
) -> str | None:
    """Delegate rejects a delegated operation — the child run is failed (RTR-4)."""
    return publish_message(
        db=db,
        message_type="operation_reject",
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        user_id=user_id,
        trace_id=trace_id,
        payload={
            "child_run_id": str(child_run_id),
            "reason": reason,
            "request_message_id": str(request_message_id) if request_message_id else None,
        },
    )


def publish_memory_share(
    *,
    db,
    sender_agent_id: str,
    recipient_agent_id: str | None,
    memory_node_id: str,
    user_id: str | None = None,
    trace_id: str | None = None,
) -> str | None:
    return publish_message(
        db=db,
        message_type="memory_share",
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        user_id=user_id,
        trace_id=trace_id,
        payload={"memory_node_id": memory_node_id},
    )


def get_inbox(
    db,
    *,
    agent_id: str,
    user_id: str | None = None,
    message_type: str | None = None,
    include_acknowledged: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = (
        db.query(SystemEvent)
        .filter(
            SystemEvent.type.in_(
                [
                    f"agent.message.{mt}"
                    for mt in ([message_type] if message_type else MESSAGE_TYPES)
                ]
            ),
        )
        .order_by(SystemEvent.timestamp.desc())
    )
    if user_id:
        query = query.filter(SystemEvent.user_id == normalize_uuid(user_id))
    query = query.limit(limit * 2)

    rows = query.all()
    agent_id_str = str(agent_id).lower()
    addressed = [
        row
        for row in rows
        if str((row.payload or {}).get("recipient_agent_id") or "").lower() == agent_id_str
    ]

    if not include_acknowledged:
        ack_query = (
            db.query(SystemEvent)
            .filter(
                SystemEvent.type == "agent.message.acknowledged",
                SystemEvent.agent_id.isnot(None),
            )
            .order_by(SystemEvent.timestamp.desc())
        )
        if user_id:
            ack_query = ack_query.filter(SystemEvent.user_id == normalize_uuid(user_id))
        ack_query = ack_query.limit(200)
        ack_rows = ack_query.all()
        acked_refs = {
            str((ack.payload or {}).get("acknowledged_message_id"))
            for ack in ack_rows
            if (ack.payload or {}).get("acknowledged_message_id")
        }
        addressed = [row for row in addressed if str(row.id) not in acked_refs]

    return [_serialize_inbox_message(row) for row in addressed[:limit]]


class MessageNotFoundError(Exception):
    """No message with this id exists (for this user), or the id is not a message at all."""


class MessageNotOwnedError(Exception):
    """The message exists but is addressed to a different agent than the one acknowledging it."""


def _message_event_types() -> list[str]:
    return [f"agent.message.{mt}" for mt in MESSAGE_TYPES]


def acknowledge_message(
    db,
    *,
    message_id: str,
    agent_id: str,
    user_id: str | None = None,
) -> str | None:
    """Acknowledge a message addressed to ``agent_id`` (FR-28: resolve → authorise → emit).

    Before FR-28 this emitted an ``agent.message.acknowledged`` event unconditionally — so it
    acknowledged a message that never existed (audit integrity) and, because ``get_inbox`` builds
    its suppression set from every ack for the user, let one agent acknowledge another agent's
    message by id and make it vanish from that agent's inbox. Both are closed here by refusing to
    emit until the message is resolved and the caller is shown to be its recipient.

    Raises :class:`MessageNotFoundError` when ``message_id`` is not a live message (absent,
    malformed, or not an ``agent.message.*`` type) for this user, and :class:`MessageNotOwnedError`
    when it is addressed to a different agent. Recipient comparison is case-insensitive, matching
    ``get_inbox``.
    """
    try:
        normalized_id = normalize_uuid(message_id)
    except (ValueError, AttributeError, TypeError):
        # A malformed id is not a message — treat it as absent rather than a 500. The route also
        # types the param as UUIDPath, but the bus defends for any non-route caller.
        raise MessageNotFoundError(f"Message {message_id!r} not found")

    query = db.query(SystemEvent).filter(
        SystemEvent.id == normalized_id,
        SystemEvent.type.in_(_message_event_types()),
    )
    if user_id:
        query = query.filter(SystemEvent.user_id == normalize_uuid(user_id))
    message = query.first()
    if message is None:
        raise MessageNotFoundError(f"Message {message_id!r} not found")

    recipient = str((message.payload or {}).get("recipient_agent_id") or "")
    if recipient.lower() != str(agent_id).lower():
        raise MessageNotOwnedError(
            f"Message {message_id!r} is not addressed to agent {agent_id!r}"
        )

    return str(
        queue_system_event(
            db=db,
            event_type="agent.message.acknowledged",
            user_id=user_id,
            trace_id=None,
            source="coordination",
            agent_id=agent_id,
            payload={
                "acknowledged_message_id": str(message_id),
                "agent_id": str(agent_id),
            },
            required=True,
        )
    )


def _serialize_inbox_message(row) -> dict[str, Any]:
    return {
        "message_id": str(row.id),
        "message_type": str(row.type).replace("agent.message.", ""),
        "sender_agent_id": str(row.agent_id) if row.agent_id else None,
        "recipient_agent_id": (row.payload or {}).get("recipient_agent_id"),
        "payload": row.payload or {},
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "trace_id": row.trace_id,
        "user_id": str(row.user_id) if row.user_id else None,
    }
