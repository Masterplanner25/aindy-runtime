"""EFFECT-OUTCOME-UNKNOWN-1 phase 4 — reconcile effects whose outcome is unknown.

An `unknown` effect is held: never re-run on replay (DEC-085), never reaped by TTL (DEC-087). These
routes are how an operator settles one, after checking the counterparty, which the runtime cannot:

* ``GET  /platform/effects/unknown`` lists the unresolved ones, oldest first, with their detail.
* ``POST /platform/effects/{action_id}/resolve {"status": "success"|"failed", "note"}``.
  ``success``: it landed, so later calls replay it. ``failed``: it did not, so the slot is freed and
  a retry may run it. The note is required: the audit event `effect.reconciled` carries who resolved
  it, to what, and why, in the SAME transaction as the change.

Admin only: the `/platform` parent gate, plus the `platform.admin` scope for an API key.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from AINDY.auth.api_key_auth import Scopes
from AINDY.core.execution_helper import execute_with_pipeline
from AINDY.db.database import get_db
from AINDY.platform_layer.rate_limiter import limiter
from AINDY.services.auth_service import enforce_api_key_scope, get_current_user

router = APIRouter()
_REQUIRE_PLATFORM_ADMIN = Depends(enforce_api_key_scope(Scopes.PLATFORM_ADMIN))


class ResolveEffectRequest(BaseModel):
    status: Literal["success", "failed"]
    note: str = Field(..., min_length=1, max_length=2000)


def _refresh_gauge(db) -> None:
    try:
        from AINDY.kernel.effect_ledger import count_unknown_effects
        from AINDY.platform_layer.metrics import effect_unknown_unresolved

        effect_unknown_unresolved.set(count_unknown_effects(db))
    except Exception:  # noqa: BLE001 — observability never fails the request
        pass


@router.get("/effects/unknown", response_model=None)
@limiter.limit("60/minute")
async def list_unknown_effects_route(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    def handler(ctx):
        from AINDY.kernel.effect_ledger import list_unknown_effects

        items = list_unknown_effects(db, limit=limit)
        return {"effects": items, "count": len(items)}

    return await execute_with_pipeline(
        request=request,
        route_name="platform.effects.unknown.list",
        handler=handler,
        user_id=str(current_user["sub"]),
        input_payload={"limit": limit},
        metadata={"source": "platform.effects"},
    )


@router.post("/effects/{action_id}/resolve", response_model=None)
@limiter.limit("30/minute")
async def resolve_unknown_effect(
    request: Request,
    action_id: str,
    body: ResolveEffectRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _scope: None = _REQUIRE_PLATFORM_ADMIN,
):
    def handler(ctx):
        from AINDY.core.execution_signal_helper import queue_system_event
        from AINDY.core.system_event_types import SystemEventTypes
        from AINDY.kernel.effect_ledger import EffectNotFound, EffectNotReconcilable, reconcile_effect_record

        actor = str(current_user["sub"])
        try:
            result = reconcile_effect_record(db, action_id, body.status, commit=False)
        except EffectNotFound:
            raise HTTPException(status_code=404, detail="effect not found")
        except EffectNotReconcilable as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        queue_system_event(
            db=db,
            event_type=SystemEventTypes.EFFECT_RECONCILED,
            user_id=actor,
            payload={**result, "note": body.note, "resolved_by": actor},
            required=True,
        )
        db.commit()  # the resolution and its audit event land together
        _refresh_gauge(db)
        return {**result, "note": body.note, "resolved_by": actor}

    return await execute_with_pipeline(
        request=request,
        route_name="platform.effects.resolve",
        handler=handler,
        user_id=str(current_user["sub"]),
        input_payload={"action_id": action_id, "status": body.status},
        metadata={"source": "platform.effects"},
    )
