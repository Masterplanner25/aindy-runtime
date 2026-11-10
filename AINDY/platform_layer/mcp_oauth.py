"""OAuth for the MCP endpoint (FR-66 phase 2, DEC-129).

The runtime is its own authorization server, for ``/mcp`` only, so ChatGPT and claude.ai can add the
MCP server by URL. Built on the ``mcp`` SDK's handlers (``/authorize``, ``/token``, ``/register``,
``/revoke``, and both metadata documents); this module is the provider they call, the sign-in and
consent page, and the storage. Off unless ``AINDY_MCP_OAUTH`` is on (D6). Design:
``docs/design/MCP_REMOTE_TRANSPORT_DESIGN.md`` §5–§8.

* **Access token:** a 1-hour HS256 JWT signed with keys derived from the signing keys under their
  own domain, so it is never a session token and a session token is never one of these. Its
  audience is this server's resource URL. Each use re-checks its grant, the user's ``is_active``
  and ``token_version`` (cached a few seconds per grant; an unreadable grant is a refusal).
* **Grant:** one row per consent. The authorization code (single-use, 60 seconds) and the refresh
  token (30 days, rotated on every use) are stored as SHA-256 hashes. Replaying a rotated refresh
  token revokes the grant. A new consent for the same user and client revokes the earlier grant.
* **Client:** a dynamic registration (open, capped and rate-limited; D4), or one an operator
  registers with ``aindy-runtime mcp-oauth register-client``. A client secret is stored sealed.
* **Scopes** are the runtime's scope names, plus a capability's own name for a tool whose
  capability no scope names (DEC-130). A token is held to them by ``key_scopes_authorize``,
  exactly as a platform key is (DEC-126).

Every setting is read per call, never at import.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ACCESS_TOKEN_TTL_S = 3600
REFRESH_TOKEN_TTL_S = 30 * 24 * 3600
CODE_TTL_S = 60
PENDING_TTL_S = 600
CLIENT_PRUNE_AFTER_DAYS = 30
GRANT_CHECK_CACHE_S = 5.0
CONSENT_ATTEMPTS_PER_MINUTE = 10
CONSENT_PATH = "/oauth/consent"

_ACCESS_DOMAIN = b"aindy-mcp-oauth-access-v1"
_PENDING_DOMAIN = b"aindy-mcp-oauth-pending-v1"
_CLIENT_SECRET_DOMAIN = b"aindy-mcp-oauth-client-secret-v1"

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}

# A capability whose authorizing scopes don't include a scope of its own name gets the one
# here on the consent page. Every other mapped capability is its own scope name.
_CANONICAL_SCOPE = {"memory.search": "memory.read", "flow.run": "flow.execute"}


# ── configuration ──────────────────────────────────────────────────────────────────────────────


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def oauth_enabled() -> bool:
    """D6: off unless ``AINDY_MCP_OAUTH`` is on."""
    return _flag("AINDY_MCP_OAUTH")


def registration_open() -> bool:
    """D4: dynamic registration is open unless ``AINDY_MCP_OAUTH_CLIENTS=closed``."""
    return os.getenv("AINDY_MCP_OAUTH_CLIENTS", "open").strip().lower() != "closed"


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, "").strip() or default))
    except ValueError:
        return default


def max_clients() -> int:
    return _int_env("AINDY_MCP_OAUTH_MAX_CLIENTS", 500)


def registrations_per_hour() -> int:
    return _int_env("AINDY_MCP_OAUTH_REGISTRATIONS_PER_HOUR", 30)


def public_url() -> str:
    """``AINDY_MCP_PUBLIC_URL``: the origin clients reach this server at, and the token issuer.

    Required with OAuth on. ``https://`` only, except a loopback host (local testing). An origin,
    no path: the endpoints are served at the root of this app.
    """
    raw = os.getenv("AINDY_MCP_PUBLIC_URL", "").strip().rstrip("/")
    if not raw:
        raise RuntimeError(
            "AINDY_MCP_OAUTH is on but AINDY_MCP_PUBLIC_URL is not set: the https origin clients "
            "reach this server at (e.g. https://mcp.example.com). It is the token issuer."
        )
    parsed = urlparse(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise RuntimeError(f"AINDY_MCP_PUBLIC_URL must be an absolute URL, got {raw!r}")
    if parsed.scheme != "https" and parsed.hostname not in _LOOPBACK:
        raise RuntimeError(
            f"AINDY_MCP_PUBLIC_URL must be https:// (got {raw!r}); plain http is allowed only for a "
            "loopback host."
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RuntimeError(f"AINDY_MCP_PUBLIC_URL must be an origin with no path, got {raw!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def public_host() -> str:
    """The ``Host`` value clients send to the public URL, for the transport's Host check."""
    return urlparse(public_url()).netloc


