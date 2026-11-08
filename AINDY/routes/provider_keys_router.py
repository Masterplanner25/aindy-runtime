"""A user's own provider keys (FR-59, DEC-121): set, replace, delete and list. Never read back.

  GET    /auth/provider-keys            — {provider, key_hint, updated_at} for each key set
  PUT    /auth/provider-keys/{provider} — body {"key": "..."}; set or replace
  DELETE /auth/provider-keys/{provider} — remove

A signed-in session only, so a leaked API key cannot swap whose account pays. The key never
enters the pipeline's records: ``input_payload`` holds the provider name only, and an error
message never repeats it. Nothing uses a stored key until ``AINDY_PROVIDER_KEYS=per_user``.
"""
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from AINDY.core.execution_helper import execute_with_pipeline_sync
from AINDY.core.execution_signal_helper import queue_system_event
from AINDY.core.system_event_types import SystemEventTypes
from AINDY.db.database import get_db
from AINDY.platform_layer.rate_limiter import limiter
from AINDY.services.auth_service import get_current_user, is_api_key_principal

router = APIRouter(prefix="/auth/provider-keys", tags=["Provider keys"])


def _require_session(current_user: dict = Depends(get_current_user)) -> dict:
    if is_api_key_principal(current_user):
        raise HTTPException(status_code=403, detail="Provider keys are managed from a signed-in session.")
    return current_user


def _run(request: Request, route_name: str, handler, *, db: Session, user_id: str, provider: Optional[str] = None,
         success_status_code: int = 200):
    result = execute_with_pipeline_sync(
        request=request,
        route_name=route_name,
        handler=handler,
        user_id=user_id,
        input_payload={"provider": provider} if provider else {},  # never the key
        metadata={"db": db, "source": "auth.provider_keys"},
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


def _audit(db, event_type: str, user_id: str, provider: str, key_hint) -> None:
    """The audit record: provider and hint, never the key."""
    payload = {"provider": provider}
    if key_hint:
        payload["key_hint"] = key_hint
    queue_system_event(db=db, event_type=event_type, user_id=user_id, payload=payload, required=False)


def _bad_request(exc: Exception) -> HTTPException:
    # The messages come from provider_keys and name the provider or the rule, never the key.
    return HTTPException(status_code=422, detail={"error": "invalid_provider_key", "message": str(exc)})


@router.get("", response_model=None)
@limiter.limit("60/minute")
def list_provider_keys(request: Request, db: Session = Depends(get_db), current_user: dict = Depends(_require_session)):
    user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.platform_layer.provider_keys import list_user_provider_keys

        return {"keys": list_user_provider_keys(db, user_id)}

    return _run(request, "auth.provider_keys.list", handler, db=db, user_id=user_id)


@router.put("/{provider}", response_model=None)
@limiter.limit("10/minute")
def set_provider_key(
    request: Request,
    provider: str,
    body: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    current_user: dict = Depends(_require_session),
):
    user_id = str(current_user["sub"])
    raw_key = body.get("key") if isinstance(body, dict) else None

    def handler(ctx):
        from AINDY.platform_layer.provider_keys import ProviderKeyConfigError, set_user_provider_key

        if not isinstance(raw_key, str):
            raise HTTPException(status_code=422, detail={"error": "invalid_provider_key", "message": "Send {\"key\": \"...\"}."})
        try:
            stored = set_user_provider_key(db, user_id, provider, raw_key)
            _audit(db, SystemEventTypes.PROVIDER_KEY_SET, user_id, stored["provider"], stored["key_hint"])
            return stored
        except ValueError as exc:
            raise _bad_request(exc) from None
        except ProviderKeyConfigError:
            raise HTTPException(
                status_code=503,
                detail={"error": "provider_keys_unavailable", "message": "Provider keys are not configured on this server."},
            ) from None

    return _run(request, "auth.provider_keys.set", handler, db=db, user_id=user_id, provider=provider)


@router.delete("/{provider}", status_code=204, response_model=None)
@limiter.limit("10/minute")
def delete_provider_key(
    request: Request, provider: str, db: Session = Depends(get_db), current_user: dict = Depends(_require_session)
):
    user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.platform_layer.provider_keys import delete_user_provider_key

        try:
            if not delete_user_provider_key(db, user_id, provider):
                raise HTTPException(status_code=404, detail=f"No {provider} key is set.")
            _audit(db, SystemEventTypes.PROVIDER_KEY_DELETED, user_id, provider.strip().lower(), None)
        except ValueError as exc:
            raise _bad_request(exc) from None
        return None

    return _run(request, "auth.provider_keys.delete", handler, db=db, user_id=user_id, provider=provider,
                success_status_code=204)
