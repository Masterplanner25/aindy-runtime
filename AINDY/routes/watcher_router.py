"""
watcher_router.py — A.I.N.D.Y. Watcher signal receiver.

Endpoints:
  POST /watcher/signals   — receive batched signals from Watcher process
  GET  /watcher/signals   — query stored signals (paginated)
  POST /watcher/keys      — mint your own watcher key
  GET  /watcher/keys      — list your watcher keys
  DELETE /watcher/keys/{id} — revoke one

Auth on /signals, either of:
  X-Platform-Key: a user's own key. The signals are that user's, and naming anyone else is a 403.
  X-API-Key: the deployment's shared secret, for a service. It may name any user, so it must
  never be installed on a user's machine (DEC-117).
Auth on /keys: a signed-in session, so one key can't mint another.
"""

from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from AINDY.core.execution_gate import to_envelope
from AINDY.auth.api_key_auth import Scopes, is_watcher_only
from AINDY.core.execution_helper import execute_with_pipeline_sync
from AINDY.db.database import get_db
from AINDY.platform_layer.rate_limiter import limiter
from AINDY.routes.path_params import UUIDPath
from AINDY.services.auth_service import (
    _platform_key_header,
    _resolve_platform_key_as_user,
    api_key_header,
    get_current_user,
    is_api_key_principal,
    verify_api_key,
)
from AINDY.watcher.constants import (
    _VALID_ACTIVITY_TYPES,
    _VALID_SIGNAL_TYPES,
    _parse_timestamp,
)

router = APIRouter(
    prefix="/watcher",
    tags=["Watcher"],
)


def _watcher_caller(
    api_key: str | None = Security(api_key_header),
    platform_key: str | None = Security(_platform_key_header),
    db: Session = Depends(get_db),
) -> Optional[str]:
    """The user a platform key belongs to, or None for the shared service key."""
    if platform_key:
        principal = _resolve_platform_key_as_user(platform_key, db, allow_watcher_only=True)
        scopes = set(principal.get("api_key_scopes") or [])
        if not scopes.intersection({Scopes.WATCHER_SIGNALS, Scopes.PLATFORM_ADMIN}):
            raise HTTPException(
                status_code=403,
                detail=f"API key scope '{Scopes.WATCHER_SIGNALS}' required.",
            )
        return str(principal["sub"])
    verify_api_key(api_key)
    return None


WatcherCaller = Annotated[Optional[str], Depends(_watcher_caller)]


def _require_session(current_user: dict = Depends(get_current_user)) -> dict:
    if is_api_key_principal(current_user):
        raise HTTPException(status_code=403, detail="Watcher keys are managed from a signed-in session.")
    return current_user


def _same_user(named: Optional[str], caller: str) -> bool:
    try:
        return UUID(str(named)) == UUID(caller)
    except ValueError:
        return False


def _refuse_another_user() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"error": "TENANT_VIOLATION", "message": "A watcher key only acts for its owner."},
    )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SignalPayload(BaseModel):
    signal_type: str = Field(..., description="session_started | session_ended | distraction_detected | focus_achieved | context_switch | heartbeat")
    session_id: str = Field(..., description="UUID string grouping signals within one session")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp from watcher")
    app_name: str = Field(..., description="Active application name")
    window_title: str = Field(default="", description="Active window title")
    activity_type: str = Field(..., description="work | communication | distraction | idle | unknown")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    user_id: Optional[str] = Field(default=None, description="User ID to associate with this signal batch")


class SignalBatch(BaseModel):
    signals: List[SignalPayload] = Field(..., min_length=1, max_length=100)


class SignalResponse(BaseModel):
    id: int
    signal_type: str
    session_id: str
    app_name: str
    window_title: Optional[str]
    activity_type: str
    signal_timestamp: str
    received_at: str
    duration_seconds: Optional[float]
    focus_score: Optional[float]
    metadata: Optional[Dict[str, Any]]


class WatcherKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class WatcherIngestResponse(BaseModel):
    accepted: int
    session_ended_count: int
    orchestration: Optional[Dict[str, Any]] = None


def _validate_signal(sig: SignalPayload, idx: int) -> None:
    if sig.signal_type not in _VALID_SIGNAL_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Signal [{idx}]: unknown signal_type {sig.signal_type!r}",
        )
    if sig.activity_type not in _VALID_ACTIVITY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Signal [{idx}]: unknown activity_type {sig.activity_type!r}",
        )
    try:
        _parse_timestamp(sig.timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if sig.user_id:
        try:
            UUID(str(sig.user_id))
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Signal [{idx}]: invalid user_id {sig.user_id!r}",
            ) from exc


