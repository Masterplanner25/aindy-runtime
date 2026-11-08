"""FR-59 (DEC-121) — whose key pays for a model call.

Phase 1 of ``docs/design/PROVIDER_KEYS_PER_USER_DESIGN.md``: the encrypted per-user store, the
resolver and its error types. Nothing calls the resolver for a real model call yet, and
``AINDY_PROVIDER_KEYS`` defaults to ``server``, so a deployment behaves exactly as before.

* **Encryption.** AES-GCM, a fresh 12-byte nonce per seal, with ``user_id:provider`` as
  associated data so a ciphertext copied to another row or provider will not open. The master
  keys are the secret ``provider_key_encryption_keys``, fetched through ``secret_broker``
  (``AINDY_SECRET_PROVIDER_KEY_ENCRYPTION_KEYS`` in the env backend): a comma-separated list of
  base64 32-byte keys. The first seals; every one opens. A row records which key sealed it.
* **Resolution.** ``resolve_provider_key(provider, user_id)``: in ``server`` mode, the server's
  key. In ``per_user`` mode, the caller's own key, else the server's only for an operator or a
  call with no user, else ``ProviderKeyRequired``. It never falls back to the server's key for a
  non-operator.
* **Nothing returns a stored key** except the resolver, to the call that spends it.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROVIDERS = ("openai", "anthropic", "deepseek", "azure_openai", "perplexity")
MODE_SERVER = "server"
MODE_PER_USER = "per_user"
MASTER_KEYS_SECRET = "provider_key_encryption_keys"
MAX_KEY_LENGTH = 4096
_OPERATOR_CACHE_TTL_SECONDS = 60.0
_operator_cache: dict[str, tuple[float, bool]] = {}

# The setting each provider's server key lives in. Perplexity has no runtime client, so its
# server key comes from the process env, where the app already keeps it.
_SERVER_KEY_SETTINGS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
}


class ProviderKeyError(Exception):
    """Base for this module's errors. A message never contains a key."""


class ProviderKeyRequired(ProviderKeyError):
    """The caller must add their own key for this provider (per-user mode, not an operator)."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"An API key for {provider} is required. Add one in your settings.")
        self.provider = provider


class ProviderKeyRejected(ProviderKeyError):
    """The provider refused the caller's own key. Never retried on the server's key."""

    def __init__(self, provider: str, key_hint: str = "") -> None:
        hint = f" (ending {key_hint})" if key_hint else ""
        super().__init__(f"{provider} rejected your API key{hint}. Replace it in your settings.")
        self.provider = provider
        self.key_hint = key_hint


class ProviderKeyConfigError(ProviderKeyError):
    """The master keys are missing or malformed."""


@dataclass(frozen=True)
class ResolvedKey:
    provider: str
    value: Optional[str]
    source: str  # "user" or "server"
    fingerprint: Optional[str]

    def __repr__(self) -> str:  # never print the key
        return f"ResolvedKey(provider={self.provider!r}, source={self.source!r}, fingerprint={self.fingerprint!r})"


# ── mode ─────────────────────────────────────────────────────────────────────


def provider_keys_mode() -> str:
    """``AINDY_PROVIDER_KEYS``: ``server`` (default) or ``per_user``. Anything else is ``server``,
    the behaviour every deployment had before, and is logged."""
    from AINDY.config import settings

    raw = str(getattr(settings, "AINDY_PROVIDER_KEYS", "") or MODE_SERVER).strip().lower()
    if raw in (MODE_SERVER, MODE_PER_USER):
        return raw
    logger.warning("AINDY_PROVIDER_KEYS=%r is not 'server' or 'per_user'; using 'server'", raw)
    return MODE_SERVER


def normalize_provider(provider: str) -> str:
    name = str(provider or "").strip().lower()
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r}. Expected one of: {', '.join(PROVIDERS)}")
    return name


# ── encryption ───────────────────────────────────────────────────────────────


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:12]


def load_master_keys() -> list[tuple[str, bytes]]:
    """``[(key_id, key)]`` from the broker, first is the sealing key. Raises if absent or malformed."""
    from AINDY.platform_layer.secret_broker import get_secret_broker

    raw = get_secret_broker().fetch(MASTER_KEYS_SECRET)
    if not raw:
        raise ProviderKeyConfigError(
            f"No master key: set the secret {MASTER_KEYS_SECRET!r} "
            "(AINDY_SECRET_PROVIDER_KEY_ENCRYPTION_KEYS in the env backend)."
        )
    keys: list[tuple[str, bytes]] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            key = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        except Exception as exc:
            raise ProviderKeyConfigError("A master key is not valid base64.") from exc
        if len(key) != 32:
            raise ProviderKeyConfigError("Each master key must decode to 32 bytes.")
        keys.append((_key_id(key), key))
    if not keys:
        raise ProviderKeyConfigError(f"The secret {MASTER_KEYS_SECRET!r} holds no keys.")
    return keys


def generate_master_key() -> str:
    """A new master key, for an operator to add in front of the list."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


