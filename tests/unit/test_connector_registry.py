"""FR-1 — connector registration hook + capability-enforced outbound boundary.

Covers registration (``register_connector`` / ``get_connector`` / ``iter_connectors`` +
validation + overwrite guard + default capability), the ``dispatch_connector`` envelope
(missing / success / handler error / denial), and the ``authorized_external_call``
enforcement stack (domain allowlist, rate limit, JIT credential scope, passthrough). No
database — ``perform_external_call`` is patched to run the operation directly, isolating the
enforcement layer from observability.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from AINDY.agents.capability_policy import (
    CapabilityPolicy,
    clear_capability_policies,
    register_capability_policy,
)
from AINDY.platform_layer import registry
from AINDY.platform_layer.connector_service import ConnectorContext, dispatch_connector
from AINDY.platform_layer.external_call_service import (
    OutboundCallDenied,
    authorized_external_call,
)
from AINDY.platform_layer.registry import (
    get_connector,
    iter_connectors,
    register_connector,
)
from AINDY.platform_layer.secret_broker import (
    clear_secret_scopes,
    register_secret_scope,
    resolve_secret,
    set_secret_broker,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _clean():
    registry._connectors.clear()
    clear_capability_policies()
    clear_secret_scopes()
    set_secret_broker(None)
    yield
    registry._connectors.clear()
    clear_capability_policies()
    clear_secret_scopes()
    set_secret_broker(None)


def _passthrough():
    """Patch perform_external_call to just run the operation (no DB / no events)."""

    def _fake(*, service_name, operation, **kwargs):
        return operation()

    return patch(
        "AINDY.platform_layer.external_call_service.perform_external_call",
        side_effect=_fake,
    )


# ── Registration ──────────────────────────────────────────────────────────────

def test_register_and_get():
    def handler(action, ctx):
        return {"sent": True}

    register_connector("email", handler, capability="outbound.email", description="SMTP")
    entry = get_connector("email")
    assert entry["handler"] is handler
    assert entry["capability"] == "outbound.email"
    assert entry["description"] == "SMTP"
    assert ("email", entry) in iter_connectors()


def test_default_capability_derived_from_type():
    register_connector("webhook", lambda action, ctx: None)
    assert get_connector("webhook")["capability"] == "outbound.webhook"


def test_get_unknown_returns_none():
    assert get_connector("nope") is None


def test_overwrite_guard():
    register_connector("crm", lambda action, ctx: 1)
    with pytest.raises(ValueError, match="already registered"):
        register_connector("crm", lambda action, ctx: 2)
    # overwrite=True replaces it
    register_connector("crm", lambda action, ctx: 2, overwrite=True)
    assert get_connector("crm")["handler"]({}, None) == 2


def test_validation_rejects_non_callable():
    with pytest.raises(ValueError):
        register_connector("bad", "not-callable")


def test_validation_accepts_keyword_only_handler():
    def handler(*, action, ctx):
        return "ok"

    register_connector("kw", handler)
    assert get_connector("kw") is not None


# ── Dispatch envelope ─────────────────────────────────────────────────────────

def test_dispatch_missing_connector():
    res = dispatch_connector("ghost", {"x": 1})
    assert res["success"] is False
    assert "not registered" in res["error"]


def test_dispatch_success_passes_action_and_ctx():
    seen = {}

    def handler(action, ctx):
        seen["ctx"] = ctx
        return {"delivered": action["to"]}

    register_connector("email", handler)
    res = dispatch_connector("email", {"to": "x@y.com"}, user_id="u1")
    assert res == {"success": True, "result": {"delivered": "x@y.com"}, "error": None}
    assert isinstance(seen["ctx"], ConnectorContext)
    assert seen["ctx"].connector_type == "email"
    assert seen["ctx"].capability == "outbound.email"
    assert seen["ctx"].user_id == "u1"


def test_dispatch_handler_exception_becomes_envelope():
    def handler(action, ctx):
        raise RuntimeError("boom")

    register_connector("email", handler)
    res = dispatch_connector("email", {})
    assert res["success"] is False
    assert "boom" in res["error"]
    assert "denied" not in res


def test_dispatch_denial_marks_denied():
    register_capability_policy("outbound.webhook", CapabilityPolicy(domains=("allowed.com",)))

    def handler(action, ctx):
        return ctx.call(
            service_name="webhook",
            operation=lambda: "SENT",
            action={"url": "https://evil.com/hook"},
        )

    register_connector("webhook", handler)
    with _passthrough():
        res = dispatch_connector("webhook", {})
    assert res["success"] is False
    assert res["denied"] is True
    assert "evil.com" in res["error"]


# ── authorized_external_call enforcement ──────────────────────────────────────

def test_no_policy_is_vacuous_passthrough():
    with _passthrough():
        result = authorized_external_call(
            service_name="webhook",
            capability="outbound.webhook",
            operation=lambda: "SENT",
            action={"url": "https://anywhere.example/hook"},
        )
    assert result == "SENT"


def test_domain_allowlist_denies_outside_host():
    register_capability_policy("outbound.webhook", CapabilityPolicy(domains=("allowed.com",)))
    ran = {"op": False}

    def op():
        ran["op"] = True
        return "SENT"

    with _passthrough():
        with pytest.raises(OutboundCallDenied) as ei:
            authorized_external_call(
                service_name="webhook",
                capability="outbound.webhook",
                operation=op,
                action={"url": "https://evil.com/hook"},
            )
    assert ei.value.violation["kind"] == "domain"
    assert ran["op"] is False  # denied before the network op


def test_domain_allowlist_allows_listed_host():
    register_capability_policy("outbound.webhook", CapabilityPolicy(domains=("allowed.com",)))
    with _passthrough():
        result = authorized_external_call(
            service_name="webhook",
            capability="outbound.webhook",
            operation=lambda: "SENT",
            action={"url": "https://api.allowed.com/hook"},
        )
    assert result == "SENT"


def test_rate_limit_denies_second_call():
    register_capability_policy("outbound.sms", CapabilityPolicy(rate="1/minute"))
    with _passthrough():
        first = authorized_external_call(
            service_name="sms",
            capability="outbound.sms",
            operation=lambda: "SENT",
            user_id="rate-user",
            action={},
        )
        assert first == "SENT"
        with pytest.raises(OutboundCallDenied) as ei:
            authorized_external_call(
                service_name="sms",
                capability="outbound.sms",
                operation=lambda: "SENT",
                user_id="rate-user",
                action={},
            )
    assert ei.value.violation["kind"] == "rate"


def test_credential_scope_active_inside_operation(monkeypatch):
    register_secret_scope("stripe_key", "outbound.stripe")
    monkeypatch.setenv("AINDY_SECRET_STRIPE_KEY", "sk_live_abc")
    captured = {}

    def op():
        # ambient capability_scope is [outbound.stripe] → resolution permitted
        captured["allowed"] = resolve_secret("stripe_key")
        return "charged"

    with _passthrough():
        result = authorized_external_call(
            service_name="stripe",
            capability="outbound.stripe",
            operation=op,
            action={},
        )
    assert result == "charged"
    assert captured["allowed"] == {"ok": True, "value": "sk_live_abc"}


def test_credential_scope_denies_wrong_capability(monkeypatch):
    register_secret_scope("stripe_key", "outbound.stripe")
    monkeypatch.setenv("AINDY_SECRET_STRIPE_KEY", "sk_live_abc")
    captured = {}

    def op():
        captured["denied"] = resolve_secret("stripe_key")
        return "ok"

    with _passthrough():
        authorized_external_call(
            service_name="webhook",
            capability="outbound.webhook",  # not outbound.stripe
            operation=op,
            action={},
        )
    assert captured["denied"]["ok"] is False
    assert "requires capability" in captured["denied"]["error"]
