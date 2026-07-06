"""AGENT-HARDEN-2 — cryptographic capability-token integrity (keyed HMAC).

The capability token's ``token_hash`` is an HMAC-SHA256 keyed on the auth
``KeyRing`` secret, not an unkeyed SHA-256. These tests pin the security
properties: the MAC is keyed (a legacy unkeyed hash is rejected), tampering any
bound field fails verification, and rotation is honored within the grace window.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

import pytest

from AINDY.agents import capability_service as cs
from AINDY.agents.capability_service import (
    _now_utc,
    _token_hash,
    _token_payload,
    validate_token,
)

pytestmark = pytest.mark.runtime_only

_FIELDS = dict(
    run_id="r1",
    user_id="u1",
    execution_token="exec-1",
    approval_mode="manual",
    granted_tools=[],
    allowed_capabilities=[],
)


def _use_keys(monkeypatch, *, active, verify):
    monkeypatch.setattr("AINDY.services.auth_service.signing_key", lambda: active)
    monkeypatch.setattr("AINDY.services.auth_service.verification_keys", lambda: list(verify))


def _mk_token(**over):
    issued = _now_utc()
    expires = issued + timedelta(hours=1)
    fields = {**_FIELDS, "issued_at": issued.isoformat(), "expires_at": expires.isoformat()}
    fields.update(over)
    tok = {
        "run_id": fields["run_id"],
        "user_id": fields["user_id"],
        "agent_type": "default",
        "execution_token": fields["execution_token"],
        "issued_at": fields["issued_at"],
        "expires_at": fields["expires_at"],
        "granted_tools": fields["granted_tools"],
        "allowed_capabilities": fields["allowed_capabilities"],
        "approval_mode": fields["approval_mode"],
        "token_hash": _token_hash(**fields),
    }
    return tok, fields


# --------------------------------------------------------------------------- #
# The MAC is keyed
# --------------------------------------------------------------------------- #

def test_token_hash_is_keyed_hmac(monkeypatch):
    _use_keys(monkeypatch, active="secret-A", verify=["secret-A"])
    tok, fields = _mk_token()

    payload = _token_payload(**fields)
    expected = hmac.new(b"secret-A", payload.encode(), hashlib.sha256).hexdigest()
    unkeyed = hashlib.sha256(payload.encode()).hexdigest()

    assert tok["token_hash"] == expected
    assert tok["token_hash"] != unkeyed  # not the old forgeable scheme


def test_different_keys_produce_different_macs(monkeypatch):
    _use_keys(monkeypatch, active="secret-A", verify=["secret-A"])
    a, fields = _mk_token()
    _use_keys(monkeypatch, active="secret-B", verify=["secret-B"])
    b_hash = _token_hash(**fields)
    assert a["token_hash"] != b_hash


# --------------------------------------------------------------------------- #
# The forge hole is closed
# --------------------------------------------------------------------------- #

def test_legacy_unkeyed_hash_is_rejected(monkeypatch):
    """A token bearing the old unkeyed SHA-256 over its own fields must fail —
    this is exactly the forgery the change closes."""
    _use_keys(monkeypatch, active="secret-A", verify=["secret-A"])
    _, fields = _mk_token()
    payload = _token_payload(**fields)
    forged = {
        "run_id": "r1", "user_id": "u1", "agent_type": "default",
        "execution_token": fields["execution_token"],
        "issued_at": fields["issued_at"], "expires_at": fields["expires_at"],
        "granted_tools": [], "allowed_capabilities": [], "approval_mode": "manual",
        "token_hash": hashlib.sha256(payload.encode()).hexdigest(),  # attacker-computable
    }
    result = validate_token(forged, run_id="r1", user_id="u1")
    assert result["ok"] is False
    assert result["error"] == "capability token hash mismatch"


def test_valid_token_verifies(monkeypatch):
    _use_keys(monkeypatch, active="secret-A", verify=["secret-A"])
    tok, _ = _mk_token()
    assert validate_token(tok, run_id="r1", user_id="u1")["ok"] is True


def test_tampered_execution_token_rejected(monkeypatch):
    """execution_token is bound by the MAC — swapping it invalidates the token."""
    _use_keys(monkeypatch, active="secret-A", verify=["secret-A"])
    tok, _ = _mk_token()
    tok["execution_token"] = "attacker-swapped-uuid"
    result = validate_token(tok, run_id="r1", user_id="u1")
    assert result["ok"] is False
    assert result["error"] == "capability token hash mismatch"


def test_token_signed_under_foreign_key_rejected(monkeypatch):
    """A MAC computed under a key the ring never held must fail."""
    _use_keys(monkeypatch, active="secret-A", verify=["secret-A"])
    tok, fields = _mk_token()
    payload = _token_payload(**fields)
    tok["token_hash"] = hmac.new(b"attacker-key", payload.encode(), hashlib.sha256).hexdigest()
    assert validate_token(tok, run_id="r1", user_id="u1")["ok"] is False


# --------------------------------------------------------------------------- #
# Rotation grace window (mirrors the JWT KeyRing)
# --------------------------------------------------------------------------- #

def test_rotation_grace_window(monkeypatch):
    # Minted under A.
    _use_keys(monkeypatch, active="key-A", verify=["key-A"])
    tok, _ = _mk_token()

    # Rotated: active is now B, A retained as previous within grace.
    _use_keys(monkeypatch, active="key-B", verify=["key-B", "key-A"])
    assert validate_token(tok, run_id="r1", user_id="u1")["ok"] is True

    # Grace expired: only B remains — the A-minted token no longer verifies.
    _use_keys(monkeypatch, active="key-B", verify=["key-B"])
    assert validate_token(tok, run_id="r1", user_id="u1")["ok"] is False


# --------------------------------------------------------------------------- #
# KeyRing-unavailable fallback
# --------------------------------------------------------------------------- #

def test_signing_keys_fallback_to_secret_key(monkeypatch):
    def _raise():
        raise RuntimeError("keyring down")

    monkeypatch.setattr("AINDY.services.auth_service.signing_key", _raise)
    monkeypatch.setattr("AINDY.services.auth_service.verification_keys", _raise)
    monkeypatch.setenv("SECRET_KEY", "env-secret")

    assert cs._signing_keys(for_verify=True) == ["env-secret"]
    assert cs._signing_keys(for_verify=False) == ["env-secret"]
