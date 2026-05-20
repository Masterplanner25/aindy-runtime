from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from AINDY.core.execution_guard import is_execution_exempt_path
from AINDY.core.execution_helper import execute_with_pipeline
from AINDY.core.route_execution_guard import (
    RouteExecutionViolation,
    enforce_registered_route_execution,
    validate_registered_route_execution,
)
from AINDY.routing import register_routes


pytestmark = pytest.mark.runtime_only


managed_router = APIRouter()
pipeline_alias = execute_with_pipeline


@managed_router.get("/managed")
async def managed_route(request: Request):
    return await execute_with_pipeline(
        request=request,
        route_name="test.managed",
        handler=lambda ctx: {"ok": True},
        metadata={"source": "test"},
    )


@managed_router.get("/managed-alias")
async def managed_alias_route(request: Request):
    return await pipeline_alias(
        request=request,
        route_name="test.managed.alias",
        handler=lambda ctx: {"ok": True, "alias": True},
        metadata={"source": "test"},
    )


def test_registered_runtime_routes_are_wrapped_for_execution_enforcement():
    app = FastAPI()

    register_routes(app)

    managed_routes = [route for route in app.routes if getattr(route, "path", None) == "/api/version"]
    assert managed_routes
    assert getattr(managed_routes[0], "_aindy_execution_wrapped", False) is True


def test_managed_route_succeeds_under_runtime_enforcement():
    app = FastAPI()
    app.include_router(managed_router)
    enforce_registered_route_execution(app)

    with TestClient(app) as client:
        response = client.get("/managed")

    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True


def test_exempt_route_is_not_required_to_enter_pipeline():
    router = APIRouter()

    @router.get("/health")
    def health_route():
        return {"status": "ok"}

    app = FastAPI()
    app.include_router(router)
    enforce_registered_route_execution(app)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert is_execution_exempt_path("/health") is True


def test_helper_indirection_route_is_allowed_by_runtime_wrapper_even_if_ast_audit_is_stricter():
    app = FastAPI()
    app.include_router(managed_router)
    enforce_registered_route_execution(app)

    with TestClient(app) as client:
        response = client.get("/managed-alias")

    assert response.status_code == 200
    assert response.json()["data"]["alias"] is True
    with pytest.raises(RouteExecutionViolation, match="/managed-alias"):
        validate_registered_route_execution(app)


def test_runtime_wrapper_blocks_successful_bypass_route():
    router = APIRouter()

    @router.get("/bypass")
    def bypass_route(request: Request):
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    enforce_registered_route_execution(app)

    with TestClient(app, raise_server_exceptions=True) as client:
        with pytest.raises(RouteExecutionViolation, match="/bypass"):
            client.get("/bypass")


def test_non_exempt_route_without_request_fails_closed_at_registration():
    router = APIRouter()

    @router.get("/missing-request")
    def missing_request_route():
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)

    with pytest.raises(RouteExecutionViolation, match="must declare a Request parameter"):
        enforce_registered_route_execution(app)
