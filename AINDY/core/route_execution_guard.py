"""Request-time enforcement of the route execution contract.

`enforce_registered_route_execution(app)` runs once at boot (`routing.py`) and WRAPS every
registered, non-exempt `APIRoute`. The wrapper is the guarantee, and it is request-time: on
each call it marks the endpoint entered and, after the handler returns, raises
`RouteExecutionViolation` if the router declared the contract (`require_execution_context`)
and the handler never entered the pipeline — so a bypass fails the request rather than
serving it. Routers registered WITHOUT the dependency (admin, user-owned agents, automation
logs: plain DB-query handlers) are wrapped but not required.

★ There is no boot-time static proof, and there deliberately is not one (DEC-023,
`ROUTE-AST-UNWIRED-1`). This module used to carry `validate_registered_route_execution`, an
AST walk over each endpoint's module that raised at "boot" if the endpoint could not reach
`execute_with_pipeline` by name — never called by the application, and by its own test it
rejected a route that works (a module-level alias of the pipeline helper). An unrunnable
stricter twin next to the real guard is what let the boot-time refusal be CLAIMED; it was
deleted rather than wired. The boot-time property that IS true — every non-exempt route on
the registered app is wrapped — is pinned by a derived census in
`tests/unit/test_route_execution_guard.py`.
"""

from __future__ import annotations

import inspect
import logging
from functools import wraps
from typing import Any, Generator, Iterable

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from AINDY.core.execution_guard import (
    classify_execution_failure,
    is_execution_exempt_path,
    mark_execution_endpoint_entered,
)

logger = logging.getLogger(__name__)

_ROUTE_WRAPPED_ATTR = "_aindy_execution_wrapped"
_ROUTE_ENDPOINT_ATTR = "_aindy_original_endpoint"


class RouteExecutionViolation(RuntimeError):
    """Raised when a registered route bypasses the execution pipeline."""


def _route_request_parameter_name(route: APIRoute) -> str | None:
    return getattr(route.dependant, "request_param_name", None)


