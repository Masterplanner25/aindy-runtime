"""Outbound email for runtime-owned flows (FR-6 Phase A).

The runtime needs to send mail on its own behalf — password reset, and later address
verification. Two shapes were possible and the hybrid was chosen deliberately:

* **App-registered connector.** ``register_connector`` is a hook for *apps*; the runtime
  ships none. Depending on one would invert the split — a runtime-owned auth flow would
  need an app to register something, and password reset would be unavailable in any
  ``platform-only`` deployment, conflicting with the "runtime boots clean without plugins"
  contract.
* **Runtime-owned SMTP only.** Self-contained, but ignores an ``email`` connector an
  operator has already configured for app notifications, and would send their mail by a
  second route with different policy.

So: **use a registered ``email`` connector when one exists, otherwise fall back to
runtime-owned SMTP config.** Both routes go through the same capability
(``outbound.email``) and the same authorization stack, so enabling one does not buy weaker
enforcement than the other.

**A registered connector that fails is an error, not a reason to fall back.** Falling back
would route mail somewhere the operator did not intend, silently, precisely when their
intended channel is broken. The fallback exists for *absence*, not for failure.

**Availability is explicit.** ``email_channel_status()`` reports which route is live so
callers can fail closed with a clear 503 rather than accepting a request they cannot
fulfil. FR-6's ``/auth/password/forgot`` uses it for exactly that.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from AINDY.config import settings

logger = logging.getLogger(__name__)

EMAIL_CAPABILITY = "outbound.email"
#: The connector type runtime-owned transactional mail dispatches to.
#:
#: FR-9. This was ``"email"`` in 2.0.0, and that collided with the type apps register for
#: user-authored automations (``register_connector("email", ...)``). Registering one
#: silently opted an app into handling the runtime's password-reset and verification mail
#: too, in a *different* action shape, with no signal at registration time that it had
#: happened. Combined with the deliberate no-SMTP-fallback rule, an app-side shape mismatch
#: became "no account can complete signup": ``/auth/register`` returned 202, the connector
#: raised KeyError on a field it did not expect, and the only evidence anywhere was one
#: WARNING line.
#:
#: The two senders share nothing but the word "email" — one is user-configured per-action
#: egress, the other is auth-critical runtime mail — so they now have separate types. An
#: app that genuinely wants to carry transactional mail opts in explicitly by registering
#: this type; otherwise the runtime's own SMTP carries it, which is the hybrid the FR-6
#: decision called for.
CONNECTOR_TYPE = "transactional_email"

#: Secret name consulted through the broker before falling back to configured settings.
SMTP_PASSWORD_SECRET = "SMTP_PASSWORD"


def _smtp_configured() -> bool:
    return bool(getattr(settings, "AINDY_SMTP_HOST", "") and getattr(settings, "AINDY_SMTP_FROM", ""))


def _connector_registered() -> bool:
    try:
        from AINDY.platform_layer.registry import get_connector

        return get_connector(CONNECTOR_TYPE) is not None
    except Exception:  # registry unavailable — treat as absent, never raise from a status check
        return False


def email_channel_status() -> dict[str, Any]:
    """Which delivery route is live, without sending anything.

    Returns ``{"available": bool, "route": "connector"|"smtp"|None, "detail": str}``.
    Callers that cannot proceed without mail should fail closed on ``available`` being
    False rather than attempting a send and interpreting the failure.
    """
    if _connector_registered():
        return {
            "available": True,
            "route": "connector",
            "detail": f"registered '{CONNECTOR_TYPE}' connector",
        }
    if _smtp_configured():
        return {
            "available": True,
            "route": "smtp",
            "detail": f"runtime SMTP via {settings.AINDY_SMTP_HOST}",
        }
    return {
        "available": False,
        "route": None,
        "detail": (
            f"no '{CONNECTOR_TYPE}' connector registered and AINDY_SMTP_HOST/"
            "AINDY_SMTP_FROM not configured"
        ),
    }


def _resolve_smtp_password() -> str:
    """Prefer the broker; fall back to configured settings.

    The broker path keeps the credential just-in-time and out of the process environment
    where it is available, without making SMTP unusable for a deployment that has not set
    one up.
    """
    try:
        from AINDY.platform_layer.secret_broker import resolve_secret

        resolved = resolve_secret(
            SMTP_PASSWORD_SECRET,
            capabilities=[EMAIL_CAPABILITY],
            required_capability=EMAIL_CAPABILITY,
        )
        value = (resolved or {}).get("value")
        if value:
            return str(value)
    except Exception as exc:
        logger.debug("[email] secret broker unavailable for %s: %s", SMTP_PASSWORD_SECRET, exc)
    return str(getattr(settings, "AINDY_SMTP_PASSWORD", "") or "")


def _send_via_smtp(*, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.AINDY_SMTP_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    host = settings.AINDY_SMTP_HOST
    port = int(getattr(settings, "AINDY_SMTP_PORT", 587) or 587)
    user = getattr(settings, "AINDY_SMTP_USER", "") or ""
    password = _resolve_smtp_password()

    with smtplib.SMTP(host, port, timeout=20) as client:
        if getattr(settings, "AINDY_SMTP_STARTTLS", True):
            client.starttls()
        if user:
            client.login(user, password)
        client.send_message(message)


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    db: Any = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Send one message. Returns ``{"success", "route", "error"}`` and never raises.

    Never raising is deliberate: callers are auth endpoints whose response must not vary
    with delivery outcome. ``/auth/password/forgot`` returns 200 whether or not a mail was
    sent, so a raised exception would either leak through the response or have to be
    swallowed at every call site.
    """
    status = email_channel_status()
    if not status["available"]:
        logger.warning("[email] send skipped — %s", status["detail"])
        return {"success": False, "route": None, "error": status["detail"]}

    action = {"type": "send", "to": to, "subject": subject, "body": body}

    if status["route"] == "connector":
        from AINDY.platform_layer.connector_service import dispatch_connector

        # dispatch_connector applies the capability/policy/rate stack itself and returns a
        # normalized envelope without raising. A failure here is NOT a fallback trigger.
        result = dispatch_connector(CONNECTOR_TYPE, action, user_id=user_id, db=db)
        ok = bool(result.get("success"))
        if not ok:
            # FR-9 ask 3: this is auth-critical. A failure here means password resets and
            # verification mail are not being delivered while every endpoint still reports
            # success, so it is an error, not a warning, and it names the registered type
            # so the cause is not a log-grepping exercise.
            logger.error(
                "[email] registered '%s' connector FAILED and there is no SMTP fallback by "
                "design — transactional mail (verification, password reset) is NOT being "
                "delivered: %s",
                CONNECTOR_TYPE,
                result.get("error"),
            )
        return {"success": ok, "route": "connector", "error": result.get("error")}

    from AINDY.platform_layer.external_call_service import authorized_external_call

    try:
        authorized_external_call(
            service_name="smtp",
            capability=EMAIL_CAPABILITY,
            operation=lambda: _send_via_smtp(to=to, subject=subject, body=body),
            action=action,
            user_id=user_id,
            db=db,
            endpoint=str(getattr(settings, "AINDY_SMTP_HOST", "")),
            method="SMTP",
        )
        return {"success": True, "route": "smtp", "error": None}
    except Exception as exc:
        logger.warning("[email] SMTP send failed: %s", exc)
        return {"success": False, "route": "smtp", "error": str(exc)}


def log_email_channel_status() -> None:
    """Startup breadcrumb — an operator should learn at boot that mail is unconfigured,
    not when a locked-out user's reset silently does nothing."""
    status = email_channel_status()
    if status["available"]:
        logger.info("[email] outbound email available via %s", status["detail"])
    else:
        logger.warning(
            "[email] outbound email is NOT configured (%s). Password reset and any other "
            "runtime-sent mail will be unavailable and will fail closed.",
            status["detail"],
        )
