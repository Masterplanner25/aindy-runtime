"""A user's connected MCP apps (FR-66 phase 2, DEC-129): see them, and end any one.

  GET    /auth/mcp-grants             — the caller's live grants: client, redirect host, scopes, dates
  DELETE /auth/mcp-grants/{grant_id}  — end one; every token issued under it stops working

A signed-in session only, like the provider-key routes: a leaked API key must not be able to see or
end someone's connections. A grant that isn't the caller's answers 404, as one that doesn't exist.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from AINDY.core.execution_helper import execute_with_pipeline_sync
from AINDY.db.database import get_db
from AINDY.platform_layer.rate_limiter import limiter
from AINDY.services.auth_service import get_current_user, is_api_key_principal

router = APIRouter(prefix="/auth/mcp-grants", tags=["MCP connected apps"])


def _require_session(current_user: dict = Depends(get_current_user)) -> dict:
    if is_api_key_principal(current_user):
        raise HTTPException(status_code=403, detail="Connected apps are managed from a signed-in session.")
    return current_user


def _run(request: Request, route_name: str, handler, *, db: Session, user_id: str, success_status_code: int = 200,
         input_payload: dict | None = None):
    result = execute_with_pipeline_sync(
        request=request,
        route_name=route_name,
        handler=handler,
        user_id=user_id,
        input_payload=input_payload or {},
        metadata={"db": db, "source": "auth.mcp_grants"},
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


@router.get("", response_model=None)
@limiter.limit("60/minute")
def list_mcp_grants(request: Request, db: Session = Depends(get_db), current_user: dict = Depends(_require_session)):
    user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.platform_layer.mcp_oauth import list_user_grants

        return {"grants": list_user_grants(db, user_id)}

    return _run(request, "auth.mcp_grants.list", handler, db=db, user_id=user_id)


@router.delete("/{grant_id}", status_code=204, response_model=None)
@limiter.limit("30/minute")
def revoke_mcp_grant(request: Request, grant_id: str, db: Session = Depends(get_db),
                     current_user: dict = Depends(_require_session)):
    user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.platform_layer.mcp_oauth import revoke_user_grant

        if not revoke_user_grant(db, user_id, grant_id):
            raise HTTPException(status_code=404, detail="No such connected app.")
        return {}

    _run(request, "auth.mcp_grants.revoke", handler, db=db, user_id=user_id, success_status_code=204,
         input_payload={"grant_id": grant_id})
    return None