def scope_for_capability(capability: str) -> str:
    """The one scope the consent page offers for a tool's capability (DEC-130)."""
    from AINDY.auth.api_key_auth import DISPATCH_CAPABILITY_SCOPES

    authorizing = DISPATCH_CAPABILITY_SCOPES.get(capability)
    if not authorizing:
        return capability  # no scope names it: the capability's own name authorizes it
    if capability in _CANONICAL_SCOPE:
        return _CANONICAL_SCOPE[capability]
    return capability if capability in authorizing else sorted(authorizing)[0]


@dataclass(frozen=True)
class OAuthConfig:
    issuer: str
    resource: str
    path: str
    offered_scopes: tuple[str, ...]
    default_scopes: tuple[str, ...]
    scope_tools: dict = field(default_factory=dict)  # scope -> [tool names], for the consent page


def build_config(path: str, allowlist: list[str]) -> OAuthConfig:
    """The issuer, the resource, and the scopes the exposed tools need (read-only ones by default)."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    issuer = public_url()
    scope_tools: dict[str, list[str]] = {}
    read_scopes: list[str] = []
    for name in allowlist:
        entry = SYSCALL_REGISTRY.get(name)
        if entry is None:
            continue
        scope = scope_for_capability(entry.capability)
        scope_tools.setdefault(scope, []).append(name)
        if getattr(entry, "read_only", False) and scope not in read_scopes:
            read_scopes.append(scope)
    offered = tuple(scope_tools)
    if not offered:
        raise RuntimeError("[mcp-oauth] no tools exposed, so no scopes to offer")
    return OAuthConfig(
        issuer=issuer,
        resource=f"{issuer}{path}",
        path=path,
        offered_scopes=offered,
        default_scopes=tuple(read_scopes),
        scope_tools=scope_tools,
    )


# ── keys and tokens ────────────────────────────────────────────────────────────────────────────


def _domain_keys(domain: bytes) -> list[str]:
    """The current key first, then every key still inside the rotation grace window."""
    from AINDY.services.auth_service import _derive_domain_key, signing_key, verification_keys

    keys: list[str] = []
    for secret in [signing_key(), *verification_keys()]:
        derived = _derive_domain_key(secret, domain)
        if derived not in keys:
            keys.append(derived)
    return keys


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite hands back naive datetimes for a timezone-aware column."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sign(claims: dict, domain: bytes) -> str:
    import jwt

    return jwt.encode(claims, _domain_keys(domain)[0], algorithm="HS256")


def _verify(token: str, domain: bytes, **kwargs) -> Optional[dict]:
    import jwt

    for key in _domain_keys(domain):
        try:
            return jwt.decode(token, key, algorithms=["HS256"], options={"require": ["exp"]}, **kwargs)
        except jwt.InvalidSignatureError:
            continue
        except jwt.PyJWTError:
            return None
    return None


def mint_access_token(cfg: OAuthConfig, *, user_id: str, client_id: str, grant_id: str,
                      scopes: list[str], token_version: int) -> tuple[str, int]:
    now = int(time.time())
    expires_at = now + ACCESS_TOKEN_TTL_S
    claims = {
        "typ": "mcp_access", "iss": cfg.issuer, "aud": cfg.resource, "sub": user_id,
        "cid": client_id, "gid": grant_id, "scope": " ".join(scopes), "tv": int(token_version),
        "iat": now, "exp": expires_at, "jti": secrets.token_hex(8),
    }
    return _sign(claims, _ACCESS_DOMAIN), expires_at


def decode_access_token(cfg: OAuthConfig, token: str) -> Optional[dict]:
    """The claims of a valid, unexpired MCP access token for this server, or None."""
    claims = _verify(token, _ACCESS_DOMAIN, audience=cfg.resource, issuer=cfg.issuer)
    if not claims or claims.get("typ") != "mcp_access":
        return None
    return claims


def _fernet():
    from cryptography.fernet import Fernet, MultiFernet

    return MultiFernet([
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(k.encode("utf-8")).digest()))
        for k in _domain_keys(_CLIENT_SECRET_DOMAIN)
    ])


def seal_client_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def open_client_secret(ciphertext: str) -> Optional[str]:
    """None once the key that sealed it has left the rotation grace window: the client re-registers."""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None


# ── storage (synchronous; the provider runs these in a worker thread) ─────────────────────────


def _session():
    from AINDY.db.database import SessionLocal

    return SessionLocal()


def _redirect_uri_allowed(uri: str) -> bool:
    """OAuth 2.1: https, or http to a loopback host (a native or local client)."""
    parsed = urlparse(str(uri))
    if parsed.fragment:
        return False
    if parsed.scheme == "https":
        return bool(parsed.hostname)
    return parsed.scheme == "http" and parsed.hostname in _LOOPBACK


def load_client(client_id: str):
    from mcp.shared.auth import OAuthClientInformationFull

    from AINDY.db.models.mcp_oauth import McpOAuthClient

    db = _session()
    try:
        row = db.get(McpOAuthClient, client_id)
        if row is None:
            return None
        info = json.loads(row.client_info)
        if row.client_secret_ciphertext:
            secret = open_client_secret(row.client_secret_ciphertext)
            if secret is None:
                logger.warning("[mcp-oauth] client %s's secret is sealed under a retired key", client_id)
                return None
            info["client_secret"] = secret
        return OAuthClientInformationFull.model_validate(info)
    finally:
        db.close()


def store_client(client_info, *, operator: bool = False) -> None:
    """Persist a registration. ``operator=True`` (the CLI) skips the open/closed switch and the caps."""
    from mcp.server.auth.provider import RegistrationError
    from sqlalchemy import exists

    from AINDY.db.models.mcp_oauth import McpOAuthClient, McpOAuthGrant

    if not operator and not registration_open():
        raise RegistrationError("invalid_client_metadata", "client registration is closed on this server")
    bad = [str(u) for u in (client_info.redirect_uris or []) if not _redirect_uri_allowed(str(u))]
    if bad or not client_info.redirect_uris:
        raise RegistrationError(
            "invalid_redirect_uri",
            f"redirect URIs must be https, or http to a loopback host: {bad or 'none given'}",
        )

    db = _session()
    try:
        now = _now()
        # D4: prune registrations that never led to a grant.
        stale = now - timedelta(days=CLIENT_PRUNE_AFTER_DAYS)
        db.query(McpOAuthClient).filter(
            McpOAuthClient.created_at < stale,
            ~exists().where(McpOAuthGrant.client_id == McpOAuthClient.client_id),
        ).delete(synchronize_session=False)
        if not operator:
            if db.query(McpOAuthClient).count() >= max_clients():
                raise RegistrationError("invalid_client_metadata", "this server has reached its client limit")
            recent = db.query(McpOAuthClient).filter(
                McpOAuthClient.created_at >= now - timedelta(hours=1)
            ).count()
            if recent >= registrations_per_hour():
                raise RegistrationError("invalid_client_metadata", "too many registrations; try again later")
        info = client_info.model_dump(mode="json", exclude={"client_secret"}, exclude_none=True)
        db.add(McpOAuthClient(
            client_id=client_info.client_id,
            client_info=json.dumps(info),
            client_secret_ciphertext=seal_client_secret(client_info.client_secret) if client_info.client_secret else None,
            created_at=now,
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


#: Why a grant ended — the `reason` on `mcp_grant.revoked`.
REVOKE_REASONS = ("user", "client", "superseded", "refresh_replay", "account_changed")


def _grant_event_payload(grant, reason: Optional[str] = None) -> dict:
    payload = {"grant_id": str(grant.id), "client_id": grant.client_id, "scopes": (grant.scopes or "").split()}
    if reason is not None:
        assert reason in REVOKE_REASONS, reason
        payload["reason"] = reason
    return payload


def _audit(db, event_type: str, user_id, payload: dict) -> None:
    """Write the audit row on ``db`` AFTER its change committed. Best effort: a failed audit
    write is logged, never undoes a revocation or a consent (emit rolls back only its own row)."""
    from AINDY.core.system_event_service import emit_system_event

    try:
        emit_system_event(
            db=db, event_type=event_type, user_id=str(user_id), source="mcp.oauth",
            payload=payload, required=False, skip_memory_capture=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[mcp-oauth] could not write %s for grant %s: %s", event_type, payload.get("grant_id"), exc)


def _revoked(db, grants, reason: str) -> None:
    from AINDY.core.system_event_types import SystemEventTypes

    for grant in grants:
        _audit(db, SystemEventTypes.MCP_GRANT_REVOKED, grant.user_id, _grant_event_payload(grant, reason))


def create_grant_with_code(*, user_id: str, pending: dict, scopes: list[str]) -> str:
    """A new grant for (user, client) holding a fresh code; an earlier live grant is revoked."""
    from AINDY.core.system_event_types import SystemEventTypes
    from AINDY.db.models.mcp_oauth import McpOAuthGrant
    from AINDY.db.models.user import User

    code = secrets.token_urlsafe(32)
    db = _session()
    try:
        user = db.get(User, uuid.UUID(user_id))
        now = _now()
        earlier = db.query(McpOAuthGrant).filter(
            McpOAuthGrant.user_id == uuid.UUID(user_id),
            McpOAuthGrant.client_id == pending["cid"],
            McpOAuthGrant.revoked_at.is_(None),
        ).all()
        for grant in earlier:
            grant.revoked_at = now
            _forget_grant(str(grant.id))
        made = McpOAuthGrant(
            id=uuid.uuid4(),
            user_id=uuid.UUID(user_id),
            client_id=pending["cid"],
            scopes=" ".join(scopes),
            resource=pending.get("res"),
            token_version=int(getattr(user, "token_version", 0) or 0),
            code_hash=_sha256(code),
            code_challenge=pending["cc"],
            code_redirect_uri=pending["ru"],
            code_redirect_explicit=bool(pending.get("rx", True)),
            code_expires_at=now + timedelta(seconds=CODE_TTL_S),
            created_at=now,
        )
        db.add(made)
        db.commit()
        _revoked(db, earlier, "superseded")
        created = _grant_event_payload(made)
        created["redirect_host"] = urlparse(str(pending["ru"])).netloc
        _audit(db, SystemEventTypes.MCP_GRANT_CREATED, user_id, created)
        return code
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _grant_by(db, **criteria):
    from AINDY.db.models.mcp_oauth import McpOAuthGrant

    query = db.query(McpOAuthGrant)
    for column, value in criteria.items():
        query = query.filter(getattr(McpOAuthGrant, column) == value)
    return query.first()


def load_code(client_id: str, code: str):
    from mcp.server.auth.provider import AuthorizationCode

    db = _session()
    try:
        grant = _grant_by(db, code_hash=_sha256(code))
        if grant is None or grant.client_id != client_id or grant.revoked_at is not None:
            return None
        return AuthorizationCode(
            code=code,
            scopes=grant.scopes.split(),
            expires_at=_aware(grant.code_expires_at).timestamp(),
            client_id=grant.client_id,
            code_challenge=grant.code_challenge,
            redirect_uri=grant.code_redirect_uri,
            redirect_uri_provided_explicitly=bool(grant.code_redirect_explicit),
            resource=grant.resource,
            subject=str(grant.user_id),
        )
    finally:
        db.close()


def _issue(cfg: OAuthConfig, db, grant, scopes: list[str], *, consume: dict) -> Any:
    """Rotate the grant's refresh token (only if ``consume`` still matches) and mint an access token."""
    from mcp.server.auth.provider import TokenError
    from mcp.shared.auth import OAuthToken

    from AINDY.db.models.mcp_oauth import McpOAuthGrant

    refresh = secrets.token_urlsafe(32)
    now = _now()
    updates = {
        "refresh_token_hash": _sha256(refresh),
        "previous_refresh_token_hash": grant.refresh_token_hash,
        "refresh_expires_at": now + timedelta(seconds=REFRESH_TOKEN_TTL_S),
        "last_used_at": now,
        "code_hash": None,
        "code_challenge": None,
        "code_expires_at": None,
    }
    query = db.query(McpOAuthGrant).filter(McpOAuthGrant.id == grant.id, McpOAuthGrant.revoked_at.is_(None))
    for column, value in consume.items():
        query = query.filter(getattr(McpOAuthGrant, column) == value)
    if query.update(updates, synchronize_session=False) != 1:
        db.rollback()
        raise TokenError("invalid_grant", "this grant was already used or revoked")
    db.commit()
    access, _ = mint_access_token(
        cfg, user_id=str(grant.user_id), client_id=grant.client_id, grant_id=str(grant.id),
        scopes=scopes, token_version=grant.token_version,
    )
    return OAuthToken(
        access_token=access, token_type="Bearer", expires_in=ACCESS_TOKEN_TTL_S,
        scope=" ".join(scopes), refresh_token=refresh,
    )


