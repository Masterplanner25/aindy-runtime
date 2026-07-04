"""RTR-1 Phase 2e — capability token expiry check + refresh (durable resume)."""
from __future__ import annotations

from datetime import timedelta

from AINDY.agents.capability_service import (
    _now_utc,
    _token_hash,
    refresh_token,
    token_is_expired,
    validate_token,
)


def _token(*, expires_delta_hours: float, run_id="r1", user_id="u1"):
    issued = _now_utc()
    expires = issued + timedelta(hours=expires_delta_hours)
    issued_s, expires_s = issued.isoformat(), expires.isoformat()
    granted_tools = ["send", "search"]
    allowed_caps = ["send_email", "web_search"]
    return {
        "run_id": run_id, "user_id": user_id, "agent_type": "default",
        "execution_token": "old-exec-token",
        "issued_at": issued_s, "expires_at": expires_s,
        "granted_tools": granted_tools, "allowed_capabilities": allowed_caps,
        "approval_mode": "manual",
        "token_hash": _token_hash(
            run_id=run_id, user_id=user_id, execution_token="old-exec-token",
            issued_at=issued_s, expires_at=expires_s, approval_mode="manual",
            granted_tools=granted_tools, allowed_capabilities=allowed_caps,
        ),
    }


# --------------------------------------------------------------------------- #
# token_is_expired
# --------------------------------------------------------------------------- #

def test_token_is_expired_detects_past_and_future():
    assert token_is_expired(_token(expires_delta_hours=-1)) is True
    assert token_is_expired(_token(expires_delta_hours=1)) is False


def test_token_is_expired_treats_missing_or_malformed_as_expired():
    assert token_is_expired(None) is True
    assert token_is_expired({}) is True
    assert token_is_expired({"expires_at": "not-a-date"}) is True
    assert token_is_expired("nope") is True


# --------------------------------------------------------------------------- #
# refresh_token
# --------------------------------------------------------------------------- #

def test_refresh_reuses_grants_on_fresh_clock():
    expired = _token(expires_delta_hours=-5)
    assert token_is_expired(expired) is True

    refreshed = refresh_token(expired)
    assert refreshed is not None
    # Grants + identity preserved verbatim — no re-derivation / escalation.
    assert refreshed["granted_tools"] == expired["granted_tools"]
    assert refreshed["allowed_capabilities"] == expired["allowed_capabilities"]
    assert refreshed["approval_mode"] == "manual"
    assert refreshed["run_id"] == "r1" and refreshed["user_id"] == "u1"
    # Fresh clock + new opaque token + recomputed hash.
    assert refreshed["execution_token"] != expired["execution_token"]
    assert token_is_expired(refreshed) is False
    assert refreshed["token_hash"] != expired["token_hash"]


def test_refreshed_token_passes_validation_registry_independent():
    # Empty grants → validate_token's registry normalization is a no-op, isolating
    # the identity/expiry/hash checks. (With real tools, validation additionally
    # requires the plugin registry to be loaded — as it is in production; the same
    # applies to mint_token's tokens, so refresh mirrors mint exactly.)
    from datetime import timedelta as _td

    issued = _now_utc()
    expires_s = (issued + _td(hours=-1)).isoformat()
    issued_s = issued.isoformat()
    base = {
        "run_id": "r1", "user_id": "u1", "agent_type": "default",
        "execution_token": "old", "issued_at": issued_s, "expires_at": expires_s,
        "granted_tools": [], "allowed_capabilities": [], "approval_mode": "manual",
        "token_hash": _token_hash(
            run_id="r1", user_id="u1", execution_token="old",
            issued_at=issued_s, expires_at=expires_s, approval_mode="manual",
            granted_tools=[], allowed_capabilities=[],
        ),
    }
    refreshed = refresh_token(base)
    result = validate_token(refreshed, run_id="r1", user_id="u1")
    assert result["ok"] is True, result


def test_refresh_rejects_unusable_token():
    assert refresh_token(None) is None
    assert refresh_token({}) is None
    assert refresh_token({"run_id": "r1"}) is None  # no user_id
