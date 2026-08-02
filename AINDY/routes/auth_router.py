"""
auth_router.py — Authentication endpoints for A.I.N.D.Y.

Public endpoints (no auth required):
  POST /auth/login    — exchange credentials for JWT token
  POST /auth/register — create a new user account

Authenticated endpoints:
  POST /auth/logout          — invalidate the caller's sessions
  POST /auth/password/change — rotate the caller's own password (FR-6 item 1)

Phase 3: Uses PostgreSQL User model via DB session (replaced in-memory store).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from AINDY.core.execution_signal_helper import queue_system_event
from sqlalchemy.orm import Session
from AINDY.core.execution_helper import execute_with_pipeline_sync
from AINDY.db.database import get_db
from AINDY.platform_layer.rate_limiter import limiter
from AINDY.platform_layer.user_ids import parse_user_id
from AINDY.schemas.auth_schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from AINDY.services.auth_service import (
    MIN_PASSWORD_LENGTH,
    authenticate_user,
    bump_token_version,
    change_user_password,
    create_access_token,
    create_password_reset_token,
    get_current_user,
    register_user,
    require_admin_principal,
    reset_password_with_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])
emit_system_event = queue_system_event
initialize_signup_state = None


@router.post("/register", status_code=202)
@limiter.limit("10/minute")
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
    request: Request = None,
):
    """Begin registration (FR-6 Phase C). Public endpoint.

    **Returns 202 with no token, identically for a new and an already-registered address.**
    That uniformity is the point: it closes the account-enumeration oracle the previous
    409-on-duplicate created. It could not be closed while registration also authenticated
    the caller, because a duplicate cannot be handed a token — so the token moved behind
    address verification.

    A new address gets a verification link; an already-registered one gets a "someone tried
    to register with your address" notice instead. The caller cannot tell which was sent.
    """
    def handler(ctx):
        from AINDY.db.models.user import User
        from AINDY.services.auth_service import hash_password

        email = (body.email or "").strip()

        # Validate the password BEFORE the existence check. A 400 here describes the
        # submitted password, not the account, so it is not an enumeration signal — and
        # doing it first means a rejected request never reveals whether the address exists.
        if len(body.password or "") < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
            )

        existing = db.query(User).filter(User.email == email).first()
        if existing:
            # Create nothing, and disclose nothing. Notify the real owner instead — which
            # is also how they learn someone is probing their address.
            _send_duplicate_registration_notice(existing, db)
            # Equalise against the create path's bcrypt cost so timing does not disclose
            # what the response deliberately hides.
            hash_password(body.password or "")
        else:
            try:
                user = register_user(
                    email=email,
                    password=body.password,
                    username=body.username,
                    db=db,
                )
            except HTTPException as exc:
                # A concurrent registration for the same address won the race between our
                # existence check and this insert. register_user still raises 409 as a
                # last-line guard, but letting that reach the caller would leak exactly
                # what the uniform 202 exists to hide — under a timing window an attacker
                # can provoke. Fall back to the duplicate branch instead.
                if exc.status_code != 409:
                    raise
                _send_duplicate_registration_notice(
                    db.query(User).filter(User.email == email).first(), db
                )
                return {"status": "verification_sent"}
            emit_system_event(
                db=db,
                event_type="auth.register.completed",
                user_id=user.id,
                payload={"email": user.email, "username": user.username},
                required=True,
            )
            _send_verification_email(user, db)

        return {"status": "verification_sent"}

    if request is None:
        return handler(None)

    return execute_with_pipeline_sync(
        request=request,
        route_name="auth.register",
        handler=handler,
        metadata={"db": db},
        # NEVER body.model_dump() — RegisterRequest carries the plaintext password and
        # input_payload is persisted on the ExecutionUnit. This route (and login) had been
        # writing raw passwords into the execution record; the same surface /password/change
        # was deliberately kept clear of.
        input_payload={"email": body.email, "username": body.username},
        success_status_code=202,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
    request: Request = None,
):
    """
    Authenticate user and return JWT token. Public endpoint.
    """
    def handler(ctx):
        user = authenticate_user(email=body.email, password=body.password, db=db)
        emit_system_event(
            db=db,
            event_type="auth.login.completed",
            user_id=user.id,
            payload={
                "email": user.email,
            },
            required=True,
        )
        token = create_access_token(
            {"sub": str(user.id), "email": user.email, "is_admin": bool(getattr(user, "is_admin", False))},
            token_version=int(getattr(user, "token_version", 0)),
        )
        return {"access_token": token, "token_type": "bearer"}

    if request is None:
        return handler(None)

    return execute_with_pipeline_sync(
        request=request,
        route_name="auth.login",
        handler=handler,
        metadata={"db": db},
        # Email only — LoginRequest carries the plaintext password (see the register note).
        input_payload={"email": body.email},
    )


@router.post("/logout", status_code=200)
@limiter.limit("10/minute")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    def handler(ctx):
        from AINDY.db.models.user import User

        if current_user.get("auth_type") == "api_key":
            raise HTTPException(status_code=401, detail="Bearer token required")

        user_id = parse_user_id(current_user["sub"])
        if user_id:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                bump_token_version(user)
                db.commit()
        return {"status": "logged_out"}

    return execute_with_pipeline_sync(
        request=request,
        route_name="auth.logout",
        handler=handler,
        user_id=str(current_user["sub"]),
        metadata={"db": db},
    )


@router.post("/password/change", status_code=200)
@limiter.limit("5/minute")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Rotate the caller's own password (FR-6 item 1).

    Bearer-JWT only — a platform API key has no password to rotate. On success every
    other session is invalidated (``token_version`` bump) and a freshly-versioned token
    is returned in the same shape as ``/auth/login``, so the caller stays signed in
    while other sessions are cut.

    Neither password is ever put in ``input_payload`` or the emitted event: both are
    trace-logged surfaces.
    """
    def handler(ctx):
        if current_user.get("auth_type") == "api_key":
            raise HTTPException(status_code=401, detail="Bearer token required")

        user_id = parse_user_id(current_user["sub"])
        if not user_id:
            raise HTTPException(status_code=400, detail="Invalid user_id")

        user = change_user_password(
            user_id=user_id,
            current_password=body.current_password,
            new_password=body.new_password,
            db=db,
        )
        emit_system_event(
            db=db,
            event_type="auth.password.changed",
            user_id=user.id,
            payload={"email": user.email},
            required=True,
        )
        token = create_access_token(
            {"sub": str(user.id), "email": user.email, "is_admin": bool(getattr(user, "is_admin", False))},
            token_version=int(getattr(user, "token_version", 0)),
        )
        # Same shape as /auth/login (inside the canonical envelope, which ui-kit
        # unwraps) so a client can reuse its existing token-store path verbatim.
        return {"access_token": token, "token_type": "bearer"}

    return execute_with_pipeline_sync(
        request=request,
        route_name="auth.password.change",
        handler=handler,
        user_id=str(current_user["sub"]),
        metadata={"db": db},
    )


