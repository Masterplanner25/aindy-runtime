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
)
from AINDY.services.auth_service import (
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


@router.post("/register", response_model=TokenResponse, status_code=201)
@limiter.limit("10/minute")
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
    request: Request = None,
):
    """
    Register a new user. Public endpoint — no auth required.
    Returns a JWT access token on success.
    """
    def handler(ctx):
        user = register_user(
            email=body.email,
            password=body.password,
            username=body.username,
            db=db,
        )
        emit_system_event(
            db=db,
            event_type="auth.register.completed",
            user_id=user.id,
            payload={
                "email": user.email,
                "username": user.username,
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
        route_name="auth.register",
        handler=handler,
        metadata={"db": db},
        input_payload=body.model_dump(),
        success_status_code=201,
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
        input_payload=body.model_dump(),
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

