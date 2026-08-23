from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response

from AINDY.config import settings

logger = logging.getLogger(__name__)

_EXEMPT_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/ready",
    "/readiness",
    "/metrics",
)

FAILURE_ENDPOINT_PRE_PIPELINE_EXCEPTION = "endpoint_pre_pipeline_exception"
#: FR-20 — the endpoint body raised an `HTTPException` before entering the pipeline.
#: Still a contract violation, but a *deliberate* status the caller is entitled to: a
#: stale link must 404, not 500. Kept distinct from the class above so the guard can
#: record the violation without also discarding the answer.
FAILURE_ENDPOINT_HTTP_ERROR = "endpoint_http_error_before_pipeline"
FAILURE_DEPENDENCY_HTTP_ERROR = "dependency_http_error_before_endpoint"
FAILURE_VALIDATION_ERROR = "validation_error_before_endpoint"
FAILURE_UNEXPECTED_PRE_ENDPOINT_EXCEPTION = "unexpected_exception_before_endpoint"


def is_execution_exempt_path(path: str) -> bool:
    normalized = path or "/"
    return normalized == "/" or normalized.startswith(_EXEMPT_PREFIXES)


def require_execution_context(request: Request) -> None:
    if is_execution_exempt_path(request.url.path):
        return
    request.state.execution_contract_required = True
    request.state.execution_contract_route_phase = "dependency_gate"
    if hasattr(request.state, "execution_context"):
        return


def mark_execution_endpoint_entered(request: Request | None) -> None:
    if request is None or is_execution_exempt_path(request.url.path):
        return
    request.state.execution_endpoint_entered = True
    request.state.execution_contract_route_phase = "endpoint_body"


def mark_execution_pipeline_entered(request: Request | None) -> None:
    if request is None or is_execution_exempt_path(request.url.path):
        return
    request.state.execution_pipeline_entered = True
    request.state.execution_contract_route_phase = "pipeline"


def classify_execution_failure(request: Request, exc: Exception) -> str | None:
    if is_execution_exempt_path(request.url.path):
        return None
    if not getattr(request.state, "execution_contract_required", False):
        return None
    if hasattr(request.state, "execution_context"):
        request.state.execution_contract_route_phase = "pipeline_exception"
        return None
    if getattr(request.state, "execution_endpoint_entered", False):
        # FR-20 — an `HTTPException` from the endpoint body carries an intended status.
        # A dependency raising the same type already keeps its status (see the branch
        # below); only the endpoint case lost it, so the two disagreed about what an
        # HTTPException means depending on where it was raised.
        if isinstance(exc, HTTPException):
            request.state.execution_contract_failure_classification = (
                FAILURE_ENDPOINT_HTTP_ERROR
            )
            request.state.execution_contract_route_phase = "endpoint_http_error"
            return FAILURE_ENDPOINT_HTTP_ERROR
        request.state.execution_contract_failure_classification = (
            FAILURE_ENDPOINT_PRE_PIPELINE_EXCEPTION
        )
        request.state.execution_contract_route_phase = "endpoint_exception"
        return FAILURE_ENDPOINT_PRE_PIPELINE_EXCEPTION
    if isinstance(exc, RequestValidationError):
        request.state.execution_contract_failure_classification = FAILURE_VALIDATION_ERROR
        request.state.execution_contract_route_phase = "validation_failure"
        return FAILURE_VALIDATION_ERROR
    if isinstance(exc, HTTPException):
        request.state.execution_contract_failure_classification = FAILURE_DEPENDENCY_HTTP_ERROR
        request.state.execution_contract_route_phase = "dependency_http_error"
        return FAILURE_DEPENDENCY_HTTP_ERROR
    request.state.execution_contract_failure_classification = (
        FAILURE_UNEXPECTED_PRE_ENDPOINT_EXCEPTION
    )
    request.state.execution_contract_route_phase = "dependency_exception"
    return FAILURE_UNEXPECTED_PRE_ENDPOINT_EXCEPTION


def validate_execution_contract(
    request: Request,
    response: Response | None = None,
) -> None:
    if is_execution_exempt_path(request.url.path):
        return
    if not getattr(request.state, "execution_contract_required", False):
        return
    if hasattr(request.state, "execution_context"):
        return
    failure_classification = getattr(
        request.state,
        "execution_contract_failure_classification",
        None,
    )
    if failure_classification in {
        FAILURE_DEPENDENCY_HTTP_ERROR,
        FAILURE_VALIDATION_ERROR,
        FAILURE_UNEXPECTED_PRE_ENDPOINT_EXCEPTION,
        # FR-20. Without this the middleware would re-raise where the route guard
        # deliberately did not, and the status would be lost one layer further out —
        # the fix has to be made in both places or in neither.
        FAILURE_ENDPOINT_HTTP_ERROR,
    }:
        return
    message = f"ExecutionContract violation: route bypassed execution pipeline for {request.method} {request.url.path}"
    if settings.ENFORCE_EXECUTION_CONTRACT:
        raise RuntimeError(message)
    logger.warning(message, extra={"path": request.url.path})