def _send_verification_email(user, db) -> None:
    from AINDY.config import settings
    from AINDY.platform_layer.email_channel import send_email
    from AINDY.services.auth_service import create_email_verification_token

    token = create_email_verification_token(user)
    template = getattr(settings, "AINDY_EMAIL_VERIFY_URL_TEMPLATE", "") or ""
    link = template.replace("{token}", token) if template else token
    send_email(
        to=user.email,
        subject="Confirm your email address",
        body="\n".join(
            [
                "Welcome. Confirm your email address to finish setting up your account:",
                "",
                link,
                "",
                "If you did not create this account, you can ignore this message.",
            ]
        ),
        db=db,
        user_id=str(user.id),
    )


def _send_duplicate_registration_notice(user, db) -> None:
    """Tell the real owner that someone tried to register with their address.

    This is what makes the uniform 202 honest rather than merely silent: the person with a
    legitimate interest is informed, while the caller learns nothing. It also gives the
    owner a signal that their address is being probed.
    """
    from AINDY.platform_layer.email_channel import send_email

    send_email(
        to=user.email,
        subject="Someone tried to register with your email address",
        body="\n".join(
            [
                "Someone attempted to create an account using this email address, which "
                "is already registered.",
                "",
                "If this was you, sign in instead — or use the password reset flow if you "
                "have forgotten your password.",
                "",
                "If it was not you, no action is needed. Your account has not changed.",
            ]
        ),
        db=db,
        user_id=str(user.id),
    )


