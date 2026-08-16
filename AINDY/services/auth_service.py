"""
A.I.N.D.Y. Authentication Service

Provides:
- JWT token creation and verification (user auth)
- API key validation (service-to-service auth)
- Password hashing utilities
"""
import hashlib
import hmac
import os
import signal
import threading
from datetime import datetime, timedelta, timezone
import re
from typing import Optional, TYPE_CHECKING

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, Security, Depends
from fastapi.security import (
    HTTPBearer,
    HTTPAuthorizationCredentials,
    APIKeyHeader,
)
from sqlalchemy.orm import Session

from AINDY.config import settings
from AINDY.db.database import get_db
from AINDY.platform_layer.user_ids import parse_user_id


class KeyRing:
    """
    Two-slot JWT key ring supporting live rotation.

    Signing always uses the active key.
    Verification tries active first, then previous (grace period).
    """

    def __init__(
        self,
        active: str,
        previous: Optional[str] = None,
        grace_hours: int = 24,
    ) -> None:
        self._lock = threading.RLock()
        self._active = active
        self._previous = previous
        self._previous_expires: Optional[datetime] = None
        self._grace_hours = grace_hours
        if previous:
            self._previous_expires = datetime.now(timezone.utc) + timedelta(hours=grace_hours)

    @property
    def active_key(self) -> str:
        with self._lock:
            return self._active

    def rotate(self, new_key: str) -> None:
        """Promote active → previous (with expiry), set new active key."""
        with self._lock:
            if new_key == self._active:
                return
            self._previous = self._active
            self._previous_expires = datetime.now(timezone.utc) + timedelta(
                hours=self._grace_hours
            )
            self._active = new_key

    def verify_keys(self) -> list[str]:
        """Return keys to try for verification, most recent first."""
        with self._lock:
            keys = [self._active]
            if self._previous and self._previous_expires:
                if datetime.now(timezone.utc) < self._previous_expires:
                    keys.append(self._previous)
                else:
                    self._previous = None
                    self._previous_expires = None
            return keys

    def reload_from_env(self) -> bool:
        """Reload active key from SECRET_KEY env var. Returns True if key changed."""
        new_key = os.getenv("SECRET_KEY", "")
        if not new_key or new_key == self._active:
            return False
        self.rotate(new_key)
        return True


# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT config
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
_key_ring = KeyRing(active=settings.SECRET_KEY)

# API key config
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)
_platform_key_header = APIKeyHeader(name="X-Platform-Key", auto_error=False)


def _get_signing_key() -> str:
    return _key_ring.active_key


def signing_key() -> str:
    """Active signing secret (mint side). Rotated via ``rotate_signing_key`` / SIGHUP.

    Shared by JWT signing and the agent capability-token HMAC (AGENT-HARDEN-2) so
    both ride the same rotation machinery.
    """
    return _key_ring.active_key


def verification_keys() -> list[str]:
    """Secrets to try when verifying a MAC/JWT: active first, then previous within
    the rotation grace window. Mirrors the JWT verify path.
    """
    return _key_ring.verify_keys()


def rotate_signing_key(new_key: str) -> bool:
    if new_key == _key_ring.active_key:
        return False
    _key_ring.rotate(new_key)
    return True


# ── Password utilities ──────────────────────────────────────────────────────

# The floor for every path that sets a password: `register_user` and
# `change_user_password`. Deliberately NOT configurable — a security floor an operator
# can switch off is not a floor. Raising it is a code change.
#
# Applied to registration 2026-08-01 (previously change-only). This rejects *new*
# registrations under the length; it does not invalidate any stored password, and login
# is unaffected. The blast radius is a downstream registration form that permitted
# shorter passwords, which now gets a 400.
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── JWT utilities ───────────────────────────────────────────────────────────

