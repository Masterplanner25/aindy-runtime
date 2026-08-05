"""
FR-6 Phase A — the hybrid outbound email channel.

Route resolution is the whole point of this module, so the tests are weighted toward the
decisions rather than the happy path:

  * a registered `email` connector wins over configured SMTP;
  * SMTP is the fallback for the connector's **absence**;
  * a registered connector that FAILS must not fall back — falling back would route mail
    somewhere the operator did not intend, silently, exactly when their intended channel
    is broken;
  * with neither configured, the channel reports unavailable so callers can fail closed
    with an honest 503 instead of accepting a request they cannot fulfil.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from AINDY.platform_layer import email_channel as ec


pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _no_smtp_by_default(monkeypatch):
    """Default every test to 'nothing configured'; each opts in to what it needs."""
    monkeypatch.setattr(ec.settings, "AINDY_SMTP_HOST", "", raising=False)
    monkeypatch.setattr(ec.settings, "AINDY_SMTP_FROM", "", raising=False)


def _configure_smtp(monkeypatch):
    monkeypatch.setattr(ec.settings, "AINDY_SMTP_HOST", "smtp.example.test", raising=False)
    monkeypatch.setattr(ec.settings, "AINDY_SMTP_FROM", "noreply@example.test", raising=False)


def _with_connector(present: bool):
    return patch.object(ec, "_connector_registered", return_value=present)


# ── status ──────────────────────────────────────────────────────────────────

def test_unavailable_when_nothing_configured():
    with _with_connector(False):
        s = ec.email_channel_status()
    assert s["available"] is False
    assert s["route"] is None
    assert (
        "not configured" in s["detail"]
        or "no 'transactional_email' connector" in s["detail"]
    )


def test_smtp_alone_is_available(monkeypatch):
    _configure_smtp(monkeypatch)
    with _with_connector(False):
        s = ec.email_channel_status()
    assert s == {"available": True, "route": "smtp", "detail": s["detail"]}
    assert s["route"] == "smtp"


def test_connector_alone_is_available():
    with _with_connector(True):
        s = ec.email_channel_status()
    assert s["available"] is True and s["route"] == "connector"


def test_connector_takes_precedence_over_smtp(monkeypatch):
    """Hybrid means connector-first — an operator's configured channel wins."""
    _configure_smtp(monkeypatch)
    with _with_connector(True):
        assert ec.email_channel_status()["route"] == "connector"


def test_host_without_from_is_not_configured(monkeypatch):
    """A host with no sender address cannot send; treating it as configured would make
    the channel report available and then fail on every send."""
    monkeypatch.setattr(ec.settings, "AINDY_SMTP_HOST", "smtp.example.test", raising=False)
    monkeypatch.setattr(ec.settings, "AINDY_SMTP_FROM", "", raising=False)
    with _with_connector(False):
        assert ec.email_channel_status()["available"] is False


def test_status_never_raises_when_registry_is_broken():
    with patch("AINDY.platform_layer.registry.get_connector", side_effect=RuntimeError("boom")):
        assert ec.email_channel_status()["available"] is False


# ── send: routing ───────────────────────────────────────────────────────────

def _send():
    return ec.send_email(to="u@example.test", subject="s", body="b")


def test_send_uses_the_connector_when_registered():
    dispatch = MagicMock(return_value={"success": True, "result": {}, "error": None})
    with _with_connector(True), patch(
        "AINDY.platform_layer.connector_service.dispatch_connector", dispatch
    ):
        out = _send()
    assert out["success"] is True and out["route"] == "connector"
    # FR-9: the runtime dispatches its own reserved type, never the apps' "email".
    assert dispatch.call_args[0][0] == "transactional_email"


def test_send_falls_back_to_smtp_when_no_connector(monkeypatch):
    _configure_smtp(monkeypatch)
    called = {}

    def _fake_authorized(**kwargs):
        called.update(kwargs)
        return kwargs["operation"]()

    with _with_connector(False), patch(
        "AINDY.platform_layer.external_call_service.authorized_external_call", _fake_authorized
    ), patch.object(ec, "_send_via_smtp") as smtp:
        out = _send()

    assert out["success"] is True and out["route"] == "smtp"
    smtp.assert_called_once()
    assert called["capability"] == ec.EMAIL_CAPABILITY, "SMTP must go through the same capability"


def test_connector_failure_does_NOT_fall_back_to_smtp(monkeypatch):
    """The fallback exists for ABSENCE, not failure. Falling back here would send mail by
    a route the operator did not choose, precisely when their chosen route is broken."""
    _configure_smtp(monkeypatch)
    dispatch = MagicMock(return_value={"success": False, "result": None, "error": "relay down"})
    with _with_connector(True), patch(
        "AINDY.platform_layer.connector_service.dispatch_connector", dispatch
    ), patch.object(ec, "_send_via_smtp") as smtp:
        out = _send()

    assert out["success"] is False
    assert out["route"] == "connector"
    smtp.assert_not_called()


def test_send_reports_unavailable_rather_than_raising():
    with _with_connector(False):
        out = _send()
    assert out["success"] is False and out["route"] is None and out["error"]


def test_send_never_raises_when_smtp_errors(monkeypatch):
    """Callers are auth endpoints whose response must not vary with delivery outcome."""
    _configure_smtp(monkeypatch)
    with _with_connector(False), patch(
        "AINDY.platform_layer.external_call_service.authorized_external_call",
        side_effect=RuntimeError("connection refused"),
    ):
        out = _send()
    assert out["success"] is False and "connection refused" in out["error"]


# ── secret resolution ───────────────────────────────────────────────────────

def test_smtp_password_prefers_the_broker(monkeypatch):
    monkeypatch.setattr(ec.settings, "AINDY_SMTP_PASSWORD", "from-env", raising=False)
    with patch(
        "AINDY.platform_layer.secret_broker.resolve_secret",
        return_value={"value": "from-broker"},
    ):
        assert ec._resolve_smtp_password() == "from-broker"


def test_smtp_password_falls_back_to_settings_when_broker_has_none(monkeypatch):
    """A deployment without a broker must still be able to send."""
    monkeypatch.setattr(ec.settings, "AINDY_SMTP_PASSWORD", "from-env", raising=False)
    with patch(
        "AINDY.platform_layer.secret_broker.resolve_secret", side_effect=RuntimeError("no broker")
    ):
        assert ec._resolve_smtp_password() == "from-env"


# ── startup breadcrumb ──────────────────────────────────────────────────────

def test_startup_warns_when_unconfigured(caplog):
    with _with_connector(False):
        ec.log_email_channel_status()
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_startup_is_quiet_when_configured(caplog):
    with _with_connector(True):
        ec.log_email_channel_status()
    assert not any(r.levelname == "WARNING" for r in caplog.records)
