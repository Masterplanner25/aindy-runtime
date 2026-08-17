from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from AINDY.core.execution_helper import execute_with_pipeline_sync
from AINDY.db.database import get_db
from AINDY.platform_layer.rate_limiter import limiter
from AINDY.routes.platform.nodus_shared import _run_flow_platform
from AINDY.routes.platform.schemas import FlowDefinition, FlowRunRequest
from AINDY.auth.api_key_auth import Scopes
from AINDY.services.auth_service import enforce_api_key_scope, get_current_user

router = APIRouter()

# ── HTTP-SCOPE-GAP-1 / KEY-SCOPE-ESCALATION-1 — per-endpoint scopes on the /platform tree ──
#
# `require_platform_admin_access` on the parent router returns **any** authenticated API key
# unconditionally, on the stated assumption that "scope enforcement happens per-endpoint or
# per-syscall". For most of this tree it did not. Demonstrated: a `flow.read`-only key reached
# every route here, drained the dead-letter queue and **rotated the platform signing key**.
#
# For JWT callers nothing changes — the parent gate already required `is_admin`, and an admin
# session derives `platform.admin` and `webhook.manage`. Only API keys are newly constrained,
# which is the point.
_REQUIRE_PLATFORM_ADMIN = Depends(enforce_api_key_scope(Scopes.PLATFORM_ADMIN))


def _execute_flows(
    request: Request,
    route_name: str,
    handler,
    *,
    user_id: str,
    db: Session | None = None,
    input_payload=None,
    success_status_code: int = 200,
):
    metadata = {"source": "platform.flows"}
    if db is not None:
        metadata["db"] = db
    result = execute_with_pipeline_sync(
        request=request,
        route_name=route_name,
        handler=handler,
        user_id=user_id,
        input_payload=input_payload or {},
        metadata=metadata,
        success_status_code=success_status_code,
        return_result=True,
    )
    if not result.success:
        detail = result.metadata.get("detail") or result.error or "Execution failed"
        raise HTTPException(
            status_code=int(result.metadata.get("status_code", 500)),
            detail=detail,
        )
    data = result.data
    if isinstance(data, dict):
        data = dict(data)
        data.pop("execution_envelope", None)
    return data


@router.get("/flows/strategies", response_model=None)
@limiter.limit("60/minute")
def get_flow_strategies(request: Request, current_user: dict = Depends(get_current_user), _s: None = Depends(enforce_api_key_scope(Scopes.FLOW_READ))):
    def handler(ctx):
        from AINDY.platform_layer.registry import get_all_flow_strategies
        from AINDY.kernel.scheduler.common import PRIORITY_ORDER, MAX_PER_SCHEDULE_CYCLE
        from AINDY.core.retry_policy import (
            FLOW_NODE_DEFAULT, AGENT_LOW_MEDIUM, AGENT_HIGH_RISK,
            ASYNC_JOB_DEFAULT, NODUS_SCHEDULED_DEFAULT,
        )

        registered = get_all_flow_strategies()
        strategies = [
            {
                "id": flow_type,
                "intent_type": flow_type,
                "user_id": None,
                "score": None,
                "usage_count": 0,
                "success_count": 0,
                "flow": {
                    "handler": getattr(h, "__qualname__", None) or getattr(h, "__name__", repr(h)),
                    "type": flow_type,
                },
            }
            for flow_type, h in sorted(registered.items())
        ]

        def _policy_dict(p):
            return {
                "max_attempts": p.max_attempts,
                "backoff_ms": p.backoff_ms,
                "exponential_backoff": p.exponential_backoff,
                "execution_guarantee": p.execution_guarantee,
            }

        return {
            "strategies": strategies,
            "count": len(strategies),
            "scheduling": {
                "priority_tiers": list(PRIORITY_ORDER),
                "max_per_cycle": MAX_PER_SCHEDULE_CYCLE,
                "dispatch_model": "priority-first, round-robin per tenant",
            },
            "retry_policies": {
                "flow_node": _policy_dict(FLOW_NODE_DEFAULT),
                "agent_low_medium": _policy_dict(AGENT_LOW_MEDIUM),
                "agent_high_risk": {**_policy_dict(AGENT_HIGH_RISK), "high_risk_immediate_fail": AGENT_HIGH_RISK.high_risk_immediate_fail},
                "async_job": _policy_dict(ASYNC_JOB_DEFAULT),
                "nodus_scheduled": _policy_dict(NODUS_SCHEDULED_DEFAULT),
            },
        }

    return _execute_flows(
        request,
        "platform.flows.strategies",
        handler,
        user_id=str(current_user["sub"]),
    )


