"""FR-9 — an app's `email` connector must not intercept runtime transactional mail.

In 2.0.0 the runtime dispatched password-reset and verification mail to the ``email``
connector type — the same type apps register for user-authored automations. Registering one
silently opted an app into carrying auth-critical mail in an action shape it had never been
told about. Because a registered-connector failure deliberately does **not** fall back to
SMTP, a shape mismatch meant ``/auth/register`` returned ``202`` while no verification mail
could ever be sent: registration looked healthy, and no account could complete signup.

These tests pin the separation and the action shape an opt-in handler can rely on.
"""
from unittest.mock import patch

import pytest

from AINDY.platform_layer import email_channel


@pytest.fixture
def registered(monkeypatch):
    """Record which connector types the channel looks up and dispatches to."""
    looked_up: list[str] = []

    def fake_get_connector(connector_type):
        looked_up.append(connector_type)
        return object() if connector_type == email_channel.CONNECTOR_TYPE else None

    monkeypatch.setattr(
        "AINDY.platform_layer.registry.get_connector", fake_get_connector, raising=False
    )
    return looked_up


def test_runtime_uses_a_type_distinct_from_the_apps_email_connector():
    """The whole fix in one assertion."""
    assert email_channel.CONNECTOR_TYPE == "transactional_email"
    assert email_channel.CONNECTOR_TYPE != "email"


def test_an_app_email_connector_is_never_consulted(monkeypatch):
    """An app registering "email" must not be handed transactional mail.

    Registry answers only for "email" — the pre-fix collision. The channel must not find a
    route through it, and must fall through to its own SMTP decision instead.
    """
    def only_app_email(connector_type):
        return object() if connector_type == "email" else None

    monkeypatch.setattr(
        "AINDY.platform_layer.registry.get_connector", only_app_email, raising=False
    )
    monkeypatch.setattr(email_channel, "_smtp_configured", lambda: False)

    status = email_channel.email_channel_status()
    assert status["route"] != "connector", "an app 'email' connector was intercepted"
    assert status["available"] is False


def test_dispatch_targets_the_reserved_type_with_the_documented_shape(registered):
    """The action shape is a published contract — pin it so it cannot drift silently."""
    captured = {}

    def dispatch(connector_type, action, **kwargs):
        captured["type"] = connector_type
        captured["action"] = action
        return {"success": True, "result": None, "error": None}

    with patch("AINDY.platform_layer.connector_service.dispatch_connector", dispatch):
        out = email_channel.send_email(to="a@b.test", subject="s", body="b")

    assert out["success"] is True
    assert captured["type"] == "transactional_email"
    assert captured["action"] == {
        "type": "send",
        "to": "a@b.test",
        "subject": "s",
        "body": "b",
    }


def test_connector_failure_is_logged_at_error_not_warning(registered, caplog):
    """FR-9 ask 3. Undelivered auth mail must not be a lone WARNING to grep for.

    The endpoints deliberately keep returning success, so the log is the only signal an
    operator gets — it has to be loud enough to reach them.
    """
    def failing(connector_type, action, **kwargs):
        return {"success": False, "result": None, "error": "'payload'"}

    with caplog.at_level("WARNING"), patch(
        "AINDY.platform_layer.connector_service.dispatch_connector", failing
    ):
        out = email_channel.send_email(to="a@b.test", subject="s", body="b")

    assert out["success"] is False
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors, "an undelivered transactional mail must log at ERROR"
    assert "transactional_email" in errors[0].getMessage()


def test_a_handler_that_raises_is_contained_end_to_end(monkeypatch):
    """The app's exact failure, through the REAL dispatch path — not a mock of it.

    Their handler opened with ``action["payload"]`` and raised KeyError on the runtime's
    shape. `send_email` promises never to raise, because auth endpoints must not vary their
    response with delivery outcome. That promise is *inherited* from `dispatch_connector`
    normalising handler exceptions rather than enforced by `send_email` itself, so mocking
    the dispatcher would test nothing. This registers a genuinely broken handler and drives
    the real path.
    """
    from AINDY.platform_layer.registry import register_connector

    def broken_handler(action, ctx):
        return {"delivered": action["payload"]}  # KeyError: the reported app-side bug

    register_connector(
        email_channel.CONNECTOR_TYPE, broken_handler, overwrite=True
    )

    result = email_channel.send_email(to="a@b.test", subject="s", body="b")

    assert result["success"] is False, "a broken handler must not report success"
    assert result["route"] == "connector"
    assert "payload" in str(result["error"])