# Every token this service mints declares what it is for, and every token it accepts
# is checked against that. Before this existed, `decode_access_token` accepted ANY
# HS256 token verifying against a KeyRing secret and examined nothing else — so any
# other token type signed with the same key (a password-reset token carrying `sub`
# and `tv`, say) was silently a valid bearer access token for that user.
#
# FR-6 keeps the primary control at a lower level — non-access tokens are signed with
# a domain-separated derived key, so they cannot verify here at all. This claim is
# defence in depth: it makes the "wrong token type" failure explicit rather than
# relying on every future token type remembering to derive its own key.
ACCESS_TOKEN_PURPOSE = "access"


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    token_version: int = 0,
) -> str:
    to_encode = data.copy()
    to_encode["tv"] = token_version
    to_encode["purpose"] = ACCESS_TOKEN_PURPOSE
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, _get_signing_key(), algorithm=ALGORITHM)


# ── Email-verification tokens (FR-6 Phase C) ─────────────────────────

#: Separate domain from the reset token, not just a different purpose claim. Reusing the
#: reset domain would make a verification link redeemable as a password reset and vice
#: versa — two flows with different authority, one credential.
EMAIL_VERIFY_DOMAIN = b"aindy-email-verify-v1"
EMAIL_VERIFY_PURPOSE = "email_verify"


def _verify_signing_key() -> str:
    return _derive_domain_key(signing_key(), EMAIL_VERIFY_DOMAIN)


def _verify_verification_keys() -> list[str]:
    return [_derive_domain_key(k, EMAIL_VERIFY_DOMAIN) for k in verification_keys()]


def create_email_verification_token(user, *, ttl_hours: int | None = None) -> str:
    """Mint an address-verification token.

    Deliberately does NOT pin ``token_version``: unlike a reset token, this one must
    survive ordinary account activity between registering and clicking the link. Logging in
    elsewhere, or an admin invalidating sessions, should not silently void a verification
    email. Single-use comes from ``is_verified`` instead — a consumed token finds the
    account already verified.
    """
    ttl = int(
        ttl_hours
        if ttl_hours is not None
        else getattr(settings, "AINDY_EMAIL_VERIFY_TTL_HOURS", 48)
    )
    payload = {
        "sub": str(user.id),
        "purpose": EMAIL_VERIFY_PURPOSE,
        "exp": datetime.now(timezone.utc) + timedelta(hours=ttl),
    }
    return jwt.encode(payload, _verify_signing_key(), algorithm=ALGORITHM)


def verify_email_token(token: str) -> dict:
    """Return the claims of a valid verification token, or raise a generic 400."""
    generic = HTTPException(status_code=400, detail="Invalid or expired verification token")
    for key in _verify_verification_keys():
        try:
            payload = jwt.decode(token, key, algorithms=[ALGORITHM])
        except JWTError:
            continue
        if payload.get("purpose") != EMAIL_VERIFY_PURPOSE:
            raise generic
        return payload
    raise generic


def confirm_email_verification(*, token: str, db: Session):
    """Consume a verification token and mark the address confirmed. Returns the user.

    Idempotent on an already-verified account: re-following a link the user already used
    succeeds rather than erroring, because the user-visible outcome is identical and an
    error would only be confusing.
    """
    from AINDY.db.models.user import User

    claims = verify_email_token(token)
    user_uuid = parse_user_id(claims.get("sub"))
    user = db.query(User).filter(User.id == user_uuid).first() if user_uuid else None
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    if not user.is_verified:
        user.is_verified = True
        user.verified_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)
    return user


# ── Password-reset tokens (FR-6 Phase B) ────────────────────────────────────

#: Domain string mixed into the signing key for password-reset tokens.
#:
#: This is the PRIMARY control against token confusion, not the ``purpose`` claim.
#: ``decode_access_token`` verifies against the raw KeyRing secret, so a reset token
#: signed with that secret would be a structurally valid access token — it carries
#: ``sub`` and ``tv``, which is everything ``_resolve_authenticated_jwt_user`` needs.
#: Deriving a separate key means a reset token cannot verify there at all, regardless of
#: what any future code does or forgets to check.
#:
#: Versioned so the derivation can be changed without silently accepting old tokens.
PASSWORD_RESET_DOMAIN = b"aindy-password-reset-v1"
PASSWORD_RESET_PURPOSE = "password_reset"


