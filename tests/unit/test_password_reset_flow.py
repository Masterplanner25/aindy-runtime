"""
FR-6 Phase B — `/auth/password/forgot` + `/auth/password/reset`.

The tests are weighted toward the two hazards the scope identified, because those are what
a plausible implementation gets wrong:

  * **Hazard 1** — a reset token must NOT authenticate. It carries `sub` and `tv`, which is
    everything the auth path needs, so a token signed with the ordinary key would be a
    working session. Domain separation is the control; the test below is the proof.
  * **Single-use is structural** — consuming a token bumps `token_version`, so a replay
    fails the version comparison. There is no revocation list to get wrong.
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
    MIN_PASSWORD_LENGTH,
    PASSWORD_RESET_PURPOSE,
    create_password_reset_token,
    decode_access_token,
    hash_password,
    reset_password_with_token,
    signing_key,
    verify_password,
    verify_password_reset_token,
    _reset_signing_key,
)


pytestmark = pytest.mark.runtime_only


class _User:
    def __init__(self, token_version=0, is_active=True):
        self.id = uuid.uuid4()
        self.email = "u@example.test"
        self.hashed_password = hash_password("old-password-1")
        self.is_active = is_active
        self.token_version = token_version


def _db_for(user):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    return db


# ── Hazard 1: a reset token is not a session ────────────────────────────────

def test_reset_token_is_rejected_by_the_access_path():
    """THE regression test for FR-6 Hazard 1.

    The token carries `sub` and `tv` — everything `_resolve_authenticated_jwt_user` needs.
    If it were signed with the ordinary key, the emailed link would be a working session.
    """
    token = create_password_reset_token(_User())
    with pytest.raises(HTTPException) as exc:
        decode_access_token(token)
    assert exc.value.status_code == 401


def test_reset_token_does_not_verify_against_the_access_key():
    """Domain separation, checked at the crypto layer rather than via behaviour."""
    token = create_password_reset_token(_User())
    with pytest.raises(Exception):
        jwt.decode(token, signing_key(), algorithms=[ALGORITHM])


def test_access_token_is_rejected_by_the_reset_path():
    """The separation has to hold in both directions, or an ordinary session token could
    be replayed as a reset authorisation."""
    from AINDY.services.auth_service import create_access_token

    access = create_access_token({"sub": "u1"}, token_version=0)
    with pytest.raises(HTTPException) as exc:
        verify_password_reset_token(access)
    assert exc.value.status_code == 400


def test_reset_key_differs_from_the_signing_key():
    assert _reset_signing_key() != signing_key()


# ── token validation ────────────────────────────────────────────────────────

def test_valid_token_round_trips():
    user = _User(token_version=4)
    claims = verify_password_reset_token(create_password_reset_token(user))
    assert claims["sub"] == str(user.id)
    assert claims["tv"] == 4
    assert claims["purpose"] == PASSWORD_RESET_PURPOSE


def test_expired_token_is_rejected():
    user = _User()
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "tv": 0,
            "purpose": PASSWORD_RESET_PURPOSE,
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        _reset_signing_key(),
        algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException):
        verify_password_reset_token(expired)


def test_wrong_purpose_on_the_reset_key_is_rejected():
    """Correctly signed with the reset key but minted for something else — the purpose
    claim still has to be checked, not just the key."""
    forged = jwt.encode(
        {
            "sub": "u1",
            "tv": 0,
            "purpose": "email_verify",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        _reset_signing_key(),
        algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException):
        verify_password_reset_token(forged)


def test_rejections_are_indistinguishable():
    """Expired vs forged vs wrong-purpose must not be tellable apart — each would disclose
    account state to whoever holds the token."""
    user = _User()
    bad = [
        jwt.encode({"sub": "x", "tv": 0, "purpose": PASSWORD_RESET_PURPOSE,
                    "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                   _reset_signing_key(), algorithm=ALGORITHM),
        jwt.encode({"sub": str(user.id), "tv": 0, "purpose": "other",
                    "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                   _reset_signing_key(), algorithm=ALGORITHM),
        "not-a-token-at-all",
    ]
    seen = set()
    for tok in bad:
        with pytest.raises(HTTPException) as exc:
            verify_password_reset_token(tok)
        seen.add((exc.value.status_code, exc.value.detail))
    assert len(seen) == 1, f"rejection responses differ and leak state: {seen}"


# ── consuming the token ─────────────────────────────────────────────────────

def test_reset_sets_the_new_password_and_bumps_the_version():
    user = _User(token_version=2)
    token = create_password_reset_token(user)
    reset_password_with_token(token=token, new_password="brand-new-pass", db=_db_for(user))
    assert verify_password("brand-new-pass", user.hashed_password)
    assert user.token_version == 3


def test_token_is_single_use_by_construction():
    """No revocation list — the version bump from the first use invalidates the token."""
    user = _User(token_version=0)
    token = create_password_reset_token(user)
    reset_password_with_token(token=token, new_password="first-new-pass", db=_db_for(user))

    with pytest.raises(HTTPException) as exc:
        reset_password_with_token(token=token, new_password="second-new-pass", db=_db_for(user))
    assert exc.value.status_code == 400
    assert verify_password("first-new-pass", user.hashed_password), "replay changed the password"


def test_token_minted_before_an_unrelated_logout_is_rejected():
    """Any token_version movement burns the token — logout, a password change, or an
    admin session invalidation, not just a prior reset."""
    user = _User(token_version=1)
    token = create_password_reset_token(user)
    user.token_version = 2  # e.g. the user logged out meanwhile
    with pytest.raises(HTTPException):
        reset_password_with_token(token=token, new_password="new-password-x", db=_db_for(user))


def test_reset_enforces_the_password_floor():
    user = _User()
    token = create_password_reset_token(user)
    with pytest.raises(HTTPException) as exc:
        reset_password_with_token(
            token=token, new_password="a" * (MIN_PASSWORD_LENGTH - 1), db=_db_for(user)
        )
    assert exc.value.status_code == 400


def test_reset_refuses_a_disabled_account():
    user = _User(is_active=False)
    token = create_password_reset_token(user)
    with pytest.raises(HTTPException) as exc:
        reset_password_with_token(token=token, new_password="new-password-x", db=_db_for(user))
    assert exc.value.status_code == 403


def test_token_for_a_deleted_user_is_generic_not_404():
    """A token naming a deleted account must not be distinguishable from a forged one."""
    user = _User()
    token = create_password_reset_token(user)
    with pytest.raises(HTTPException) as exc:
        reset_password_with_token(token=token, new_password="new-password-x", db=_db_for(None))
    assert exc.value.status_code == 400


# ── route behaviour ─────────────────────────────────────────────────────────

def _routes():
    from AINDY.routes.auth_router import router

    return {r.path for r in router.routes if hasattr(r, "path")}


def test_both_routes_are_registered():
    paths = _routes()
    assert "/auth/password/forgot" in paths
    assert "/auth/password/reset" in paths


def test_forgot_limits_per_ip_and_per_email():
    """Per-IP alone lets a distributed caller pound one inbox; per-email alone lets one
    host sweep many addresses. Both keys must be counted."""
    import sys, importlib

    mod = sys.modules.get("AINDY.routes.auth_router") or importlib.import_module(
        "AINDY.routes.auth_router"
    )
    rm = MagicMock()
    rm.rate_limit_hit.return_value = (1, False)
    req = MagicMock()
    req.client.host = "203.0.113.9"
    with patch("AINDY.kernel.resource_manager.get_resource_manager", return_value=rm):
        mod._forgot_rate_limited("USER@Example.test", req)

    keys = [c.args[0] for c in rm.rate_limit_hit.call_args_list]
    assert any(k.startswith("auth:forgot:ip:") for k in keys), keys
    assert any(k == "auth:forgot:email:user@example.test" for k in keys), (
        f"email key must be normalised lower-case: {keys}"
    )


def test_forgot_rate_limiter_fails_open():
    """A counter outage must not lock legitimate users out of account recovery."""
    import sys, importlib

    mod = sys.modules.get("AINDY.routes.auth_router") or importlib.import_module(
        "AINDY.routes.auth_router"
    )
    with patch(
        "AINDY.kernel.resource_manager.get_resource_manager", side_effect=RuntimeError("redis down")
    ):
        assert mod._forgot_rate_limited("u@example.test", MagicMock()) is False
