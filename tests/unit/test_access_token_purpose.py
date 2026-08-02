"""
Access tokens declare a purpose, and `decode_access_token` requires it (FR-6 Hazard 1).

Before this, `decode_access_token` asked exactly one question — does the signature verify
against a KeyRing secret — and examined nothing else. Any other token type signed with the
same key was therefore silently a valid bearer access token. FR-6's password-reset token
carries `sub` and `tv`, which is all `_resolve_authenticated_jwt_user` needs, so the emailed
reset link would have *been* a session.

FR-6 keeps the primary control lower down (non-access tokens are signed with a
domain-separated derived key and cannot verify here at all). This claim is defence in depth:
it makes "wrong token type" an explicit failure rather than something every future token
type has to remember to prevent by deriving its own key.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt

from AINDY.services.auth_service import (
    ACCESS_TOKEN_PURPOSE,
    ALGORITHM,
    create_access_token,
    decode_access_token,
    signing_key,
)


pytestmark = pytest.mark.runtime_only


def _mint_raw(claims: dict, *, key: str | None = None) -> str:
    """Mint a token directly, bypassing create_access_token, to forge shapes."""
    payload = {"exp": datetime.now(timezone.utc) + timedelta(minutes=30), **claims}
    return jwt.encode(payload, key or signing_key(), algorithm=ALGORITHM)


# ── the happy path still works ──────────────────────────────────────────────

def test_minted_access_token_round_trips():
    token = create_access_token({"sub": "u1", "email": "a@b.c"}, token_version=3)
    payload = decode_access_token(token)
    assert payload["sub"] == "u1"
    assert payload["tv"] == 3
    assert payload["purpose"] == ACCESS_TOKEN_PURPOSE


def test_mint_stamps_the_purpose_claim():
    """Decoded without validation — proves the claim is really in the token."""
    token = create_access_token({"sub": "u1"}, token_version=0)
    raw = jwt.decode(token, signing_key(), algorithms=[ALGORITHM])
    assert raw["purpose"] == ACCESS_TOKEN_PURPOSE


# ── the hazard itself ───────────────────────────────────────────────────────

def test_password_reset_shaped_token_is_rejected():
    """THE regression test. A correctly-signed token carrying sub+tv — everything
    `_resolve_authenticated_jwt_user` needs — must not authenticate."""
    forged = _mint_raw({"sub": "u1", "tv": 0, "purpose": "password_reset"})
    with pytest.raises(HTTPException) as exc:
        decode_access_token(forged)
    assert exc.value.status_code == 401


def test_token_with_no_purpose_is_rejected():
    """The pre-2.0 token shape. Also the upgrade's breaking edge: sessions minted
    before the upgrade carry no purpose and stop verifying."""
    legacy = _mint_raw({"sub": "u1", "tv": 0})
    with pytest.raises(HTTPException) as exc:
        decode_access_token(legacy)
    assert exc.value.status_code == 401


@pytest.mark.parametrize("purpose", ["password_reset", "email_verify", "", None, "ACCESS", 1])
def test_any_non_access_purpose_is_rejected(purpose):
    claims = {"sub": "u1", "tv": 0}
    if purpose is not None:
        claims["purpose"] = purpose
    with pytest.raises(HTTPException) as exc:
        decode_access_token(_mint_raw(claims))
    assert exc.value.status_code == 401


# ── the rejection must not leak ─────────────────────────────────────────────

def test_wrong_purpose_is_indistinguishable_from_a_bad_signature():
    """A distinct error would confirm the token is genuine and whose it is."""
    wrong_purpose = _mint_raw({"sub": "u1", "tv": 0, "purpose": "password_reset"})
    bad_signature = _mint_raw({"sub": "u1", "tv": 0, "purpose": "access"}, key="not-the-key")

    errs = []
    for tok in (wrong_purpose, bad_signature):
        with pytest.raises(HTTPException) as exc:
            decode_access_token(tok)
        errs.append((exc.value.status_code, exc.value.detail))

    assert errs[0] == errs[1], f"responses differ and leak token validity: {errs}"


# ── signature checking is not weakened ──────────────────────────────────────

def test_correct_purpose_with_wrong_key_still_rejected():
    """The claim is additive — it must not become a substitute for the signature."""
    with pytest.raises(HTTPException):
        decode_access_token(
            _mint_raw({"sub": "u1", "tv": 0, "purpose": ACCESS_TOKEN_PURPOSE}, key="wrong-key")
        )


def test_expired_access_token_still_rejected():
    expired = jwt.encode(
        {
            "sub": "u1",
            "tv": 0,
            "purpose": ACCESS_TOKEN_PURPOSE,
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        signing_key(),
        algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException):
        decode_access_token(expired)