def _user_still_valid(db, grant) -> bool:
    from AINDY.db.models.user import User

    user = db.get(User, grant.user_id)
    return bool(user and user.is_active and int(user.token_version or 0) == int(grant.token_version))


def exchange_code(cfg: OAuthConfig, client_id: str, code: str):
    from mcp.server.auth.provider import TokenError

    db = _session()
    try:
        grant = _grant_by(db, code_hash=_sha256(code))
        if grant is None or grant.client_id != client_id or grant.revoked_at is not None:
            raise TokenError("invalid_grant", "unknown authorization code")
        if not _user_still_valid(db, grant):
            raise TokenError("invalid_grant", "the account changed since consent")
        return _issue(cfg, db, grant, grant.scopes.split(), consume={"code_hash": _sha256(code)})
    finally:
        db.close()


def load_refresh(client_id: str, token: str):
    """The refresh token, or None. A rotated-out token being replayed revokes its grant."""
    from mcp.server.auth.provider import RefreshToken

    db = _session()
    try:
        digest = _sha256(token)
        grant = _grant_by(db, refresh_token_hash=digest)
        if grant is None:
            replayed = _grant_by(db, previous_refresh_token_hash=digest)
            if replayed is not None and replayed.revoked_at is None:
                logger.warning("[mcp-oauth] a rotated refresh token was replayed; revoking grant %s", replayed.id)
                replayed.revoked_at = _now()
                _forget_grant(str(replayed.id))
                db.commit()
                _revoked(db, [replayed], "refresh_replay")
            return None
        if grant.client_id != client_id or grant.revoked_at is not None:
            return None
        expires = _aware(grant.refresh_expires_at)
        return RefreshToken(
            token=token, client_id=grant.client_id, scopes=grant.scopes.split(),
            expires_at=int(expires.timestamp()) if expires else None, subject=str(grant.user_id),
        )
    finally:
        db.close()


