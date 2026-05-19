"""Validate that registered runtime routes enter the execution pipeline."""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi.routing import APIRoute

from AINDY.core.execution_guard import is_execution_exempt_path

_PIPELINE_CALLS = {"execute_with_pipeline", "execute_with_pipeline_sync"}


class RouteExecutionViolation(Exception):
    """Raised when a registered route handler bypasses the execution pipeline."""


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
    endpoint = inspect.unwrap(route.endpoint)
    module = inspect.getmodule(endpoint)
    source_file = inspect.getsourcefile(endpoint)
    if module is None or source_file is None:
        return False
    if not source_file.endswith(".py"):
        return False
    analysis = _analyse_module(source_file)
    return analysis.function_uses_pipeline(endpoint.__name__)


def validate_registered_route_execution(app) -> None:
    violations: list[str] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if is_execution_exempt_path(route.path):
            continue
        if _route_uses_execution_pipeline(route):
            continue
        endpoint = inspect.unwrap(route.endpoint)
        methods = ",".join(sorted(route.methods or []))
        violations.append(
            f"{methods} {route.path} -> {endpoint.__module__}.{endpoint.__name__}"
        )

    if not violations:
        return

    message = ["RouteExecutionViolation: registered routes bypass execution pipeline:"]
    message.extend(f"  {line}" for line in violations)
    raise RouteExecutionViolation("\n".join(message))
