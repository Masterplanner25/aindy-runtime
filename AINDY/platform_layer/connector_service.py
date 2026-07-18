"""
platform_layer/connector_service.py - Runtime dispatch for outbound connectors (FR-1).

Apps register outbound integrations (``social`` / ``crm`` / ``email`` / ``webhook`` /
``stripe`` / …) via ``registry.register_connector`` instead of a hardcoded app-side
``if/elif`` ladder. ``dispatch_connector`` is the runtime entry point that:

  - resolves the registered handler for a connector type,
  - runs it under the connector capability's authorization scope (socket-level egress
    allowlist + JIT credential vaulting, both opt-in and vacuous until configured), and
  - hands the handler a :class:`ConnectorContext` carrying a pre-bound ``call`` helper —
    :func:`authorized_external_call` scoped to this connector's capability — so a
    per-call recipient/domain allowlist, rate limit, and observability apply to every
    outbound request the connector makes.

Enforcement composes the existing primitives (``CapabilityPolicy``, ``SecretBroker``,
``egress_guard``); registering a connector changes dispatch routing only — behavior is
unchanged until an operator registers a policy / secret scope / enables egress. This is
the FR-1 counterpart to the ``execute_tool`` agent-tool boundary.
"""
from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ConnectorContext:
    """Per-dispatch context handed to a connector handler.

    ``call(...)`` is :func:`authorized_external_call` pre-bound to this connector's
    ``capability``, ``user_id``, and ``db`` — a connector performs outbound I/O through it
    to get the recipient/domain allowlist, rate limit, egress guard, credential scope, and
    observability for free. A connector may also ``resolve_secret(name)`` directly (it is
    gated by the same capability via the ambient scope this dispatch installs).
    """

    connector_type: str
    capability: str
    user_id: str | None = None
    db: Any = None
    metadata: dict[str, Any] | None = None

    def call(
        self,
        *,
        service_name: str | None = None,
        operation: Callable[[], Any],
        action: Any = None,
        endpoint: str | None = None,
        method: str | None = None,
        domains: Any = None,
        extra: dict[str, Any] | None = None,
    ):
        """Run ``operation`` as an authorized outbound call scoped to this connector."""
        from AINDY.platform_layer.external_call_service import authorized_external_call

        return authorized_external_call(
            service_name=service_name or self.connector_type,
            capability=self.capability,
            operation=operation,
            action=action,
            user_id=self.user_id,
            db=self.db,
            endpoint=endpoint,
            method=method,
            domains=domains,
            extra=extra,
        )


def dispatch_connector(
    connector_type: str,
    action: dict,
    *,
    user_id: str | None = None,
    db=None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch ``action`` to the registered handler for ``connector_type``.

    Returns a normalized ``{"success", "result", "error"}`` envelope (``"denied": True``
    is added when the failure was a capability/policy/rate denial rather than a transport
    error). Never raises — a missing connector, a denial, or a handler exception all map
    to ``success=False``.
    """
    from AINDY.platform_layer.registry import get_connector

    entry = get_connector(connector_type)
    if entry is None:
        return {
            "success": False,
            "result": None,
            "error": f"connector {connector_type!r} is not registered",
        }

    capability = entry["capability"]
    handler = entry["handler"]
    ctx = ConnectorContext(
        connector_type=str(connector_type),
        capability=capability,
        user_id=user_id,
        db=db,
        metadata=metadata or {},
    )

    # Defense in depth: pin egress + capability scope around the WHOLE handler, so a
    # connector that bypasses ctx.call and issues raw urllib/smtplib I/O still hits the
    # socket-level allowlist and can still resolve secrets. authorized_external_call
    # re-applies both per call (nested scopes are safe).
    from AINDY.platform_layer.external_call_service import _egress_domains_for
    from AINDY.platform_layer.secret_broker import capability_scope

    egress_domains = _egress_domains_for(capability, None)
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
        except Exception:
            egress_cm = contextlib.nullcontext()

    try:
        from AINDY.platform_layer.external_call_service import OutboundCallDenied

        with egress_cm, capability_scope([capability]):
            result = handler(action, ctx)
        return {"success": True, "result": result, "error": None}
    except OutboundCallDenied as denied:
        logger.warning("[connector:%s] outbound denied: %s", connector_type, denied)
        return {
            "success": False,
            "result": None,
            "error": str(denied),
            "denied": True,
        }
    except Exception as exc:
        logger.warning("[connector:%s] handler failed: %s", connector_type, exc)
        return {"success": False, "result": None, "error": str(exc)}
