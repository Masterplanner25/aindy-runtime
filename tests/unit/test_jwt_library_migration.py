"""
The JWT library moved from python-jose to PyJWT (CVE-2026-85394 / GHSA-3qf3-8w2g-rqmx).

python-jose <= 3.5.0 accepts a DER-encoded public key as an HMAC secret, so a token forged
with the public key verifies when ``algorithms`` is not pinned. No fixed release exists. The
runtime was not exploitable (HS256 pinned at every verifying decode, symmetric keys only),
but the advisory has no fix, so the library goes rather than an exemption.

What these tests pin:

1. Tokens minted by python-jose BEFORE the upgrade still verify after it. The fixtures were
   minted with python-jose 3.5.0 itself (the key and claims are below) and are frozen here,
   so the test does not need jose installed to mean something. A session, an emailed
   verification link and an emailed reset link must all survive the upgrade.
2. ``algorithms`` stays pinned: a token signed with a different HMAC algorithm, or with
   ``alg: none``, is refused.
3. Nothing under ``AINDY/`` imports ``jose`` again.
"""
from __future__ import annotations

import ast
import base64
import json
from pathlib import Path

import jwt
import pytest
from fastapi import HTTPException

from AINDY.services import auth_service
from AINDY.services.auth_service import (
    KeyRing,
    decode_access_token,
    verify_email_token,
    verify_password_reset_token,
)


pytestmark = pytest.mark.runtime_only

# Minted with python-jose 3.5.0, HS256, exp 2100-01-01:
#   access : jwt.encode(claims, LEGACY_SECRET)
#   verify : jwt.encode(claims, hmac(LEGACY_SECRET, b"aindy-email-verify-v1").hexdigest())
#   reset  : jwt.encode(claims, hmac(LEGACY_SECRET, b"aindy-password-reset-v1").hexdigest())
LEGACY_SECRET = "legacy-fixture-secret-0123456789abcdef"
LEGACY_SUB = "6f1c2a3e-1b2c-4d5e-8f90-123456789abc"
JOSE_ACCESS = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiI2ZjFjMmEzZS0xYjJjLTRkNWUtOGY5MC0xMjM0NTY3ODlhYmMiLCJlbWFpbCI6ImxlZ2FjeUBleGFt"
    "cGxlLnRlc3QiLCJpc19hZG1pbiI6ZmFsc2UsInR2IjowLCJwdXJwb3NlIjoiYWNjZXNzIiwiZXhwIjo0MTAyNDQ0"
    "ODAwfQ.1psOWRT5hnNtt4Gt9Smt_rLXgxCFWSagE-h4QJpV1Ac"
)
JOSE_VERIFY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiI2ZjFjMmEzZS0xYjJjLTRkNWUtOGY5MC0xMjM0NTY3ODlhYmMiLCJwdXJwb3NlIjoiZW1haWxfdmVy"
    "aWZ5IiwiZXhwIjo0MTAyNDQ0ODAwfQ.36Ib5qQIU4pPWKO1y0xRJCAV5YnduwomwR3LrbbAguk"
)
JOSE_RESET = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiI2ZjFjMmEzZS0xYjJjLTRkNWUtOGY5MC0xMjM0NTY3ODlhYmMiLCJ0diI6MCwicHVycG9zZSI6InBh"
    "c3N3b3JkX3Jlc2V0IiwiZXhwIjo0MTAyNDQ0ODAwfQ.rhk1em_H64Aqbm7Tj103Uv0wcZcDtAtAX3GNIedziVY"
)


@pytest.fixture
def legacy_key(monkeypatch):
    monkeypatch.setattr(auth_service, "_key_ring", KeyRing(active=LEGACY_SECRET))


# ── tokens minted before the upgrade survive it ─────────────────────────────

def test_jose_minted_access_token_still_verifies(legacy_key):
    payload = decode_access_token(JOSE_ACCESS)
    assert payload["sub"] == LEGACY_SUB
    assert payload["purpose"] == "access"
    assert payload["tv"] == 0


def test_jose_minted_verification_link_still_verifies(legacy_key):
    assert verify_email_token(JOSE_VERIFY)["sub"] == LEGACY_SUB


def test_jose_minted_reset_link_still_verifies(legacy_key):
    assert verify_password_reset_token(JOSE_RESET)["sub"] == LEGACY_SUB


def test_jose_tokens_are_refused_under_a_different_key():
    """Liveness control: the three tests above pass because of the key, not despite it."""
    with pytest.raises(HTTPException) as exc:
        decode_access_token(JOSE_ACCESS)
    assert exc.value.status_code == 401


def test_pyjwt_mints_byte_identical_tokens(legacy_key):
    """Same header, same claim order, same signature — the wire format did not move."""
    claims = jwt.decode(JOSE_ACCESS, LEGACY_SECRET, algorithms=["HS256"])
    assert jwt.encode(claims, LEGACY_SECRET, algorithm="HS256") == JOSE_ACCESS


# ── algorithms stays pinned ─────────────────────────────────────────────────

def _b64(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def test_other_hmac_algorithm_is_refused(legacy_key):
    claims = jwt.decode(JOSE_ACCESS, LEGACY_SECRET, algorithms=["HS256"])
    hs512 = jwt.encode(claims, LEGACY_SECRET, algorithm="HS512")
    with pytest.raises(HTTPException) as exc:
        decode_access_token(hs512)
    assert exc.value.status_code == 401


def test_alg_none_is_refused(legacy_key):
    claims = jwt.decode(JOSE_ACCESS, LEGACY_SECRET, algorithms=["HS256"])
    unsigned = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}."
    with pytest.raises(HTTPException) as exc:
        decode_access_token(unsigned)
    assert exc.value.status_code == 401


# ── python-jose does not come back ──────────────────────────────────────────

def _imported_top_levels(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_no_runtime_module_imports_jose():
    root = Path(__file__).resolve().parents[2] / "AINDY"
    imports = {p: _imported_top_levels(p) for p in root.rglob("*.py")}
    # Liveness: the census sees the JWT call sites it is meant to be guarding.
    jwt_users = sorted(p.name for p, names in imports.items() if "jwt" in names)
    assert {"auth_service.py", "rate_limiter.py", "exception_handlers.py"} <= set(jwt_users)
    offenders = sorted(str(p.relative_to(root)) for p, names in imports.items() if "jose" in names)
    assert offenders == []


# ── the two unverified reads still read ─────────────────────────────────────
# Both sites swallow every exception, so a library that raised here would degrade
# silently: the rate limiter to per-IP buckets, the error log to no user id.

def _request(token: str):
    from starlette.requests import Request

    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": ("203.0.113.9", 1234),
    })


def test_rate_limiter_buckets_on_sub_without_the_key():
    from AINDY.platform_layer.rate_limiter import _identity_key

    # No key ring patched: the signature cannot verify, and must not need to.
    assert _identity_key(_request(JOSE_ACCESS)) == LEGACY_SUB
    # Liveness: garbage falls back to the client address.
    assert _identity_key(_request("not-a-token")) == "203.0.113.9"


def test_error_log_reads_user_from_an_expired_unverifiable_token():
    import uuid

    from AINDY.exception_handlers import _extract_user_id_from_request

    claims = jwt.decode(JOSE_ACCESS, LEGACY_SECRET, algorithms=["HS256"])
    expired = jwt.encode({**claims, "exp": 1}, "some-other-key-0123456789abcdef0123", algorithm="HS256")
    assert _extract_user_id_from_request(_request(expired)) == uuid.UUID(LEGACY_SUB)
    assert _extract_user_id_from_request(_request("not-a-token")) is None
