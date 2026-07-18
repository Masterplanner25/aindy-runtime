from __future__ import annotations

import contextlib
import logging
import time
from typing import Any, Callable

from AINDY.core.system_event_service import emit_error_event, emit_system_event
from AINDY.platform_layer.trace_context import get_current_trace_id

logger = logging.getLogger(__name__)


class OutboundCallDenied(Exception):
    """Raised when a capability-enforced outbound call is denied before it executes.

    Distinct from a transport failure (which ``perform_external_call`` raises from the
    operation itself): this fires *before* the network op, when a recipient/domain
    allowlist or rate limit denies the call. Carries the structured violation so the
    caller (e.g. a connector) can surface a precise error.
    """

    def __init__(self, message: str, *, violation: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.violation = violation or {}


def external_metadata(
    *,
    service_name: str,
    endpoint: str | None = None,
    model: str | None = None,
    method: str | None = None,
    status: str | None = None,
    latency_ms: float | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "service_name": service_name,
        "endpoint": endpoint,
        "model": model,
        "method": method,
        "status": status,
        "latency_ms": latency_ms,
        "error": error,
    }
    if extra:
        payload.update(extra)
    return payload


def perform_external_call(
    *,
    service_name: str,
    operation: Callable[[], Any],
    db=None,
    user_id=None,
    endpoint: str | None = None,
    model: str | None = None,
    method: str | None = None,
    extra: dict[str, Any] | None = None,
):
    from AINDY.db.database import SessionLocal

    owned_db = db is None
    active_db = db or SessionLocal()
    trace_id = get_current_trace_id()
    started_payload = external_metadata(
        service_name=service_name,
        endpoint=endpoint,
        model=model,
        method=method,
        status="started",
        extra=extra,
    )
    emit_system_event(
        db=active_db,
        event_type="external.call.started",
        user_id=user_id,
        trace_id=trace_id,
        payload=started_payload,
        required=True,
    )

    started_at = time.perf_counter()
    try:
        result = operation()
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        completed_payload = external_metadata(
            service_name=service_name,
            endpoint=endpoint,
            model=model,
            method=method,
            status="success",
            latency_ms=latency_ms,
            extra=extra,
        )
        emit_system_event(
            db=active_db,
            event_type="external.call.completed",
            user_id=user_id,
            trace_id=trace_id,
            payload=completed_payload,
            required=True,
        )
        return result
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        failed_payload = external_metadata(
            service_name=service_name,
            endpoint=endpoint,
            model=model,
            method=method,
            status="failure",
            latency_ms=latency_ms,
            error=str(exc),
            extra=extra,
        )
        emit_system_event(
            db=active_db,
            event_type="external.call.failed",
            user_id=user_id,
            trace_id=trace_id,
            payload=failed_payload,
            required=True,
        )
        emit_error_event(
            db=active_db,
            error_type="external_call",
            message=str(exc),
            user_id=user_id,
            trace_id=trace_id,
            payload=failed_payload,
            required=True,
        )
        raise
    finally:
        if owned_db:
            active_db.close()


def _egress_domains_for(capability: str, extra_domains: Any) -> set[str]:
    """Union of a capability's policy-declared egress domains and any explicit ones."""
    domains: set[str] = set(str(d).lower() for d in extra_domains) if extra_domains else set()
    try:
        from AINDY.agents.capability_policy import get_capability_policy

        policy = get_capability_policy(capability)
        if policy is not None and policy.domains:
            domains.update(str(d).lower() for d in policy.domains)
    except Exception:  # capability layer optional / import-time safe
        pass
    return domains


def authorized_external_call(
    *,
    service_name: str,
    capability: str,
    operation: Callable[[], Any],
    action: Any = None,
    user_id: str | None = None,
    db=None,
    endpoint: str | None = None,
    method: str | None = None,
    domains: Any = None,
    extra: dict[str, Any] | None = None,
):
    """Run an outbound call gated by the caller's capability (FR-1).

    The enforced counterpart to :func:`perform_external_call`. It layers the same
    authorization stack ``execute_tool`` applies to agent tools onto an arbitrary
    outbound operation, keyed on a single ``capability``:

    1. **Recipient / domain allowlist** — ``enforce_capability_policy([capability], action)``
       denies the call when the action carries an email/host outside the capability's
       ``CapabilityPolicy`` allowlist. Vacuous until a policy is registered.
    2. **Rate limit** — ``enforce_capability_rate`` records one hit against the
       capability × ``user_id`` fixed-window counter; over-limit denies. Only reached for
       otherwise-permitted calls, so the count reflects real usage.
    3. **Socket-level egress guard** — for the duration of ``operation`` the process's
       DNS resolution is pinned to the capability's domain allowlist (when
       ``AINDY_EGRESS_ENFORCEMENT`` is on), catching runtime-built URLs that step 1's
       static arg inspection misses.
    4. **JIT credential vaulting** — ``operation`` runs inside ``capability_scope`` so a
       ``resolve_secret(name)`` call inside it is gated by this capability and the secret
       is never persisted or returned in the envelope.
    5. **Observability** — the actual op is wrapped in :func:`perform_external_call`
       (``external.call.started|completed|failed`` events + timing).

    Denials (steps 1–2) raise :class:`OutboundCallDenied` *before* any network I/O.
    On success the ``operation`` result is returned unchanged.
    """
    caps = [capability]

    # 1 + 2 — declarative policy (only when any policy is registered; otherwise vacuous).
    try:
        from AINDY.agents.capability_policy import (
            enforce_capability_policy,
            enforce_capability_rate,
            has_capability_policies,
        )

        policies_active = has_capability_policies()
    except Exception:
        policies_active = False

    if policies_active:
        policy_result = enforce_capability_policy(caps, action if action is not None else {})
        if not policy_result["allowed"]:
            first = policy_result["violations"][0]
            raise OutboundCallDenied(
                f"outbound {service_name!r} denied: {first['kind']} {first['value']!r} "
                f"not allowed by capability {capability!r}",
                violation=first,
            )
        rate_result = enforce_capability_rate(caps, scope=str(user_id or "anonymous"))
        if not rate_result["allowed"]:
            first = rate_result["violations"][0]
            raise OutboundCallDenied(
                f"outbound {service_name!r} denied: capability {capability!r} rate limit "
                f"exceeded ({first['limit']}/{first['window_secs']}s)",
                violation=first,
            )

    # 3 — socket-level egress allowlist (inert unless enforcement on and domains present).
    egress_domains = _egress_domains_for(capability, domains)
    egress_cm: Any = contextlib.nullcontext()
    if egress_domains:
        try:
            from AINDY.platform_layer.egress_guard import (
                egress_enforcement_enabled,
                egress_scope,
                install_egress_guard,
            )

            if egress_enforcement_enabled():
                install_egress_guard()
                egress_cm = egress_scope(egress_domains)
        except Exception:  # egress guard is best-effort hardening
            egress_cm = contextlib.nullcontext()

    # 4 + 5 — credential scope + observability around the real operation.
    from AINDY.platform_layer.secret_broker import capability_scope

    with egress_cm, capability_scope(caps):
        return perform_external_call(
            service_name=service_name,
            operation=operation,
            db=db,
            user_id=user_id,
            endpoint=endpoint,
            method=method,
            extra=extra,
        )