def _derive_domain_key(secret: str, domain: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), domain, hashlib.sha256).hexdigest()


def _reset_signing_key() -> str:
    return _derive_domain_key(signing_key(), PASSWORD_RESET_DOMAIN)


def _reset_verification_keys() -> list[str]:
    """Derived from every key the access path would accept, so reset tokens survive a
    signing-key rotation exactly as long as access tokens do — no separate grace window
    to reason about."""
    return [_derive_domain_key(k, PASSWORD_RESET_DOMAIN) for k in verification_keys()]


def create_password_reset_token(user, *, ttl_minutes: int | None = None) -> str:
    """Mint a single-use, time-boxed password-reset token.

    Single-use is structural rather than bookkept: the token pins ``tv`` to the user's
    current ``token_version``, and consuming it bumps that version. A replay then fails the
    version comparison. No table, no revocation list, no cleanup job.
    """
    ttl = int(
        ttl_minutes
        if ttl_minutes is not None
        else getattr(settings, "AINDY_PASSWORD_RESET_TTL_MINUTES", 30)
    )
    payload = {
        "sub": str(user.id),
        "tv": int(getattr(user, "token_version", 0)),
        "purpose": PASSWORD_RESET_PURPOSE,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ttl),
    }
    return jwt.encode(payload, _reset_signing_key(), algorithm=ALGORITHM)


def verify_password_reset_token(token: str) -> dict:
    """Return the claims of a valid reset token, or raise 400.

    Every rejection — bad signature, wrong purpose, expired, already used — returns the
    same message. A caller holding a token should not learn *why* it failed, since
    "expired" versus "already used" versus "not a reset token" all disclose account state.
    """
    generic = HTTPException(status_code=400, detail="Invalid or expired reset token")
    for key in _reset_verification_keys():
        try:
            payload = jwt.decode(token, key, algorithms=[ALGORITHM])
        except JWTError:
            continue
        if payload.get("purpose") != PASSWORD_RESET_PURPOSE:
            raise generic
        return payload
    raise generic


def reset_password_with_token(*, token: str, new_password: str, db: Session):
    """Consume a reset token and set the new password. Returns the user.

    Applies the same floor as every other password-setting path, and bumps
    ``token_version`` — which both invalidates every existing session and burns this token.
    """
    from AINDY.db.models.user import User

    claims = verify_password_reset_token(token)

    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )

    user_uuid = parse_user_id(claims.get("sub"))
    user = db.query(User).filter(User.id == user_uuid).first() if user_uuid else None
    if not user:
        # Same generic error: a token naming a deleted user must not be distinguishable
        # from a forged one.
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    if int(claims.get("tv", -1)) != int(getattr(user, "token_version", 0)):
        # The version moved since the token was minted: it has already been used, or the
        # user logged out / changed their password / was force-invalidated meanwhile.
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.hashed_password = hash_password(new_password)
    bump_token_version(user)
    db.commit()
    db.refresh(user)
    return user


def _normalize_username_candidate(value: str | None) -> str:
    raw = (value or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9._-]+", "_", raw).strip("._-")
    return normalized or "user"


def _resolve_username(*, email: str, username: str | None, db: Session) -> str:
    from AINDY.db.models.user import User

    base = _normalize_username_candidate(username or email.split("@", 1)[0])
    candidate = base
    suffix = 1
    while db.query(User).filter(User.username == candidate).first():
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate


def decode_access_token(token: str) -> dict:
    """Decode and validate an **access** token.

    Rejects a structurally valid, correctly-signed token that was minted for some other
    purpose. Without this the only question asked was "does the signature verify?", which
    made every token type sharing the signing key interchangeable with a session.

    Note the deliberate response shape: a wrong-purpose token gets the *same* generic 401
    as a bad signature. Telling a caller "this is a valid password-reset token, just not
    usable here" would confirm both that the token is genuine and which account it belongs
    to.
    """
    last_exc = None
    for key in _key_ring.verify_keys():
        try:
            payload = jwt.decode(token, key, algorithms=[ALGORITHM])
        except JWTError as exc:
            last_exc = exc
            continue
        if payload.get("purpose") != ACCESS_TOKEN_PURPOSE:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload
    raise HTTPException(
        status_code=401,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    ) from last_exc


