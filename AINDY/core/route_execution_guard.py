"""Strong runtime enforcement for managed route execution."""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.routing import APIRoute

from AINDY.core.execution_guard import (
    classify_execution_failure,
    is_execution_exempt_path,
    mark_execution_endpoint_entered,
)

_PIPELINE_CALLS = {"execute_with_pipeline", "execute_with_pipeline_sync"}
_ROUTE_WRAPPED_ATTR = "_aindy_execution_wrapped"
_ROUTE_ENDPOINT_ATTR = "_aindy_original_endpoint"


class RouteExecutionViolation(RuntimeError):
    """Raised when a registered route bypasses the execution pipeline."""


@dataclass(frozen=True)
class _ModuleAnalysis:
    direct_pipeline_functions: frozenset[str]
    call_graph: dict[str, frozenset[str]]

    def function_uses_pipeline(self, function_name: str) -> bool:
        return _function_uses_pipeline(
            function_name,
            self.direct_pipeline_functions,
            self.call_graph,
            seen=frozenset(),
        )


def _called_function_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _function_uses_pipeline(
    function_name: str,
    direct_pipeline_functions: frozenset[str],
    call_graph: dict[str, frozenset[str]],
    *,
    seen: frozenset[str],
) -> bool:
    if function_name in direct_pipeline_functions:
        return True
    if function_name in seen:
        return False
    for callee in call_graph.get(function_name, frozenset()):
        if _function_uses_pipeline(
            callee,
            direct_pipeline_functions,
            call_graph,
            seen=seen | {function_name},
        ):
            return True
    return False


@lru_cache(maxsize=None)
def _analyse_module(module_path: str) -> _ModuleAnalysis:
    path = Path(module_path)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=module_path)

    direct_pipeline_functions: set[str] = set()
    call_graph: dict[str, set[str]] = {}

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        function_name = node.name
        calls: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            called_name = _called_function_name(child)
            if called_name is None:
                continue
            calls.add(called_name)
            if called_name in _PIPELINE_CALLS:
                direct_pipeline_functions.add(function_name)
        call_graph[function_name] = calls

    return _ModuleAnalysis(
        direct_pipeline_functions=frozenset(direct_pipeline_functions),
        call_graph={name: frozenset(calls) for name, calls in call_graph.items()},
    )


def _route_uses_execution_pipeline(route: APIRoute) -> bool:
    endpoint = inspect.unwrap(getattr(route, _ROUTE_ENDPOINT_ATTR, route.endpoint))
    module = inspect.getmodule(endpoint)
    source_file = inspect.getsourcefile(endpoint)
    if module is None or source_file is None or not source_file.endswith(".py"):
        return False
    analysis = _analyse_module(source_file)
    return analysis.function_uses_pipeline(endpoint.__name__)


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
    raise RouteExecutionViolation(_route_violation_message(route))


def _route_exception_message(route: APIRoute, exc: Exception) -> str:
    return (
        f"{_route_violation_message(route)} "
        f"(endpoint raised {exc.__class__.__name__} before pipeline entry: {exc})"
    )


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
                if request is not None and not hasattr(request.state, "execution_context"):
                    raise RouteExecutionViolation(_route_exception_message(route, exc)) from exc
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
            if request is not None and not hasattr(request.state, "execution_context"):
                raise RouteExecutionViolation(_route_exception_message(route, exc)) from exc
            raise
        _assert_execution_context_entered(route, request)
        return result

    return wrapped


def enforce_registered_route_execution(app) -> None:
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
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
        setattr(route, _ROUTE_WRAPPED_ATTR, True)


def validate_registered_route_execution(app) -> None:
    violations: list[str] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if is_execution_exempt_path(route.path):
            continue
        if _route_uses_execution_pipeline(route):
            continue
        endpoint = inspect.unwrap(getattr(route, _ROUTE_ENDPOINT_ATTR, route.endpoint))
        methods = ",".join(sorted(route.methods or []))
        violations.append(
            f"{methods} {route.path} -> {endpoint.__module__}.{endpoint.__name__}"
        )

    if not violations:
        return

    message = ["RouteExecutionViolation: registered routes bypass execution pipeline heuristics:"]
    message.extend(f"  {line}" for line in violations)
    raise RouteExecutionViolation("\n".join(message))
