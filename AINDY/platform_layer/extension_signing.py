"""
platform_layer/extension_signing.py - Signed plugin bundles + SBOM (AGENT-HARDEN-10).

Replaces the hardcoded ``signing: unsupported`` posture with a real cryptographic
signature: a bundle's content **digest** (SHA-256) is signed with an **Ed25519**
private key; the host verifies the detached signature against a **trust registry**
of public keys before load. Also emits a CycloneDX-lite **SBOM** for a bundle.

Enforcement policy: in a production deployment profile the host **refuses** an
unsigned or untrusted bundle; in dev/single-instance an unsigned bundle is allowed
but reported ``verified: False``. Fail-closed on any verification error.

Signing uses ``cryptography`` (already an install dependency). ``signing_available()``
reports availability; verification fails closed if the primitive is missing.
"""
from __future__ import annotations

import base64
import hashlib
import threading
from typing import Any, Optional

SIGNING_ALGORITHM = "ed25519"

# Deployment profiles where a valid, trusted signature is mandatory.
_PRODUCTION_PROFILES = {
    "distributed-api",
    "distributed-api-worker",
    "production",
    "strong-sandbox",
    "strong-sandbox-vm",
}


def _ed25519():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    return Ed25519PrivateKey, Ed25519PublicKey, serialization


def signing_available() -> bool:
    try:
        _ed25519()
        return True
    except Exception:
        return False


def generate_keypair() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64) — raw Ed25519 keys, base64-encoded."""
    Priv, _Pub, ser = _ed25519()
    priv = Priv.generate()
    priv_raw = priv.private_bytes(
        ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption()
    )
    pub_raw = priv.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    return base64.b64encode(priv_raw).decode(), base64.b64encode(pub_raw).decode()


def key_fingerprint(public_key_b64: str) -> str:
    """Stable key id: ``sha256:<hex>`` over the raw public key."""
    return "sha256:" + hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()


def sign_digest(private_key_b64: str, digest_hex: str) -> str:
    Priv, _Pub, _ser = _ed25519()
    priv = Priv.from_private_bytes(base64.b64decode(private_key_b64))
    return base64.b64encode(priv.sign(str(digest_hex).encode("utf-8"))).decode()


def verify_digest(public_key_b64: str, digest_hex: str, signature_b64: str) -> bool:
    """Constant-time-ish Ed25519 verify. Any error → False (fail-closed)."""
    try:
        _Priv, Pub, _ser = _ed25519()
        pub = Pub.from_public_bytes(base64.b64decode(public_key_b64))
        pub.verify(base64.b64decode(signature_b64), str(digest_hex).encode("utf-8"))
        return True
    except Exception:
        return False


# ── Trust registry: fingerprint → public key ──────────────────────────────────
_TRUSTED: dict[str, str] = {}
_TRUST_LOCK = threading.Lock()


def register_trusted_key(public_key_b64: str) -> str:
    fp = key_fingerprint(public_key_b64)
    with _TRUST_LOCK:
        _TRUSTED[fp] = public_key_b64
    return fp


def is_trusted(fingerprint: str) -> bool:
    with _TRUST_LOCK:
        return fingerprint in _TRUSTED


def trusted_public_key(fingerprint: str) -> Optional[str]:
    with _TRUST_LOCK:
        return _TRUSTED.get(fingerprint)


def clear_trusted_keys() -> None:
    """Test helper."""
    with _TRUST_LOCK:
        _TRUSTED.clear()


def verify_bundle_signature(
    *, digest_hex: str, signature: Optional[str], key_id: Optional[str]
) -> dict[str, Any]:
    """Verify a bundle's detached signature against a trusted key. Fail-closed.

    Returns ``{"ok": True, "key_id"}`` or ``{"ok": False, "error"}``.
    """
    if not signature or not key_id:
        return {"ok": False, "error": "bundle is unsigned"}
    pub = trusted_public_key(key_id)
    if pub is None:
        return {"ok": False, "error": f"signing key {key_id!r} is not in the trust registry"}
    if not verify_digest(pub, digest_hex, signature):
        return {"ok": False, "error": "signature verification failed"}
    return {"ok": True, "key_id": key_id}


def signature_required(profile: str) -> bool:
    return str(profile or "").strip().lower() in _PRODUCTION_PROFILES


def enforce_bundle_signature(
    *,
    profile: str,
    digest_hex: str,
    signature: Optional[str] = None,
    key_id: Optional[str] = None,
) -> dict[str, Any]:
    """Gate a bundle by profile: production requires a valid, trusted signature.

    Returns ``{"allowed", "verified", ...}``. In a production profile an
    unsigned/untrusted/invalid bundle is refused (``allowed=False``); in
    dev/single-instance it is allowed but ``verified=False``.
    """
    result = verify_bundle_signature(digest_hex=digest_hex, signature=signature, key_id=key_id)
    if result["ok"]:
        return {"allowed": True, "verified": True, "key_id": key_id}
    if signature_required(profile):
        return {
            "allowed": False,
            "verified": False,
            "error": result["error"],
            "profile": profile,
        }
    return {"allowed": True, "verified": False, "reason": result["error"]}


def generate_sbom(
    *,
    name: str,
    version: str,
    components: list[dict[str, Any]],
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """Emit a CycloneDX-lite SBOM for a bundle.

    ``components`` items: ``{name, version?, digest?, type?}`` — ``digest`` is a
    SHA-256 hex recorded as a component hash.
    """
    comps: list[dict[str, Any]] = []
    for component in components or []:
        comp: dict[str, Any] = {
            "type": component.get("type", "library"),
            "name": component.get("name"),
        }
        if component.get("version"):
            comp["version"] = component["version"]
        if component.get("digest"):
            comp["hashes"] = [{"alg": "SHA-256", "content": component["digest"]}]
        comps.append(comp)
    metadata: dict[str, Any] = {
        "component": {"type": "application", "name": name, "version": version},
    }
    if timestamp:
        metadata["timestamp"] = timestamp
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": metadata,
        "components": comps,
    }