def _aad(user_id: str, provider: str) -> bytes:
    return f"{uuid.UUID(str(user_id))}:{provider}".encode("utf-8")


def seal(raw_key: str, *, user_id: str, provider: str) -> tuple[str, str]:
    """Encrypt with the first master key. Returns ``(ciphertext_b64, key_version)``."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key_id, key = load_master_keys()[0]
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, raw_key.encode("utf-8"), _aad(user_id, provider))
    return base64.b64encode(nonce + sealed).decode("ascii"), key_id


def open_sealed(ciphertext: str, *, key_version: str, user_id: str, provider: str) -> str:
    """Decrypt a stored key. Raises ``ProviderKeyConfigError`` if no master key opens it."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    blob = base64.b64decode(ciphertext)
    nonce, sealed = blob[:12], blob[12:]
    for key_id, key in load_master_keys():
        if key_id != key_version:
            continue
        try:
            return AESGCM(key).decrypt(nonce, sealed, _aad(user_id, provider)).decode("utf-8")
        except InvalidTag as exc:
            raise ProviderKeyConfigError("A stored key failed to decrypt.") from exc
    raise ProviderKeyConfigError("The master key that sealed a stored key is no longer configured.")


def check_startup() -> None:
    """Refuse to start in ``per_user`` mode without usable master keys (design §3)."""
    if provider_keys_mode() != MODE_PER_USER:
        return
    try:
        load_master_keys()
    except ProviderKeyConfigError as exc:
        raise RuntimeError(f"AINDY_PROVIDER_KEYS=per_user, but {exc}") from exc


# ── the store ────────────────────────────────────────────────────────────────


def _public(row) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "key_hint": row.key_hint,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_user_provider_keys(db, user_id: str) -> list[dict[str, Any]]:
    from AINDY.db.models.user_provider_key import UserProviderKey

    rows = (
        db.query(UserProviderKey)
        .filter(UserProviderKey.user_id == uuid.UUID(str(user_id)))
        .order_by(UserProviderKey.provider)
        .all()
    )
    return [_public(row) for row in rows]


def set_user_provider_key(db, user_id: str, provider: str, raw_key: str) -> dict[str, Any]:
    """Store or replace a user's key. Raises ``ValueError`` for a bad key, without echoing it."""
    from AINDY.db.models.user_provider_key import UserProviderKey

    provider = normalize_provider(provider)
    key = str(raw_key or "").strip()
    if not key:
        raise ValueError("The key is empty.")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"The key is longer than {MAX_KEY_LENGTH} characters.")
    ciphertext, key_version = seal(key, user_id=str(user_id), provider=provider)
    owner = uuid.UUID(str(user_id))
    row = (
        db.query(UserProviderKey)
        .filter(UserProviderKey.user_id == owner, UserProviderKey.provider == provider)
        .first()
    )
    if row is None:
        row = UserProviderKey(user_id=owner, provider=provider)
        db.add(row)
    row.ciphertext = ciphertext
    row.key_version = key_version
    row.key_hint = key[-4:]
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _public(row)