def _resolve_authenticated_jwt_user(payload: dict, db: Session | None) -> dict:
    from AINDY.db.models.user import User

    user_uuid = parse_user_id(payload.get("sub"))
    if user_uuid is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid authenticated user id",
            headers={"WWW-Authenticate": "Bearer"},
        )
    resolved = dict(payload)
    resolved["sub"] = str(user_uuid)
    resolved["user_id"] = str(user_uuid)
    # HTTP-SCOPE-GAP-1 — seed the ordinary (non-admin) grant here, before the degraded
    # return paths below. Two of them (no usable Session; a DB error under TEST_MODE) return
    # `resolved` without ever reading the user row, and a principal with no `session_scopes`
    # is now denied every scope — so seeding is what keeps those paths working at all.
    # Non-admin is the safe seed: the admin extras are added only once the row confirms them.
    from AINDY.auth.api_key_auth import derive_session_scopes

    resolved["session_scopes"] = derive_session_scopes(is_admin=False)

    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Authentication backend unavailable",
        )
    if not isinstance(db, Session):
        return resolved

    try:
        user = db.query(User).filter(User.id == user_uuid).first()
    except Exception:
        if settings.TEST_MODE:
            return resolved
        raise
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_tv = int(getattr(user, "token_version", 0))
    token_tv = int(payload.get("tv", 0))
    if token_tv != user_tv:
        raise HTTPException(
            status_code=401,
            detail="Token has been invalidated. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    resolved["sub"] = str(user.id)
    resolved["user_id"] = str(user.id)
    resolved["email"] = user.email
    resolved["username"] = user.username
    resolved["is_admin"] = bool(getattr(user, "is_admin", False))
    # Re-derive now that the row is known — this is the authoritative grant. Derived per
    # request rather than read from a token claim, so an admin grant or revocation takes
    # effect on the next call instead of at the next login, and no session has to be
    # invalidated to change authority.
    resolved["session_scopes"] = derive_session_scopes(is_admin=resolved["is_admin"])
    return resolved


# ── FastAPI dependencies ────────────────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    platform_key: str | None = Security(_platform_key_header),
    db: Session = Depends(get_db),
) -> dict:
    """
    FastAPI dependency for JWT-protected routes.
    Usage: current_user: dict = Depends(get_current_user)
    Returns the decoded token payload (user info).

    Also accepts X-Platform-Key header as an alternative to Bearer JWT.
    """
    # Platform API key path — look up key by hash and return user dict
    if platform_key:
        return _resolve_platform_key_as_user(platform_key, db)

    if settings.TEST_MODE:
        if credentials is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return _resolve_authenticated_jwt_user(
            decode_access_token(credentials.credentials),
            db,
        )
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _resolve_authenticated_jwt_user(
        decode_access_token(credentials.credentials),
        db,
    )


def require_platform_admin_access(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Allow any authenticated API key; require is_admin for JWT users.

    Used on the /platform router where API keys are pre-authorized at the
    platform level (scope enforcement happens per-endpoint or per-syscall).
    """
    if current_user.get("auth_type") == "api_key":
        return current_user
    if not current_user.get("is_admin", False):
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required for this endpoint.",
        )
    return current_user


def require_admin_principal(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Require admin for both JWT users (is_admin) and API keys (platform.admin scope).

    Use this on endpoints that are admin-only regardless of auth method, such
    as session invalidation and user management operations.
    """
    if current_user.get("auth_type") == "api_key":
        scopes = set(current_user.get("api_key_scopes") or [])
        if "platform.admin" not in scopes:
            raise HTTPException(
                status_code=403,
                detail="Admin privileges required for this endpoint.",
            )
        return current_user
    if not current_user.get("is_admin", False):
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required for this endpoint.",
        )
    return current_user