def exchange_refresh(cfg: OAuthConfig, client_id: str, token: str, scopes: list[str]):
    from mcp.server.auth.provider import TokenError

    db = _session()
    try:
        digest = _sha256(token)
        grant = _grant_by(db, refresh_token_hash=digest)
        if grant is None or grant.client_id != client_id or grant.revoked_at is not None:
            raise TokenError("invalid_grant", "unknown refresh token")
        if not _user_still_valid(db, grant):
            grant.revoked_at = _now()
            _forget_grant(str(grant.id))
            db.commit()
            _revoked(db, [grant], "account_changed")
            raise TokenError("invalid_grant", "the account changed since consent")
        return _issue(cfg, db, grant, scopes, consume={"refresh_token_hash": digest})
    finally:
        db.close()


# A grant's liveness, cached briefly per (grant, token_version) so a call doesn't cost a read each time.
_grant_cache: dict[tuple[str, int], tuple[float, bool]] = {}
_grant_cache_lock = threading.Lock()


def _forget_grant(grant_id: str) -> None:
    with _grant_cache_lock:
        for key in [k for k in _grant_cache if k[0] == grant_id]:
            del _grant_cache[key]


def grant_is_live(grant_id: str, token_version: int) -> bool:
    """Not revoked, the user active, and the password unchanged since consent. Fails closed.

    Revoking in this process forgets the cached answer at once; a change made elsewhere (another
    process, a password change) is seen within ``GRANT_CHECK_CACHE_S``.
    """
    from AINDY.db.models.mcp_oauth import McpOAuthGrant

    key = (grant_id, int(token_version))
    now = time.monotonic()
    with _grant_cache_lock:
        cached = _grant_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    try:
        db = _session()
        try:
            grant = db.get(McpOAuthGrant, uuid.UUID(grant_id))
            live = bool(
                grant is not None and grant.revoked_at is None
                and int(grant.token_version) == int(token_version)
                and _user_still_valid(db, grant)
            )
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — unreadable means refused
        logger.warning("[mcp-oauth] grant %s could not be read, refusing: %s", grant_id, exc)
        return False
    with _grant_cache_lock:
        _grant_cache[key] = (now + GRANT_CHECK_CACHE_S, live)
    return live