@router.post("/verify-email", status_code=200)
@limiter.limit("10/minute")
def verify_email(
    body: VerifyEmailRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Confirm an address and issue the access token registration no longer returns.

    Idempotent: following an already-used link succeeds rather than erroring, since the
    user-visible outcome is identical and an error would only confuse.
    """
    def handler(ctx):
        from AINDY.services.auth_service import confirm_email_verification

        user = confirm_email_verification(token=body.token, db=db)
        emit_system_event(
            db=db,
            event_type="auth.email.verified",
            user_id=user.id,
            payload={"email": user.email},
            required=True,
        )
        token = create_access_token(
            {
                "sub": str(user.id),
                "email": user.email,
                "is_admin": bool(getattr(user, "is_admin", False)),
            },
            token_version=int(getattr(user, "token_version", 0)),
        )
        return {"access_token": token, "token_type": "bearer"}

    return execute_with_pipeline_sync(
        request=request,
        route_name="auth.email.verify",
        handler=handler,
        metadata={"db": db},
    )


def _forgot_rate_limited(email: str, request: Request) -> bool:
    """Per-IP AND per-email fixed-window limit (3/min each).

    `/forgot` is unauthenticated, so it is the cheapest endpoint to abuse for
    mail-bombing. Limiting per IP alone lets a distributed caller pound one inbox;
    limiting per email alone lets one host sweep many addresses. Both are needed.

    Rides `ResourceManager.rate_limit_hit` (Redis fixed-window, in-memory fallback) so the
    limit holds across instances. It fails open on a backend hiccup, which is the right
    trade here: a counter outage must not lock legitimate users out of recovery.
    """
    try:
        from AINDY.kernel.resource_manager import get_resource_manager

        rm = get_resource_manager()
        client = getattr(getattr(request, "client", None), "host", "") or "unknown"
        _, ip_over = rm.rate_limit_hit(f"auth:forgot:ip:{client}", limit=3, window_secs=60)
        _, email_over = rm.rate_limit_hit(
            f"auth:forgot:email:{email.strip().lower()}", limit=3, window_secs=60
        )
        return bool(ip_over or email_over)
    except Exception:
        return False


def _send_reset_email(user, db) -> None:
    from AINDY.config import settings
    from AINDY.platform_layer.email_channel import send_email

    token = create_password_reset_token(user)
    template = getattr(settings, "AINDY_PASSWORD_RESET_URL_TEMPLATE", "") or ""
    link = template.replace("{token}", token) if template else token
    send_email(
        to=user.email,
        subject="Reset your password",
        body="\n".join(
            [
                "A password reset was requested for your account.",
                "",
                link,
                "",
                "If you did not request this, you can ignore this message — your "
                "password has not changed.",
            ]
        ),
        db=db,
        user_id=str(user.id),
    )


@router.post("/password/forgot", status_code=200)
@limiter.limit("10/minute")
def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Begin password recovery (FR-6 item 2).

    **Always 200 for a well-formed request**, whether or not the email is registered.
    Anything else is an account-enumeration oracle — the same rule the runtime applies to
    itself elsewhere.

    **503 when no email channel is configured.** That discloses a property of the
    *deployment*, identical for every caller, and reveals nothing about any account — so
    the uniform-response rule does not apply. Failing closed and loudly beats accepting a
    request the runtime cannot fulfil.
    """
    def handler(ctx):
        from AINDY.db.models.user import User
        from AINDY.platform_layer.email_channel import email_channel_status
        from AINDY.services.auth_service import hash_password

        status = email_channel_status()
        if not status["available"]:
            raise HTTPException(
                status_code=503,
                detail="Password reset is unavailable: no email channel is configured",
            )

        email = (body.email or "").strip()
        if _forgot_rate_limited(email, request):
            raise HTTPException(status_code=429, detail="Too many requests")

        user = db.query(User).filter(User.email == email).first()
        if user and user.is_active:
            _send_reset_email(user, db)
        else:
            # Timing equalisation. Returning immediately here would leak the answer as
            # loudly as a status code would: the hit path mints a token and makes a
            # network call, so the miss path must not be visibly cheaper. A bcrypt hash
            # is the closest same-order work already available.
            hash_password("no-such-account-equalisation")

        return {"status": "ok"}

    return execute_with_pipeline_sync(
        request=request,
        route_name="auth.password.forgot",
        handler=handler,
        metadata={"db": db},
    )


@router.post("/password/reset", status_code=200)
@limiter.limit("10/minute")
def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Complete password recovery (FR-6 item 3).

    Consuming the token bumps ``token_version``, which invalidates every session **and**
    burns the token — single-use falls out of the design rather than being bookkept.
    Unlike `/password/change`, no new token is returned: the caller is not proven to be
    the session holder, so they log in afresh.
    """
    def handler(ctx):
        user = reset_password_with_token(
            token=body.token, new_password=body.new_password, db=db
        )
        emit_system_event(
            db=db,
            event_type="auth.password.reset",
            user_id=user.id,
            payload={"email": user.email},
            required=True,
        )
        return {"status": "password_reset"}

    return execute_with_pipeline_sync(
        request=request,
        route_name="auth.password.reset",
        handler=handler,
        metadata={"db": db},
    )


@router.post("/admin/invalidate-sessions/{user_id}")
@limiter.limit("20/minute")
def admin_invalidate_sessions(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_principal),
):
    def handler(ctx):
        from AINDY.db.models.user import User

        target_id = parse_user_id(user_id)
        if not target_id:
            raise HTTPException(status_code=400, detail="Invalid user_id")

        user = db.query(User).filter(User.id == target_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        bump_token_version(user)
        db.commit()
        return {"status": "sessions_invalidated", "user_id": str(target_id)}

    return execute_with_pipeline_sync(
        request=request,
        route_name="auth.admin.invalidate_sessions",
        handler=handler,
        user_id=str(current_user["sub"]),
        metadata={"db": db},
        input_payload={"user_id": user_id},
    )