def _jwt_scope_enforcement_enabled() -> bool:
    """HTTP-SCOPE-GAP-1 escape hatch. ON by default — this is a hatch, not an opt-in.

    Default-on is deliberate and rests on an enumeration, not on optimism: only **7 of 147**
    route decorators enforce a scope at all, and the only three scopes any of them require
    (`flow.read`, `flow.execute`, `memory.read`) are in the ordinary session set. So every
    signed-in user still passes every currently-enforcing route. The blast radius is countable
    rather than hoped-for, which is why this does not ship default-off the way a genuinely
    unmeasurable tightening would.

    Resolved per call, never cached at import — the standing rule.
    """
    return os.getenv("AINDY_JWT_SCOPE_ENFORCEMENT", "1").strip().lower() not in {"0", "false", "no"}


def enforce_api_key_scope(scope: str):
    """FastAPI dependency factory: enforce a scope for the caller, whoever they are.

    Uses the already-resolved current_user dict so no second DB lookup occurs.

    **HTTP-SCOPE-GAP-1 — JWT sessions are no longer exempt.** This check previously gated
    API-key callers only; its own docstring read *"JWT users carry full trust and are never
    gated by this check"*, which made an interactive browser session **strictly more
    privileged than any API key**. A session now presents `session_scopes`, derived from the
    user row (see `derive_session_scopes`).

    The name is now narrower than the behaviour. It is kept because it appears at 7 call
    sites and in the app team's own notes, and renaming it would churn a security-relevant
    surface for cosmetics; `SCOPE-NAMING-1` tracks the rename if it is ever worth doing.

    Usage:
        @router.get("/platform/flows")
        def list_flows(
            current_user: dict = Depends(get_current_user),
            _: None = Depends(enforce_api_key_scope(Scopes.FLOW_READ)),
        ): ...
    """
    def _check(current_user: dict = Depends(get_current_user)) -> None:
        from AINDY.auth.api_key_auth import Scopes

        if current_user.get("auth_type") == "api_key":
            scopes = set(current_user.get("api_key_scopes") or [])
            principal = "API key"
        elif _jwt_scope_enforcement_enabled():
            scopes = set(current_user.get("session_scopes") or [])
            principal = "Session"
        else:
            return

        if scope not in scopes and Scopes.PLATFORM_ADMIN not in scopes:
            raise HTTPException(
                status_code=403,
                detail=f"{principal} scope '{scope}' required. Granted: {sorted(scopes) or ['(none)']}",
            )
    _check.__name__ = f"enforce_scope_{scope.replace('.', '_')}"
    return _check


def _resolve_platform_key_as_user(raw_key: str, db: Session) -> dict:
    """Validate a platform API key and return a user dict compatible with get_current_user."""
    import hashlib
    import json as _json
    from sqlalchemy import text as _text
    from AINDY.db.models.api_key import PlatformAPIKey

    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    record = db.query(PlatformAPIKey).filter(PlatformAPIKey.key_hash == key_hash).first()

    if record is None or not record.is_valid():
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key",
        )

    # Read scopes via raw SQL to avoid SQLAlchemy ARRAY type result-processor
    # mishandling SQLite's JSON representation of the column.
    # PostgreSQL returns a list (psycopg2 converts ARRAY automatically).
    # SQLite returns a JSON-encoded string.
    row = db.execute(
        _text("SELECT scopes FROM platform_api_keys WHERE key_hash = :kh"),
        {"kh": key_hash},
    ).fetchone()
    raw_scopes_val = row[0] if row else None
    if isinstance(raw_scopes_val, list):
        scopes = raw_scopes_val
    elif isinstance(raw_scopes_val, str):
        try:
            scopes = _json.loads(raw_scopes_val)
        except Exception:
            scopes = []
    else:
        scopes = []

    return {
        "sub": str(record.user_id),
        "user_id": str(record.user_id),
        "auth_type": "api_key",
        "api_key_id": str(record.id),
        "api_key_scopes": list(scopes),
    }


