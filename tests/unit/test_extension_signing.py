"""AGENT-HARDEN-10 (PR1) — signed plugin bundles + SBOM."""
from __future__ import annotations

import pytest

from AINDY.platform_layer.extension_signing import (
    SIGNING_ALGORITHM,
    clear_trusted_keys,
    enforce_bundle_signature,
    generate_keypair,
    generate_sbom,
    is_trusted,
    key_fingerprint,
    register_trusted_key,
    sign_digest,
    signature_required,
    signing_available,
    verify_bundle_signature,
    verify_digest,
)

pytestmark = pytest.mark.runtime_only

_DIGEST = "a" * 64  # a stand-in SHA-256 hex bundle digest


@pytest.fixture(autouse=True)
def _clean():
    clear_trusted_keys()
    yield
    clear_trusted_keys()


# --------------------------------------------------------------------------- #
# Ed25519 sign / verify
# --------------------------------------------------------------------------- #

def test_signing_available():
    assert signing_available() is True
    assert SIGNING_ALGORITHM == "ed25519"


def test_sign_and_verify_roundtrip():
    priv, pub = generate_keypair()
    sig = sign_digest(priv, _DIGEST)
    assert verify_digest(pub, _DIGEST, sig) is True


def test_verify_rejects_tampered_digest():
    priv, pub = generate_keypair()
    sig = sign_digest(priv, _DIGEST)
    assert verify_digest(pub, "b" * 64, sig) is False  # different bundle digest


def test_verify_rejects_wrong_key():
    priv, _pub = generate_keypair()
    _priv2, pub2 = generate_keypair()
    sig = sign_digest(priv, _DIGEST)
    assert verify_digest(pub2, _DIGEST, sig) is False


def test_fingerprint_is_stable_and_key_specific():
    _p, pub = generate_keypair()
    assert key_fingerprint(pub).startswith("sha256:")
    assert key_fingerprint(pub) == key_fingerprint(pub)
    _p2, pub2 = generate_keypair()
    assert key_fingerprint(pub) != key_fingerprint(pub2)


# --------------------------------------------------------------------------- #
# Trust registry + bundle verification
# --------------------------------------------------------------------------- #

def test_verify_bundle_signature_trusted():
    priv, pub = generate_keypair()
    kid = register_trusted_key(pub)
    assert is_trusted(kid) is True
    sig = sign_digest(priv, _DIGEST)
    assert verify_bundle_signature(digest_hex=_DIGEST, signature=sig, key_id=kid) == {
        "ok": True, "key_id": kid,
    }


def test_verify_bundle_untrusted_key_denied():
    priv, pub = generate_keypair()
    sig = sign_digest(priv, _DIGEST)
    kid = key_fingerprint(pub)  # never registered
    result = verify_bundle_signature(digest_hex=_DIGEST, signature=sig, key_id=kid)
    assert result["ok"] is False and "not in the trust registry" in result["error"]


def test_verify_bundle_unsigned_denied():
    assert verify_bundle_signature(digest_hex=_DIGEST, signature=None, key_id=None)["ok"] is False


def test_verify_bundle_bad_signature_denied():
    priv, pub = generate_keypair()
    kid = register_trusted_key(pub)
    # signature over a different digest → verification fails for _DIGEST
    sig = sign_digest(priv, "c" * 64)
    result = verify_bundle_signature(digest_hex=_DIGEST, signature=sig, key_id=kid)
    assert result["ok"] is False and "verification failed" in result["error"]


# --------------------------------------------------------------------------- #
# Profile enforcement — production refuses unsigned/untrusted
# --------------------------------------------------------------------------- #

def test_signature_required_by_profile():
    assert signature_required("distributed-api") is True
    assert signature_required("strong-sandbox") is True
    assert signature_required("single-instance") is False
    assert signature_required("") is False


def test_production_refuses_unsigned_bundle():
    gate = enforce_bundle_signature(profile="distributed-api", digest_hex=_DIGEST)
    assert gate["allowed"] is False and gate["verified"] is False
    assert "unsigned" in gate["error"]


def test_production_allows_valid_signed_bundle():
    priv, pub = generate_keypair()
    kid = register_trusted_key(pub)
    sig = sign_digest(priv, _DIGEST)
    gate = enforce_bundle_signature(
        profile="distributed-api", digest_hex=_DIGEST, signature=sig, key_id=kid
    )
    assert gate == {"allowed": True, "verified": True, "key_id": kid}


def test_dev_allows_unsigned_but_marks_unverified():
    gate = enforce_bundle_signature(profile="single-instance", digest_hex=_DIGEST)
    assert gate["allowed"] is True and gate["verified"] is False


# --------------------------------------------------------------------------- #
# SBOM
# --------------------------------------------------------------------------- #

def test_generate_sbom_shape():
    sbom = generate_sbom(
        name="acme-plugin",
        version="1.2.3",
        components=[
            {"name": "handler.py", "digest": "d" * 64},
            {"name": "requests", "version": "2.31.0", "type": "library"},
        ],
        timestamp="2026-07-06T00:00:00Z",
    )
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["metadata"]["component"] == {"type": "application", "name": "acme-plugin", "version": "1.2.3"}
    assert sbom["metadata"]["timestamp"] == "2026-07-06T00:00:00Z"
    assert sbom["components"][0]["hashes"] == [{"alg": "SHA-256", "content": "d" * 64}]
    assert sbom["components"][1]["version"] == "2.31.0"
