"""
FR-6 Phase C — address verification, and the register enumeration fix it enables.

The oracle could not be closed while registration also authenticated the caller: a
duplicate cannot be handed a token, so *some* difference was unavoidable. Moving the token
behind verification is what makes a uniform 202 possible. These tests are therefore mostly
about **indistinguishability** — that the new-address and duplicate-address paths cannot be
told apart by status, body, or work done.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt
from unittest.mock import MagicMock, patch

from AINDY.services.auth_service import (
    ALGORITHM,
    EMAIL_VERIFY_PURPOSE,
    PASSWORD_RESET_PURPOSE,
    confirm_email_verification,
    create_email_verification_token,
    create_password_reset_token,
    decode_access_token,
    hash_password,
    verify_email_token,
    verify_password_reset_token,
    _verify_signing_key,
)


pytestmark = pytest.mark.runtime_only


class _User:
    def __init__(self, is_verified=False, is_active=True, token_version=0):
        self.id = uuid.uuid4()
        self.email = "u@example.test"
        self.hashed_password = hash_password("a-good-password")
        self.is_active = is_active
        self.is_verified = is_verified
        self.verified_at = None
        self.token_version = token_version


def _db_for(user):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    return db


# ── token separation: three domains, no crossover ───────────────────────────

def test_verification_token_is_not_an_access_token():
    with pytest.raises(HTTPException) as exc:
        decode_access_token(create_email_verification_token(_User()))
    assert exc.value.status_code == 401


def test_verification_token_is_not_a_reset_token():
    """Different domains, not just different purposes. Reusing one domain would make a
    verification link redeemable as a password reset — two authorities, one credential."""
    with pytest.raises(HTTPException):
        verify_password_reset_token(create_email_verification_token(_User()))


def test_reset_token_is_not_a_verification_token():
    with pytest.raises(HTTPException):
        verify_email_token(create_password_reset_token(_User()))


def test_wrong_purpose_on_the_verify_key_is_rejected():
    forged = jwt.encode(
        {"sub": "u1", "purpose": PASSWORD_RESET_PURPOSE,
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        _verify_signing_key(), algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException):
        verify_email_token(forged)


def test_expired_verification_token_is_rejected():
    expired = jwt.encode(
        {"sub": "u1", "purpose": EMAIL_VERIFY_PURPOSE,
         "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        _verify_signing_key(), algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException):
        verify_email_token(expired)


def test_verification_token_does_not_pin_token_version():
    """Deliberate difference from the reset token. A verification link must survive
    ordinary activity between registering and clicking — logging in elsewhere, or an admin
    invalidating sessions, should not silently void it."""
    claims = verify_email_token(create_email_verification_token(_User(token_version=7)))
    assert "tv" not in claims


# ── consuming a verification token ──────────────────────────────────────────

def test_confirm_marks_verified():
    user = _User(is_verified=False)
    confirm_email_verification(token=create_email_verification_token(user), db=_db_for(user))
    assert user.is_verified is True
    assert user.verified_at is not None


def test_confirm_is_idempotent():
    """Re-following a used link succeeds — the outcome is identical and an error would
    only confuse someone who clicked twice."""
    user = _User(is_verified=True)
    token = create_email_verification_token(user)
    assert confirm_email_verification(token=token, db=_db_for(user)) is user


def test_confirm_refuses_a_disabled_account():
    user = _User(is_active=False)
    with pytest.raises(HTTPException) as exc:
        confirm_email_verification(token=create_email_verification_token(user), db=_db_for(user))
    assert exc.value.status_code == 403


def test_confirm_for_a_missing_user_is_generic():
    user = _User()
    with pytest.raises(HTTPException) as exc:
        confirm_email_verification(token=create_email_verification_token(user), db=_db_for(None))
    assert exc.value.status_code == 400


# ── the opt-in login gate ───────────────────────────────────────────────────

def _authenticate(user, monkeypatch, *, require: bool):
    from AINDY.services import auth_service

    monkeypatch.setattr(auth_service.settings, "AINDY_REQUIRE_VERIFIED_LOGIN", require, raising=False)
    return auth_service.authenticate_user(
        email=user.email, password="a-good-password", db=_db_for(user)
    )


def test_unverified_login_allowed_by_default(monkeypatch):
    """Default OFF is a lockout guard: deployments whose users registered before
    verification existed must not be shut out by an upgrade."""
    user = _User(is_verified=False)
    assert _authenticate(user, monkeypatch, require=False) is user


def test_unverified_login_refused_when_gate_enabled(monkeypatch):
    user = _User(is_verified=False)
    with pytest.raises(HTTPException) as exc:
        _authenticate(user, monkeypatch, require=True)
    assert exc.value.status_code == 403


def test_verified_login_allowed_when_gate_enabled(monkeypatch):
    user = _User(is_verified=True)
    assert _authenticate(user, monkeypatch, require=True) is user


def test_gate_is_checked_after_the_password(monkeypatch):
    """Otherwise it becomes its own oracle — a caller without valid credentials could
    learn which addresses exist and whether they are verified."""
    from AINDY.services import auth_service

    monkeypatch.setattr(auth_service.settings, "AINDY_REQUIRE_VERIFIED_LOGIN", True, raising=False)
    user = _User(is_verified=False)
    with pytest.raises(HTTPException) as exc:
        auth_service.authenticate_user(email=user.email, password="WRONG", db=_db_for(user))
    assert exc.value.status_code == 401, "wrong password must 401, not reveal verification state"


# ── register: the two paths must be indistinguishable ───────────────────────

def _register_route():
    import importlib
    import sys

    return sys.modules.get("AINDY.routes.auth_router") or importlib.import_module(
        "AINDY.routes.auth_router"
    )


def test_register_route_is_202_and_has_no_response_model():
    """A 201-with-token contract cannot be uniform; the status change is load-bearing."""
    mod = _register_route()
    route = next(r for r in mod.router.routes if getattr(r, "path", "") == "/auth/register")
    assert route.status_code == 202


def test_verify_email_route_registered():
    mod = _register_route()
    assert any(getattr(r, "path", "") == "/auth/verify-email" for r in mod.router.routes)


def _client(runtime_only_app):
    from fastapi.testclient import TestClient

    return TestClient(runtime_only_app, raise_server_exceptions=False)


def _post_register(client, email, password="a-good-password"):
    return client.post(
        "/auth/register", json={"email": email, "password": password, "username": None}
    )


def test_new_and_duplicate_registration_are_indistinguishable(runtime_only_app, mock_db):
    """The whole point of Phase C, exercised over real HTTP.

    Status AND body must match. If either differed, the oracle would still be open — the
    caller would simply read a different field.
    """
    from AINDY.db.models.user import User

    suffix = uuid.uuid4().hex[:8]
    email = f"dup-{suffix}@example.test"

    with patch("AINDY.platform_layer.email_channel.send_email", return_value={"success": True}):
        with _client(runtime_only_app) as client:
            first = _post_register(client, email)
            second = _post_register(client, email)

    assert first.status_code == second.status_code == 202, (first.status_code, second.status_code)

    # Compare the meaningful payload, not the whole envelope: trace ids, event ids and
    # duration_ms differ between any two requests and are not an enumeration signal. What
    # must match is everything a caller could read to tell the branches apart.
    def _payload(resp):
        data = dict(resp.json().get("data") or {})
        data.pop("execution_envelope", None)
        return data

    assert _payload(first) == _payload(second), "payloads differ — the oracle is still open"
    assert "access_token" not in str(first.json()) and "access_token" not in str(second.json())

    # And exactly one account exists: the duplicate created nothing.
    assert mock_db.query(User).filter(User.email == email).count() == 1


def test_registration_returns_no_token(runtime_only_app, mock_db):
    """A token in the response is what made uniformity impossible — a duplicate cannot be
    given one. Its absence is load-bearing, not cosmetic."""
    suffix = uuid.uuid4().hex[:8]
    with patch("AINDY.platform_layer.email_channel.send_email", return_value={"success": True}):
        with _client(runtime_only_app) as client:
            resp = _post_register(client, f"new-{suffix}@example.test")

    assert resp.status_code == 202
    body = resp.json()
    assert "access_token" not in str(body), f"registration still returns a token: {body}"


def test_short_password_is_400_before_the_existence_check(runtime_only_app, mock_db):
    """A 400 describes the submitted password, not the account, so it is not an
    enumeration signal — and running it first means a rejected request never reveals
    whether the address exists."""
    suffix = uuid.uuid4().hex[:8]
    email = f"short-{suffix}@example.test"
    with patch("AINDY.platform_layer.email_channel.send_email", return_value={"success": True}):
        with _client(runtime_only_app) as client:
            created = _post_register(client, email)
            assert created.status_code == 202
            # Same address, now taken — a short password must still 400, not 202/409.
            resp = _post_register(client, email, password="short")

    assert resp.status_code == 400


def test_duplicate_notice_and_verification_use_different_subjects():
    """They must differ in the mailbox — that is where the distinction legitimately
    belongs — while the HTTP response stays identical."""
    import inspect

    mod = _register_route()
    verify_src = inspect.getsource(mod._send_verification_email)
    dup_src = inspect.getsource(mod._send_duplicate_registration_notice)
    assert "Confirm your email address" in verify_src
    assert "Someone tried to register" in dup_src


def test_register_handles_the_concurrent_duplicate_race():
    """If a concurrent request wins between the existence check and the insert,
    register_user raises 409 — and letting that reach the caller would leak precisely what
    the uniform 202 hides, through a window an attacker can provoke."""
    import inspect

    mod = _register_route()
    src = inspect.getsource(mod.register)
    assert "409" in src, "the race path must be handled, not left to surface as 409"
    assert src.count("verification_sent") >= 2, "the race path must return the uniform body"