def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
) -> Optional[dict]:
    """
    Optional auth — returns user if token present, None if not.
    Use for endpoints that work with or without auth.
    """
    if credentials is None:
        return None
    try:
        return decode_access_token(credentials.credentials)
    except HTTPException:
        return None


# ── DB-backed user operations ────────────────────────────────────────────────

def register_user(email: str, password: str, username: str | None, db: Session):
    """Create a new user in the database.

    Raises 400 if the password is under ``MIN_PASSWORD_LENGTH``, 409 if the email is
    already registered.

    The length check runs **before** the email lookup: it needs no database round-trip,
    and rejecting on the cheaper check first avoids doing a query for a request that
    cannot succeed either way.
    """
    from AINDY.db.models.user import User

    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    resolved_username = _resolve_username(email=email, username=username, db=db)
    user = User(
        email=email,
        username=resolved_username,
        hashed_password=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(email: str, password: str, db: Session):
    """Verify credentials and return user. Raises 401 on invalid credentials."""
    from AINDY.db.models.user import User
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    if getattr(settings, "AINDY_REQUIRE_VERIFIED_LOGIN", False) and not getattr(
        user, "is_verified", True
    ):
        # Opt-in (FR-6 Phase C). Checked AFTER the password so it cannot be used to
        # discover which addresses exist or are verified without valid credentials.
        raise HTTPException(
            status_code=403,
            detail="Email address not verified. Check your inbox for the verification link.",
        )
    return user


def bump_token_version(user) -> int:
    """Invalidate every outstanding JWT for ``user`` by advancing its token version.

    Wraps at 32767 because ``User.token_version`` is a SMALLINT. Callers commit.
    """
    user.token_version = (int(getattr(user, "token_version", 0)) + 1) % 32767
    return user.token_version


def change_user_password(
    *,
    user_id,
    current_password: str,
    new_password: str,
    db: Session,
):
    """Rotate an authenticated user's own password (FR-6 item 1).

    Verifies the current password, applies the minimum-length policy, writes the new
    hash, and bumps ``token_version`` so every existing session — including the one
    that made this call — is invalidated. Returns the user.

    Raises 404 (no such user), 403 (disabled), 401 (wrong current password),
    400 (too short, or unchanged).
    """
    from AINDY.db.models.user import User

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"New password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    if verify_password(new_password, user.hashed_password):
        raise HTTPException(
            status_code=400,
            detail="New password must differ from the current password",
        )

    user.hashed_password = hash_password(new_password)
    bump_token_version(user)
    db.commit()
    db.refresh(user)
    return user


def verify_api_key(
    api_key: str = Security(api_key_header),
) -> str:
    """
    FastAPI dependency for API-key-protected routes.
    Usage: key: str = Depends(verify_api_key)
    Used for service-to-service calls (bridge, internal).
    """
    valid_keys = set(
        filter(
            None,
            [
                settings.AINDY_API_KEY,
                getattr(settings, "AINDY_SERVICE_KEY", None),
                "test-api-key-for-pytest-only" if settings.TEST_MODE else None,
            ],
        )
    )
    if settings.TEST_MODE:
        if api_key not in valid_keys:
            raise HTTPException(
                status_code=401,
                detail="Invalid API key",
            )
        return api_key
    if not valid_keys:
        raise HTTPException(
            status_code=503,
            detail="API key authentication not configured — set AINDY_API_KEY in .env",
        )
    if api_key not in valid_keys:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
        )
    return api_key


def _reload_key_on_sighup(signum, frame) -> None:
    import logging as _log

    logger = _log.getLogger(__name__)
    changed = _key_ring.reload_from_env()
    if changed:
        logger.warning(
            "[auth_service] SECRET_KEY rotated via SIGHUP. "
            "Previous key retained for %d-hour grace period.",
            _key_ring._grace_hours,
        )
    else:
        logger.info("[auth_service] SIGHUP received; SECRET_KEY unchanged.")


if hasattr(signal, "SIGHUP"):
    signal.signal(signal.SIGHUP, _reload_key_on_sighup)