def revoke_grant_id(grant_id: str) -> bool:
    from AINDY.db.models.mcp_oauth import McpOAuthGrant

    db = _session()
    try:
        grant = db.get(McpOAuthGrant, uuid.UUID(grant_id))
        if grant is None or grant.revoked_at is not None:
            return False
        grant.revoked_at = _now()
        db.commit()
        _revoked(db, [grant], "client")
        return True
    finally:
        _forget_grant(grant_id)
        db.close()


def revoke_by_refresh(token: str) -> None:
    db = _session()
    try:
        grant = _grant_by(db, refresh_token_hash=_sha256(token))
        if grant is not None and grant.revoked_at is None:
            grant.revoked_at = _now()
            _forget_grant(str(grant.id))
            db.commit()
            _revoked(db, [grant], "client")
    finally:
        db.close()


# ── connected apps (the main API's /auth/mcp-grants) ──────────────────────────────────────────


def list_user_grants(db, user_id: str) -> list[dict]:
    """The user's live grants, newest first: which client, which scopes, when."""
    from AINDY.db.models.mcp_oauth import McpOAuthClient, McpOAuthGrant

    rows = (
        db.query(McpOAuthGrant, McpOAuthClient)
        .join(McpOAuthClient, McpOAuthClient.client_id == McpOAuthGrant.client_id)
        .filter(McpOAuthGrant.user_id == uuid.UUID(user_id), McpOAuthGrant.revoked_at.is_(None))
        .order_by(McpOAuthGrant.created_at.desc())
        .all()
    )
    out = []
    for grant, client in rows:
        info = json.loads(client.client_info or "{}")
        redirect = (info.get("redirect_uris") or [""])[0]
        out.append({
            "id": str(grant.id),
            "client_id": grant.client_id,
            "client_name": info.get("client_name") or "(unnamed client)",
            "redirect_host": urlparse(str(redirect)).netloc,
            "scopes": grant.scopes.split(),
            "created_at": _aware(grant.created_at).isoformat(),
            "last_used_at": _aware(grant.last_used_at).isoformat() if grant.last_used_at else None,
        })
    return out


