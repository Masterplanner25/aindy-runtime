from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from AINDY.core.execution_guard import (
    FAILURE_DEPENDENCY_HTTP_ERROR,
    FAILURE_UNEXPECTED_PRE_ENDPOINT_EXCEPTION,
    FAILURE_VALIDATION_ERROR,
    classify_execution_failure,
    is_execution_exempt_path,
    require_execution_context,
)
from AINDY.core.execution_helper import execute_with_pipeline
from AINDY.core.route_execution_guard import (
    RouteExecutionViolation,
    enforce_registered_route_execution,
    validate_registered_route_execution,
)
from AINDY.exception_handlers import register_exception_handlers
from AINDY.middleware import enforce_execution_contract
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


def _build_managed_test_app(router: APIRouter) -> FastAPI:
    app = FastAPI()
    app.include_router(router, dependencies=[Depends(require_execution_context)])
    app.middleware("http")(enforce_execution_contract)
    register_exception_handlers(app)
    enforce_registered_route_execution(app)
    return app


def test_dependency_http_exception_before_endpoint_is_allowed_and_classified():
    router = APIRouter()

    def reject_dependency():
        raise HTTPException(status_code=401, detail="Authentication required")

    @router.get("/auth-fail")
    def auth_fail_route(request: Request, _: None = Depends(reject_dependency)):
        return {"ok": True}

    app = _build_managed_test_app(router)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/auth-fail")

    assert response.status_code == 401
    assert response.json()["error"] == "http_error"
    assert app.dependency_overrides == {}


def test_validation_failure_before_endpoint_is_allowed_and_classified():
    router = APIRouter()

    @router.get("/validation-fail")
    def validation_fail_route(request: Request, count: int):
        return {"count": count}

    app = _build_managed_test_app(router)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/validation-fail", params={"count": "not-an-int"})

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_unexpected_dependency_exception_before_endpoint_preserves_original_500():
    router = APIRouter()

    def broken_dependency():
        raise ValueError("dependency exploded")

    @router.get("/dependency-error")
    def dependency_error_route(request: Request, _: None = Depends(broken_dependency)):
        return {"ok": True}

    app = _build_managed_test_app(router)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/dependency-error")

    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"


def test_endpoint_http_exception_before_pipeline_is_contract_violation():
    router = APIRouter()

    @router.get("/endpoint-http-error")
    def endpoint_http_error_route(request: Request):
        raise HTTPException(status_code=418, detail="teapot")

    app = _build_managed_test_app(router)

    with TestClient(app, raise_server_exceptions=True) as client:
        with pytest.raises(RouteExecutionViolation, match="/endpoint-http-error"):
            client.get("/endpoint-http-error")


def test_execution_failure_classification_distinguishes_pre_endpoint_cases():
    request = StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/managed",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
        }
    )
    require_execution_context(request)

    classification = classify_execution_failure(
        request,
        HTTPException(status_code=401, detail="Authentication required"),
    )
    assert classification == FAILURE_DEPENDENCY_HTTP_ERROR

    validation_request = StarletteRequest(dict(request.scope))
    require_execution_context(validation_request)
    validation_classification = classify_execution_failure(
        validation_request,
        RequestValidationError([]),
    )
    assert validation_classification == FAILURE_VALIDATION_ERROR

    unexpected_request = StarletteRequest(dict(request.scope))
    require_execution_context(unexpected_request)
    unexpected_classification = classify_execution_failure(
        unexpected_request,
        ValueError("dependency exploded"),
    )
    assert unexpected_classification == FAILURE_UNEXPECTED_PRE_ENDPOINT_EXCEPTION