@router.post("/flows", status_code=201, response_model=None)
@limiter.limit("30/minute")
def create_flow(request: Request, body: FlowDefinition, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user), _scope: None = _REQUIRE_PLATFORM_ADMIN):
    user_id = str(current_user["sub"])

    def handler(ctx):
        from AINDY.runtime.flow_registry import register_dynamic_flow

        try:
            return register_dynamic_flow(
                name=body.name,
                nodes=body.nodes,
                edges=body.edges,
                start=body.start,
                end=body.end,
                user_id=user_id,
                owner_class=body.owner_class,
                provenance=body.provenance.model_dump() if body.provenance else None,
                overwrite=body.overwrite,
                db=db,
            )
        except ValueError as exc:
            errors = exc.args[0]
            raise HTTPException(
                status_code=422,
                detail={"errors": errors if isinstance(errors, list) else [str(errors)]},
            )

    return _execute_flows(
        request,
        "platform.flows.create",
        handler,
        user_id=user_id,
        db=db,
        input_payload=body.model_dump(),
        success_status_code=201,
    )


@router.get("/flows", response_model=None)
@limiter.limit("60/minute")
def list_flows(request: Request, current_user: dict = Depends(get_current_user), _s: None = Depends(enforce_api_key_scope(Scopes.FLOW_READ))):
    def handler(ctx):
        from AINDY.runtime.flow_registry import list_dynamic_flows

        return {"flows": list_dynamic_flows()}

    return _execute_flows(
        request,
        "platform.flows.list",
        handler,
        user_id=str(current_user["sub"]),
    )


@router.get("/flows/{name}", response_model=None)
@limiter.limit("60/minute")
def get_flow(request: Request, name: str, current_user: dict = Depends(get_current_user), _s: None = Depends(enforce_api_key_scope(Scopes.FLOW_READ))):
    def handler(ctx):
        from AINDY.runtime.flow_registry import get_dynamic_flow

        meta = get_dynamic_flow(name)
        if not meta:
            raise HTTPException(status_code=404, detail=f"Flow {name!r} not found")
        return meta

    return _execute_flows(
        request,
        "platform.flows.get",
        handler,
        user_id=str(current_user["sub"]),
        input_payload={"name": name},
    )


@router.post("/flows/{name}/run", response_model=None)
@limiter.limit("30/minute")
def run_flow_endpoint(request: Request, name: str, body: FlowRunRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user), _s: None = Depends(enforce_api_key_scope(Scopes.FLOW_EXECUTE))):
    from AINDY.runtime.flow_engine import FLOW_REGISTRY

    user_id = str(current_user["sub"])
    if name not in FLOW_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Flow {name!r} is not registered")

    return execute_with_pipeline_sync(
        request=request,
        route_name="platform.flows.run",
        handler=lambda _ctx: _run_flow_platform(name, body.state, db, user_id),
        user_id=user_id,
        input_payload={"flow_name": name, **body.state},
        metadata={"db": db},
    )


@router.delete("/flows/{name}", status_code=204, response_model=None)
@limiter.limit("30/minute")
def delete_flow(request: Request, name: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user), _scope: None = _REQUIRE_PLATFORM_ADMIN):
    def handler(ctx):
        from AINDY.runtime.flow_registry import delete_dynamic_flow

        removed = delete_dynamic_flow(name, db=db)
        if not removed:
            raise HTTPException(
                status_code=404,
                detail=f"Flow {name!r} not found or is a static flow (only dynamic flows can be deleted)",
            )
        return None

    return _execute_flows(
        request,
        "platform.flows.delete",
        handler,
        user_id=str(current_user["sub"]),
        db=db,
        input_payload={"name": name},
        success_status_code=204,
    )