def revoke_user_grant(db, user_id: str, grant_id: str) -> bool:
    """End one of the caller's own grants. False when it isn't theirs, doesn't exist, or already ended."""
    from AINDY.db.models.mcp_oauth import McpOAuthGrant

    try:
        gid = uuid.UUID(grant_id)
    except ValueError:
        return False
    grant = db.get(McpOAuthGrant, gid)
    if grant is None or str(grant.user_id) != str(uuid.UUID(user_id)) or grant.revoked_at is not None:
        return False
    from AINDY.platform_layer.trace_context import is_pipeline_active
    from AINDY.core.execution_signal_helper import queue_system_event
    from AINDY.core.system_event_types import SystemEventTypes

    grant.revoked_at = _now()
    if is_pipeline_active():
        # In the route's pipeline the audit row rides this same commit (DEC-060): the
        # revocation and its record land together. A failed add never rolls the session back.
        queue_system_event(
            db=db, event_type=SystemEventTypes.MCP_GRANT_REVOKED, user_id=str(grant.user_id),
            source="mcp.oauth", payload=_grant_event_payload(grant, "user"), required=False,
        )
        db.commit()
    else:
        # Outside one, an emit that fails rolls the session back, so commit the revocation first.
        db.commit()
        _revoked(db, [grant], "user")
    _forget_grant(str(gid))
    return True


# ── the provider the SDK's handlers call ───────────────────────────────────────────────────────


def _pending_token(cfg: OAuthConfig, client, params, scopes: list[str]) -> str:
    claims = {
        "typ": "mcp_pending", "cid": client.client_id, "ru": str(params.redirect_uri),
        "rx": bool(params.redirect_uri_provided_explicitly), "cc": params.code_challenge,
        "st": params.state, "sc": scopes, "res": cfg.resource,
        "exp": int(time.time()) + PENDING_TTL_S,
    }
    return _sign(claims, _PENDING_DOMAIN)


def read_pending(token: str) -> Optional[dict]:
    claims = _verify(token, _PENDING_DOMAIN)
    return claims if claims and claims.get("typ") == "mcp_pending" else None


def _make_provider(cfg: OAuthConfig):
    import anyio
    from mcp.server.auth.provider import AccessToken, AuthorizeError

    run = anyio.to_thread.run_sync

    class McpOAuthProvider:
        async def get_client(self, client_id: str):
            return await run(lambda: load_client(client_id))

        async def register_client(self, client_info) -> None:
            await run(lambda: store_client(client_info))

        async def authorize(self, client, params) -> str:
            if params.resource and params.resource.rstrip("/") != cfg.resource:
                raise AuthorizeError("invalid_request", f"this server is {cfg.resource}, not {params.resource}")
            requested = params.scopes or (client.scope.split() if client.scope else list(cfg.default_scopes))
            scopes = [s for s in requested if s in cfg.offered_scopes]
            if not scopes:
                raise AuthorizeError("invalid_scope", "none of the requested scopes is offered here")
            pending = _pending_token(cfg, client, params, scopes)
            return f"{cfg.issuer}{CONSENT_PATH}?p={pending}"

        async def load_authorization_code(self, client, authorization_code: str):
            return await run(lambda: load_code(client.client_id, authorization_code))

        async def exchange_authorization_code(self, client, authorization_code):
            return await run(lambda: exchange_code(cfg, client.client_id, authorization_code.code))

        async def load_refresh_token(self, client, refresh_token: str):
            return await run(lambda: load_refresh(client.client_id, refresh_token))

        async def exchange_refresh_token(self, client, refresh_token, scopes):
            return await run(lambda: exchange_refresh(cfg, client.client_id, refresh_token.token, list(scopes)))

        async def load_access_token(self, token: str):
            claims = decode_access_token(cfg, token)
            if not claims:
                return None
            if not await run(lambda: grant_is_live(str(claims["gid"]), int(claims.get("tv", -1)))):
                return None
            return AccessToken(
                token=token, client_id=str(claims["cid"]), scopes=str(claims.get("scope", "")).split(),
                expires_at=int(claims["exp"]), resource=cfg.resource, subject=str(claims["sub"]),
                claims={"gid": str(claims["gid"])},
            )

        async def verify_token(self, token: str):  # TokenVerifier, for the bearer backend
            return await self.load_access_token(token)

        async def revoke_token(self, token) -> None:
            gid = (getattr(token, "claims", None) or {}).get("gid")
            if gid:
                await run(lambda: revoke_grant_id(str(gid)))
            else:
                await run(lambda: revoke_by_refresh(token.token))

    return McpOAuthProvider()


