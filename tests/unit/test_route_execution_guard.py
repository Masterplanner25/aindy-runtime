from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI, Request

from AINDY.core.execution_guard import is_execution_exempt_path
from AINDY.core.execution_helper import execute_with_pipeline
from AINDY.core.route_execution_guard import (
    RouteExecutionViolation,
    validate_registered_route_execution,
)
from AINDY.routing import register_routes


pytestmark = pytest.mark.runtime_only


managed_router = APIRouter()


@managed_router.get("/managed")
async def managed_route(request: Request):
    return await execute_with_pipeline(
        request=request,
        route_name="test.managed",
        handler=lambda ctx: {"ok": True},
        metadata={"source": "test"},
    )


def test_registered_runtime_routes_pass_execution_audit():
    app = FastAPI()

    register_routes(app)

    validate_registered_route_execution(app)


def test_execution_audit_rejects_non_exempt_bypass_route():
    router = APIRouter()

    @router.get("/bypass")
    def bypass_route():
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)

    with pytest.raises(RouteExecutionViolation, match="/bypass"):
        validate_registered_route_execution(app)


def test_execution_audit_allows_exempt_health_route():
    router = APIRouter()

    @router.get("/health")
    def health_route():
        return {"status": "ok"}

    app = FastAPI()
    app.include_router(router)

    validate_registered_route_execution(app)
    assert is_execution_exempt_path("/health") is True


@pytest.mark.asyncio
async def test_execution_audit_recognizes_pipeline_managed_route():
    app = FastAPI()
    app.include_router(managed_router)

    validate_registered_route_execution(app)
