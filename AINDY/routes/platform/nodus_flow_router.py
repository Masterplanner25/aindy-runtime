from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from AINDY.core.execution_helper import execute_with_pipeline_sync
from AINDY.db.database import get_db
from AINDY.platform_layer.rate_limiter import limiter
from AINDY.routes.platform.nodus_shared import (
    _validate_nodus_source,
    resolve_request_db_override,
)
from AINDY.routes.platform.schemas import NodusFlowRequest
from AINDY.services.auth_service import get_current_user

router = APIRouter()


@router.post("/nodus/flow", response_model=None)
@limiter.limit("30/minute")
def compile_and_run_nodus_flow(request: Request, body: NodusFlowRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["sub"])
    effective_db = resolve_request_db_override(request, db)
    _validate_nodus_source(body.script, field="script")

    def handler(_ctx):
        # RTR-1a: parses the native Nodus workflow {} / goal {} source into its
        # step DAG; register/run delegate to the RTR-1 register_nodus_workflow
        # surface (the pre-4.x flow.step() compile→PersistentFlowRunner path is
        # retired).
        from AINDY.runtime.nodus_flow_compiler import compile_nodus_flow
        from AINDY.runtime.nodus_workflow_registry import (
            register_nodus_workflow,
            run_nodus_workflow,
        )

        try:
            graph = compile_nodus_flow(body.script)
        except (ValueError, RuntimeError) as exc:
            return {"flow_name": body.flow_name, "compiled": False, "error": str(exc)}

        response: Dict[str, Any] = {
            "flow_name": body.flow_name,
            "compiled": True,
            "workflow_name": graph["workflow_name"],
            "execution_kind": graph["execution_kind"],
            "steps": graph["steps"],
            "start": graph["start"],
            "edges": graph["edges"],
            "end": graph["end"],
            "registered": False,
        }

        # Running requires the workflow to be registered; register on demand.
        if body.register or body.run:
            register_nodus_workflow(
                body.flow_name,
                body.script,
                kind="flow-graph",
                allow_legacy_missing_provenance=True,
                overwrite=True,
                db=effective_db,
            )
            response["registered"] = True

        if body.run:
            result = run_nodus_workflow(
                body.flow_name,
                db=effective_db,
                user_id=user_id,
                input_payload=dict(body.input),
            )
            response["run_result"] = {
                "status": result.get("status"),
                "run_id": result.get("run_id"),
                "trace_id": result.get("trace_id"),
                "error": result.get("error"),
            }
        return response

    return execute_with_pipeline_sync(
        request=request,
        route_name="platform.nodus.flow",
        handler=handler,
        user_id=user_id,
        input_payload={"flow_name": body.flow_name, "run": body.run, "register": body.register},
        metadata={"db": effective_db},
    )