# ── the sign-in and consent page ───────────────────────────────────────────────────────────────


_attempts: dict[str, deque] = defaultdict(deque)
_attempts_lock = threading.Lock()


def _too_many_attempts(ip: str) -> bool:
    now = time.monotonic()
    with _attempts_lock:
        window = _attempts[ip]
        while window and window[0] < now - 60:
            window.popleft()
        if len(window) >= CONSENT_ATTEMPTS_PER_MINUTE:
            return True
        window.append(now)
        return False


_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
}


def _page(cfg: OAuthConfig, *, pending_token: str, pending: dict, client_name: str, csrf: str,
          error: str = "", status: int = 200):
    from starlette.responses import HTMLResponse

    e = html.escape
    redirect_host = urlparse(pending["ru"]).netloc
    rows = []
    for scope in pending["sc"]:
        tools = ", ".join(cfg.scope_tools.get(scope, []))
        checked = "checked" if scope in cfg.default_scopes else ""
        rows.append(
            f'<label><input type="checkbox" name="scope" value="{e(scope)}" {checked}> '
            f"<b>{e(scope)}</b> <small>{e(tools)}</small></label>"
        )
    body = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Connect to A.I.N.D.Y.</title>
<style>body{{font:16px system-ui,sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem;color:#1a1a1a;background:#fff}}
label{{display:block;margin:.4rem 0}} input[type=email],input[type=password]{{width:100%;padding:.5rem;margin:.2rem 0 .8rem;box-sizing:border-box}}
.host{{font-size:1.25rem;font-weight:700;word-break:break-all}} .err{{color:#a00}} button{{padding:.6rem 1rem;margin-right:.5rem}}</style></head><body>
<h1>Connect an app to your account</h1>
<p>An app is asking to use your A.I.N.D.Y. account through its MCP server. You'll be sent back to:</p>
<p class="host">{e(redirect_host)}</p>
<p>It calls itself <i>{e(client_name)}</i>. Apps choose their own names, so check the address above.</p>
{f'<p class="err">{e(error)}</p>' if error else ''}
<form method="post" action="{CONSENT_PATH}">
<input type="hidden" name="p" value="{e(pending_token)}"><input type="hidden" name="csrf" value="{e(csrf)}">
<fieldset><legend>Allow it to</legend>{''.join(rows)}</fieldset>
<label>Email <input type="email" name="email" autocomplete="username" required></label>
<label>Password <input type="password" name="password" autocomplete="current-password" required></label>
<button type="submit" name="action" value="approve">Allow</button>
<button type="submit" name="action" value="deny" formnovalidate>Deny</button>
</form></body></html>"""
    response = HTMLResponse(body, status_code=status, headers=_PAGE_HEADERS)
    response.set_cookie("mcp_oauth_csrf", csrf, httponly=True, samesite="strict", secure=cfg.issuer.startswith("https://"),
                        path=CONSENT_PATH, max_age=PENDING_TTL_S)
    return response


def _consent_routes(cfg: OAuthConfig) -> list:
    import anyio
    from mcp.server.auth.provider import construct_redirect_uri
    from starlette.responses import PlainTextResponse, RedirectResponse
    from starlette.routing import Route

    def _client_name(client_id: str) -> str:
        client = load_client(client_id)
        return (client.client_name if client and client.client_name else "(unnamed client)")

    async def consent(request):
        if request.method == "GET":
            token = request.query_params.get("p", "")
            pending = read_pending(token)
            if not pending:
                return PlainTextResponse("This sign-in link has expired. Start again from the app.", 400, headers=_PAGE_HEADERS)
            name = await anyio.to_thread.run_sync(lambda: _client_name(pending["cid"]))
            return _page(cfg, pending_token=token, pending=pending, client_name=name, csrf=secrets.token_urlsafe(24))

        form = await request.form()
        token = str(form.get("p", ""))
        pending = read_pending(token)
        if not pending:
            return PlainTextResponse("This sign-in link has expired. Start again from the app.", 400, headers=_PAGE_HEADERS)
        cookie, field_value = request.cookies.get("mcp_oauth_csrf", ""), str(form.get("csrf", ""))
        if not cookie or not secrets.compare_digest(cookie, field_value):
            return PlainTextResponse("The form expired. Reload the page and try again.", 400, headers=_PAGE_HEADERS)

        if form.get("action") == "deny":
            return RedirectResponse(
                construct_redirect_uri(pending["ru"], error="access_denied", state=pending.get("st")),
                status_code=302, headers=_PAGE_HEADERS,
            )

        name = await anyio.to_thread.run_sync(lambda: _client_name(pending["cid"]))
        ip = request.client.host if request.client else "?"

        def again(message: str, status: int):
            return _page(cfg, pending_token=token, pending=pending, client_name=name,
                         csrf=secrets.token_urlsafe(24), error=message, status=status)

        if _too_many_attempts(ip):
            return again("Too many attempts. Wait a minute and try again.", 429)
        chosen = [s for s in form.getlist("scope") if s in pending["sc"]]
        if not chosen:
            return again("Choose at least one thing to allow.", 400)

        def _sign_in() -> Optional[str]:
            from fastapi import HTTPException

            from AINDY.services.auth_service import authenticate_user

            db = _session()
            try:
                return str(authenticate_user(str(form.get("email", "")), str(form.get("password", "")), db).id)
            except HTTPException:
                return None
            finally:
                db.close()

        user_id = await anyio.to_thread.run_sync(_sign_in)
        if not user_id:
            return again("Sign-in failed.", 401)
        code = await anyio.to_thread.run_sync(
            lambda: create_grant_with_code(user_id=user_id, pending=pending, scopes=chosen)
        )
        logger.info("[mcp-oauth] user %s granted %s to client %s", user_id, chosen, pending["cid"])
        return RedirectResponse(
            construct_redirect_uri(pending["ru"], code=code, state=pending.get("st")),
            status_code=302, headers=_PAGE_HEADERS,
        )

    return [Route(CONSENT_PATH, endpoint=consent, methods=["GET", "POST"])]


# ── wiring: the routes and middleware the streamable HTTP app takes ────────────────────────────


class _BearerGate:
    """Require a valid MCP access token on the MCP endpoint only; the OAuth routes stay open.

    nodus-mcp's ``middleware`` wraps the whole app, and the SDK's ``RequireAuthMiddleware`` would
    then refuse ``/authorize`` too, so it runs here for ``path`` alone. Its ``401`` carries the
    ``WWW-Authenticate`` header that points clients at the protected-resource metadata.
    """

    def __init__(self, app, path: str, resource_metadata_url):
        from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware

        self.app = app
        self.path = path
        self.require = RequireAuthMiddleware(app, [], resource_metadata_url)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == self.path:
            await self.require(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def http_routes_and_middleware(cfg: OAuthConfig) -> tuple[list, list]:
    """Everything the OAuth layer adds to the streamable HTTP app (FR-66 phase 2)."""
    import inspect

    from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
    from mcp.server.auth.routes import (
        build_resource_metadata_url,
        create_auth_routes,
        create_protected_resource_routes,
    )
    from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
    from pydantic import AnyHttpUrl
    from starlette.middleware import Middleware
    from starlette.middleware.authentication import AuthenticationMiddleware

    provider = _make_provider(cfg)
    issuer, resource = AnyHttpUrl(cfg.issuer), AnyHttpUrl(cfg.resource)
    routes = [
        *create_auth_routes(
            provider,
            issuer_url=issuer,
            client_registration_options=ClientRegistrationOptions(
                enabled=registration_open(),
                valid_scopes=list(cfg.offered_scopes),
                default_scopes=list(cfg.default_scopes),
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
        *create_protected_resource_routes(
            resource_url=resource, authorization_servers=[issuer], scopes_supported=list(cfg.offered_scopes),
        ),
        *_consent_routes(cfg),
    ]
    backend_kwargs = {}
    if "resource_server_url" in inspect.signature(BearerAuthBackend.__init__).parameters:  # mcp 2.x
        backend_kwargs["resource_server_url"] = resource
    middleware = [
        Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(provider, **backend_kwargs)),
        Middleware(_BearerGate, path=cfg.path, resource_metadata_url=build_resource_metadata_url(resource)),
    ]
    return routes, middleware


def register_client_cli(*, name: str, redirect_uris: list[str], public: bool) -> dict:
    """``aindy-runtime mcp-oauth register-client``: an operator-registered client, for closed mode.

    Returns the client id, and the secret once (it is stored sealed and never shown again).
    """
    from mcp.shared.auth import OAuthClientInformationFull

    secret = None if public else secrets.token_hex(32)
    info = OAuthClientInformationFull(
        client_id=str(uuid.uuid4()),
        client_secret=secret,
        client_id_issued_at=int(time.time()),
        client_name=name,
        redirect_uris=redirect_uris,
        token_endpoint_auth_method="none" if public else "client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    store_client(info, operator=True)
    return {"client_id": info.client_id, "client_secret": secret}