def _resolve_request_argument(
    route: APIRoute,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Request | None:
    request_param_name = _route_request_parameter_name(route)
    if request_param_name:
        value = kwargs.get(request_param_name)
        if isinstance(value, Request):
            return value
    for value in args:
        if isinstance(value, Request):
            return value
    for value in kwargs.values():
        if isinstance(value, Request):
            return value
    return None


def _route_violation_message(route: APIRoute) -> str:
    endpoint = inspect.unwrap(getattr(route, _ROUTE_ENDPOINT_ATTR, route.endpoint))
    methods = ",".join(sorted(route.methods or []))
    return (
        "ExecutionContract violation: registered route bypassed execution pipeline "
        f"for {methods} {route.path} -> {endpoint.__module__}.{endpoint.__name__}"
    )


def _assert_execution_context_entered(route: APIRoute, request: Request | None) -> None:
    if request is None:
        raise RouteExecutionViolation(
            f"{_route_violation_message(route)} (managed routes must receive a Request parameter)"
        )
    if hasattr(request.state, "execution_context"):
        return
    if not getattr(request.state, "execution_contract_required", False):
        return
    raise RouteExecutionViolation(_route_violation_message(route))


def _route_exception_message(route: APIRoute, exc: Exception) -> str:
    return (
        f"{_route_violation_message(route)} "
        f"(endpoint raised {exc.__class__.__name__} before pipeline entry: {exc})"
    )


def _is_pipeline_bypass_on_error(request: Request | None) -> bool:
    """Whether an endpoint exception counts as bypassing the execution pipeline.

    ROUTE-GUARD-1. The success path (:func:`_assert_execution_context_entered`) has
    always asked two questions: did the request enter the pipeline, and *was it required
    to*. The failure path asked only the first, so any exception from a route registered
    deliberately outside the contract became a ``RouteExecutionViolation`` — a 500.

    Some routers are registered without ``require_execution_context`` on purpose:
    ``admin_router``, ``agents_router`` and ``automation_router`` are plain DB-query
    handlers, and routing.py says so at each call site. For those, an ``HTTPException``
    is the endpoint's *answer*, not evidence that it skipped anything, and turning it
    into a 500 both hides the real status and reports a violation that did not occur.
    """
    if request is None:
        return False
    if hasattr(request.state, "execution_context"):
        return False
    return bool(getattr(request.state, "execution_contract_required", False))


def _record_contract_violation(route: APIRoute, exc: Exception, *, outcome: str) -> None:
    """Record a violation on the counter an operator reads, not on the status code.

    FR-20 — before this, the *only* record of "a managed route raised before entering
    the pipeline" was the 500 the caller received. That made one signal carry two
    meanings: the app's contract slip, and the answer to the request. Preserving a
    deliberate 4xx therefore requires somewhere else for the violation to land, or the
    fix would trade a wrong status for a silent one — a straight swap of one defect for
    a worse one (this repo's `DOCS-COVERAGE-CLAIM-1` shape, applied to enforcement).
    """
    logger.error(
        "%s [outcome=%s]",
        _route_exception_message(route, exc),
        outcome,
        extra={"route": route.path, "outcome": outcome},
    )
    try:
        from AINDY.platform_layer.metrics import route_contract_violations_total

        route_contract_violations_total.labels(route=route.path, outcome=outcome).inc()
    except Exception:  # pragma: no cover - a metric must never break a request
        logger.debug("[RouteGuard] violation metric skipped", exc_info=True)


def _handle_pipeline_bypass(route: APIRoute, exc: Exception) -> None:
    """Raise ``RouteExecutionViolation`` unless the endpoint raised a deliberate status.

    FR-20: an `HTTPException` from the endpoint body is the route's *answer*. Replacing
    it with a 500 tells the caller "the server broke" when the truth was "not found" —
    and a client cannot tell those apart, which is exactly what `ROUTE-GUARD-1` cost a
    day for. The violation is real either way and is recorded either way; only the
    status the caller sees differs.
    """
    if isinstance(exc, StarletteHTTPException):
        _record_contract_violation(route, exc, outcome="status_preserved")
        return
    _record_contract_violation(route, exc, outcome="converted_500")
    raise RouteExecutionViolation(_route_exception_message(route, exc)) from exc


def _wrap_route_call(route: APIRoute, endpoint):
    if inspect.iscoroutinefunction(endpoint):

        @wraps(endpoint)
        async def wrapped(*args, **kwargs):
            request = _resolve_request_argument(route, args, kwargs)
            mark_execution_endpoint_entered(request)
            try:
                result = await endpoint(*args, **kwargs)
            except RouteExecutionViolation:
                raise
            except Exception as exc:
                if request is not None:
                    classify_execution_failure(request, exc)
                if _is_pipeline_bypass_on_error(request):
                    _handle_pipeline_bypass(route, exc)
                raise
            _assert_execution_context_entered(route, request)
            return result

        return wrapped

    @wraps(endpoint)
    def wrapped(*args, **kwargs):
        request = _resolve_request_argument(route, args, kwargs)
        mark_execution_endpoint_entered(request)
        try:
            result = endpoint(*args, **kwargs)
        except RouteExecutionViolation:
            raise
        except Exception as exc:
            if request is not None:
                classify_execution_failure(request, exc)
            if _is_pipeline_bypass_on_error(request):
                _handle_pipeline_bypass(route, exc)
            raise
        _assert_execution_context_entered(route, request)
        return result

    return wrapped


def _iter_api_routes(
    routes: Iterable,
) -> Generator[tuple[APIRoute, Any], None, None]:
    """Yield (APIRoute, top_included_router) pairs from a route list.

    Handles both FastAPI ≤ 0.135 (routes eagerly flattened into app.routes as
    APIRoute objects) and FastAPI ≥ 0.137 (include_router stores a lazy
    _IncludedRouter wrapper instead).  top_included_router is the first-level
    _IncludedRouter found in app.routes; its _effective_candidates cache must be
    invalidated after wrapping so the effective route context is rebuilt from
    the new endpoint.
    """
    try:
        from fastapi.routing import _IncludedRouter
    except ImportError:
        _IncludedRouter = None

    def _walk(route_list, top_ir):
        for route in route_list:
            if isinstance(route, APIRoute):
                yield route, top_ir
            elif _IncludedRouter is not None and isinstance(route, _IncludedRouter):
                yield from _walk(route.original_router.routes, top_ir or route)

    yield from _walk(routes, None)


def enforce_registered_route_execution(app) -> None:
    # Track top-level _IncludedRouter objects (FastAPI ≥ 0.137) whose cached
    # effective route contexts must be invalidated after wrapping, so the next
    # request rebuilds them from the wrapped endpoint.  _IncludedRouter is not
    # hashable, so we key by id() to de-duplicate.
    included_routers_to_invalidate: dict[int, Any] = {}

    for route, top_ir in _iter_api_routes(app.routes):
        if is_execution_exempt_path(route.path):
            continue
        if getattr(route, _ROUTE_WRAPPED_ATTR, False):
            continue
        if _route_request_parameter_name(route) is None:
            raise RouteExecutionViolation(
                f"{_route_violation_message(route)} (managed routes must declare a Request parameter)"
            )
        original_endpoint = route.endpoint
        wrapped_endpoint = _wrap_route_call(route, original_endpoint)
        setattr(route, _ROUTE_ENDPOINT_ATTR, original_endpoint)
        setattr(wrapped_endpoint, _ROUTE_ENDPOINT_ATTR, original_endpoint)
        route.endpoint = wrapped_endpoint
        route.dependant.call = wrapped_endpoint
        if top_ir is not None:
            included_routers_to_invalidate[id(top_ir)] = top_ir
        setattr(route, _ROUTE_WRAPPED_ATTR, True)

    for ir in included_routers_to_invalidate.values():
        ir._effective_candidates = []
        ir._effective_candidates_version = None