def delete_user_provider_key(db, user_id: str, provider: str) -> bool:
    from AINDY.db.models.user_provider_key import UserProviderKey

    provider = normalize_provider(provider)
    deleted = (
        db.query(UserProviderKey)
        .filter(UserProviderKey.user_id == uuid.UUID(str(user_id)), UserProviderKey.provider == provider)
        .delete()
    )
    db.commit()
    return bool(deleted)


def rotate_master_key(db, *, dry_run: bool = True) -> dict[str, int]:
    """Re-seal every row not sealed with the first master key. Rows the configured keys cannot
    open are counted, never changed."""
    from AINDY.db.models.user_provider_key import UserProviderKey

    active_id = load_master_keys()[0][0]
    report = {"current": 0, "resealed": 0, "would_reseal": 0, "unreadable": 0}
    for row in db.query(UserProviderKey).all():
        if row.key_version == active_id:
            report["current"] += 1
            continue
        try:
            plain = open_sealed(row.ciphertext, key_version=row.key_version, user_id=str(row.user_id), provider=row.provider)
        except ProviderKeyConfigError:
            report["unreadable"] += 1
            continue
        if dry_run:
            report["would_reseal"] += 1
            continue
        row.ciphertext, row.key_version = seal(plain, user_id=str(row.user_id), provider=row.provider)
        report["resealed"] += 1
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return report


# ── resolution ───────────────────────────────────────────────────────────────


def _fingerprint(value: Optional[str]) -> Optional[str]:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16] if value else None


def _server_key(provider: str) -> ResolvedKey:
    from AINDY.config import settings

    name = _SERVER_KEY_SETTINGS[provider]
    value = getattr(settings, name, None) or os.environ.get(name) or None
    return ResolvedKey(provider=provider, value=value, source="server", fingerprint=_fingerprint(value))


def _is_operator(db, user_id: str) -> bool:
    """``User.is_admin``, read at resolve time and cached 60 s (as DEC-116). Fails closed."""
    now = time.monotonic()
    cached = _operator_cache.get(user_id)
    if cached is not None and now - cached[0] < _OPERATOR_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        from AINDY.db.models.user import User

        row = db.query(User.is_admin).filter(User.id == uuid.UUID(str(user_id))).first()
    except Exception as exc:
        logger.warning("provider key resolution: operator lookup failed: %s", exc)
        return False
    is_operator = bool(row and row[0])
    _operator_cache[user_id] = (now, is_operator)
    return is_operator


def resolve_provider_key(provider: str, user_id: Optional[str] = None, *, db=None) -> ResolvedKey:
    """Whose key pays for this call (design §5). Never falls back to the server's key for a
    non-operator. ``db`` is optional; without it the lookup opens and closes its own session."""
    provider = normalize_provider(provider)
    if provider_keys_mode() != MODE_PER_USER or not user_id:
        return _server_key(provider)  # server mode, or a call with no user (D5)

    owns_session = db is None
    if owns_session:
        from AINDY.db.database import SessionLocal

        db = SessionLocal()
    try:
        from AINDY.db.models.user_provider_key import UserProviderKey

        row = (
            db.query(UserProviderKey)
            .filter(UserProviderKey.user_id == uuid.UUID(str(user_id)), UserProviderKey.provider == provider)
            .first()
        )
        if row is not None:
            value = open_sealed(row.ciphertext, key_version=row.key_version, user_id=str(user_id), provider=provider)
            return ResolvedKey(provider=provider, value=value, source="user", fingerprint=_fingerprint(value))
        if _is_operator(db, str(user_id)):
            return _server_key(provider)
        raise ProviderKeyRequired(provider)
    finally:
        if owns_session:
            db.close()