def _run_flow_watcher(flow_name: str, payload: dict, db: Session, user_id: str | None):
    from AINDY.runtime.flow_engine import run_flow
    result = run_flow(flow_name, payload, db=db, user_id=user_id)
    if result.get("status") == "FAILED":
        error = result.get("error", "")
        if error.startswith("HTTP_"):
            parts = error.split(":", 1)
            code = int(parts[0].replace("HTTP_", ""))
            msg = parts[1] if len(parts) > 1 else error
            raise HTTPException(status_code=code, detail=msg)
        raise HTTPException(status_code=500, detail=error or f"{flow_name} failed")
    data = result.get("data") or {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("execution_envelope", to_envelope(
        eu_id=result.get("run_id"), trace_id=result.get("trace_id"),
        status=str(result.get("status") or "UNKNOWN").upper(),
        output=None, error=result.get("error"), duration_ms=None, attempt_count=None,
    ))
    return data


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/signals", status_code=201)
@limiter.limit("30/minute")
def receive_signals(
    request: Request,
    batch: SignalBatch,
    caller: WatcherCaller,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Receive a batch of watcher signals. Validates, persists, and returns counts.
    Invalid signals are rejected wholesale (entire batch) if any signal has
    an unknown signal_type — partial persistence is not supported.
    """
    user_id = caller or next((sig.user_id for sig in batch.signals if sig.user_id), None)

    def handler(ctx):
        if caller:
            for sig in batch.signals:
                if sig.user_id and not _same_user(sig.user_id, caller):
                    raise _refuse_another_user()
                sig.user_id = caller
        for idx, sig in enumerate(batch.signals):
            _validate_signal(sig, idx)
        return _run_flow_watcher(
            "watcher_signals_receive",
            {"signals": [sig.model_dump() for sig in batch.signals]},
            db,
            user_id,
        )

    return execute_with_pipeline_sync(
        request=request,
        route_name="watcher.signals.receive",
        handler=handler,
        user_id=user_id,
        input_payload=batch.model_dump(),
        metadata={"db": db},
        success_status_code=201,
    )


@router.get("/signals")
@limiter.limit("60/minute")
def list_signals(
    request: Request,
    caller: WatcherCaller,
    session_id: Optional[str] = Query(default=None, description="Filter by session UUID"),
    signal_type: Optional[str] = Query(default=None, description="Filter by signal type"),
    user_id: Optional[str] = Query(default=None, description="Filter by user UUID"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Query stored watcher signals."""
    effective_user = caller or user_id

    def handler(ctx):
        from AINDY.platform_layer.watcher_service import list_signals as svc_list_signals
        if caller and user_id and not _same_user(user_id, caller):
            raise _refuse_another_user()
        payload = svc_list_signals(
            db,
            session_id=session_id,
            signal_type=signal_type,
            user_id=effective_user,
            limit=limit,
            offset=offset,
        )
        rows = payload.get("signals") or []
        return {
            "signals": rows,
            "count": len(rows),
            "execution_envelope": to_envelope(
                eu_id=None, trace_id=None, status="SUCCESS",
                output=None, error=None, duration_ms=None, attempt_count=1,
            ),
        }

    return execute_with_pipeline_sync(
        request=request,
        route_name="watcher.signals.list",
        handler=handler,
        user_id=effective_user,
        metadata={"db": db},
        input_payload={
            "session_id": session_id,
            "signal_type": signal_type,
            "user_id": user_id,
            "limit": limit,
            "offset": offset,
        },
    )


# ---------------------------------------------------------------------------
# A user's own keys
# ---------------------------------------------------------------------------

def _execute_keys(request: Request, route_name: str, handler, *, db: Session, user_id: str, success_status_code: int = 200):
    result = execute_with_pipeline_sync(
        request=request,
        route_name=route_name,
        handler=handler,
        user_id=user_id,
        input_payload={},  # never the key itself
        metadata={"db": db, "source": "watcher.keys"},
        success_status_code=success_status_code,
        return_result=True,
    )
    if not result.success:
        detail = result.metadata.get("detail") or result.error or "Execution failed"
        raise HTTPException(status_code=int(result.metadata.get("status_code", 500)), detail=detail)
    data = result.data
    if isinstance(data, dict):
        data = dict(data)
        data.pop("execution_envelope", None)
    return data


@router.post("/keys", status_code=201, response_model=None)
@limiter.limit("10/minute")
def create_watcher_key(
    request: Request,
    body: WatcherKeyCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_require_session),
):
    """Mint a key for your own watcher. The key is shown once, and its scope is fixed."""
    user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.platform_layer.api_key_service import create_api_key

        try:
            record, raw_key = create_api_key(
                user_id=user_id, name=body.name, scopes=[Scopes.WATCHER_SIGNALS], db=db
            )
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail=f"You already have a key named {body.name!r}.")
        return {
            "key": raw_key,
            "id": str(record.id),
            "name": record.name,
            "key_prefix": record.key_prefix,
            "scopes": list(record.scopes or []),
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }

    return _execute_keys(request, "watcher.keys.create", handler, db=db, user_id=user_id, success_status_code=201)


@router.get("/keys", response_model=None)
@limiter.limit("60/minute")
def list_watcher_keys(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_require_session),
):
    user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.platform_layer.api_key_service import list_api_keys

        keys = list_api_keys(user_id=user_id, db=db)
        return {"keys": [key for key in keys if is_watcher_only(key.get("scopes"))]}

    return _execute_keys(request, "watcher.keys.list", handler, db=db, user_id=user_id)


@router.delete("/keys/{key_id}", status_code=204, response_model=None)
@limiter.limit("30/minute")
def revoke_watcher_key(
    request: Request,
    key_id: UUIDPath,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_require_session),
):
    user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.platform_layer.api_key_service import get_api_key, revoke_api_key

        meta = get_api_key(key_id=key_id, user_id=user_id, db=db)
        if not meta or not is_watcher_only(meta.get("scopes")):
            raise HTTPException(status_code=404, detail=f"Watcher key {key_id!r} not found")
        revoke_api_key(key_id=key_id, user_id=user_id, db=db)
        return None

    return _execute_keys(request, "watcher.keys.revoke", handler, db=db, user_id=user_id, success_status_code=204)
