"""FR-53 (b) — a payload ``user_id`` may only repeat the caller's identity, never choose it.

``dispatch_syscall`` authenticates the CALLER (Step 2b: the context must carry a user id) but
never compares that identity with a ``user_id`` key in the payload. Handlers written for
in-process callers read the payload's — the app has dozens that do. That is safe while the
payload is written by trusted in-process code and unsafe wherever it is written by someone
else: an outside AI over MCP, a Nodus script calling ``sys()``, an HTTP client. There the
payload's ``user_id`` is an ASSERTION, and an assertion may only constrain, never select
(``INITIATOR-IDENTITY-1``'s rule; ``syscall_registry._resolve_tenant_user_id`` already applies it
to runtime handlers).

Each transport that dispatches an outside-written payload calls :func:`bind_payload_user_id`
before dispatch (DEC-099). The dispatcher refuses the same mismatch for in-process callers
(DEC-103), after counting it first read zero (DEC-100); ``AINDY_PAYLOAD_IDENTITY_DISPATCH=observe``
restores count-only there.
"""
from __future__ import annotations

from typing import Any

PAYLOAD_USER_KEY = "user_id"

#: Syscalls that hand part of their payload on to code that runs later: a job handler, or a flow
#: node reading state. The same caller writes that part as the rest of the payload, so its
#: `user_id` is held to the same rule. Only the named key is checked, one level down.
FORWARDED_PAYLOAD_KEYS = {
    "sys.v1.job.submit": "payload",
    "sys.v1.flow.run": "initial_state",
    "sys.v1.flow.execute_intent": "intent_data",
    "sys.v1.nodus.execute": "input_payload",
}


class PayloadIdentityViolation(PermissionError):
    """A payload named a user other than the authenticated caller."""


def _normalize(value: Any) -> str:
    from AINDY.platform_layer.user_ids import parse_user_id

    parsed = parse_user_id(value)
    if parsed is not None:
        return str(parsed)
    return str(value).strip()


def payload_user_id_mismatch(payload: Any, user_id: Any) -> bool:
    """True when ``payload`` carries a ``user_id`` that is not ``user_id``.

    An absent or ``None``/empty payload key is not a mismatch. A UUID matches in any spelling
    (case, braces, hyphens); anything else compares as a stripped string.
    """
    if not isinstance(payload, dict):
        return False
    requested = payload.get(PAYLOAD_USER_KEY)
    if requested is None or requested == "":
        return False
    return _normalize(requested) != _normalize(user_id)


def forwarded_user_id_mismatch(syscall: str, payload: Any, user_id: Any) -> bool:
    """True when the part of ``payload`` that ``syscall`` forwards names another user."""
    key = FORWARDED_PAYLOAD_KEYS.get(syscall)
    if key is None or not isinstance(payload, dict):
        return False
    return payload_user_id_mismatch(payload.get(key), user_id)


def bind_payload_user_id(payload: Any, user_id: Any, *, syscall: str = "") -> dict[str, Any]:
    """Return a copy of ``payload`` whose ``user_id`` is the caller's.

    Fills the key when it is absent (handlers that require ``payload["user_id"]`` keep working
    over every transport; input schemas ignore undeclared keys). Raises
    :class:`PayloadIdentityViolation` when the payload names anyone else — refused, never
    silently overwritten, so the caller learns its assertion was wrong.
    """
    bound = dict(payload) if isinstance(payload, dict) else {}
    if payload_user_id_mismatch(bound, user_id) or forwarded_user_id_mismatch(syscall, bound, user_id):
        target = f" for {syscall!r}" if syscall else ""
        raise PayloadIdentityViolation(
            f"TENANT_VIOLATION: a payload user_id{target} does not "
            f"match the authenticated caller; a payload may not choose the user it acts on"
        )
    if bound.get(PAYLOAD_USER_KEY) in (None, ""):
        bound[PAYLOAD_USER_KEY] = str(user_id)
    return bound
