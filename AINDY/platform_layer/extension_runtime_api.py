from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from AINDY.platform_layer.extension_capabilities import (
    CAP_EVENT_EMIT,
    CAP_FLOW_RUN,
    CAP_MEMORY_READ,
    CAP_MEMORY_WRITE,
    CAP_OUTBOUND_HTTP,
    CAP_TOOL_INVOKE,
)

_RUNTIME_API_BRIDGE: Callable[[str, dict[str, Any] | None], Any] | None = None
_TRUSTED_RUNTIME_CALL_ACTIVE = False
_ALLOWED_BOOTSTRAP_CALLERS = {
    "AINDY.platform_layer.extension_worker",
    "__main__",
}


def _assert_runtime_channel_caller() -> None:
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    module_name = ""
    while caller is not None:
        candidate = str(caller.f_globals.get("__name__", ""))
        if candidate != __name__:
            module_name = candidate
            break
        caller = caller.f_back
    if module_name not in _ALLOWED_BOOTSTRAP_CALLERS:
        raise PermissionError(
            "extension runtime channel bootstrap is restricted to the extension worker"
        )


def _install_runtime_api_channel(
    *,
    bridge: Callable[[str, dict[str, Any] | None], Any],
) -> None:
    global _RUNTIME_API_BRIDGE
    _assert_runtime_channel_caller()
    _RUNTIME_API_BRIDGE = bridge


def _clear_runtime_api_channel() -> None:
    global _RUNTIME_API_BRIDGE
    _assert_runtime_channel_caller()
    _RUNTIME_API_BRIDGE = None


def _trusted_runtime_call_active() -> bool:
    return _TRUSTED_RUNTIME_CALL_ACTIVE


def _run_trusted_runtime_operation(fn: Callable[[], Any]) -> Any:
    global _TRUSTED_RUNTIME_CALL_ACTIVE
    _assert_runtime_channel_caller()
    previous = _TRUSTED_RUNTIME_CALL_ACTIVE
    _TRUSTED_RUNTIME_CALL_ACTIVE = True
    try:
        return fn()
    finally:
        _TRUSTED_RUNTIME_CALL_ACTIVE = previous


def _require_authenticated_channel() -> Callable[[str, dict[str, Any] | None], Any]:
    bridge = _RUNTIME_API_BRIDGE
    if bridge is None:
        raise PermissionError(
            "UNAUTHENTICATED_EXTENSION_CHANNEL: sandboxed plugin runtime calls require a worker-authenticated channel"
        )
    return bridge


def _invoke_runtime_api(operation: str, payload: dict[str, Any] | None = None) -> Any:
    bridge = _require_authenticated_channel()
    return bridge(operation, dict(payload or {}))


def get_granted_capabilities() -> list[str]:
    result = _invoke_runtime_api("capabilities.list")
    if not isinstance(result, list):
        raise RuntimeError("extension runtime API returned invalid capabilities payload")
    return [str(item) for item in result]


def get_execution_metadata() -> dict[str, Any]:
    result = _invoke_runtime_api("execution.metadata")
    if not isinstance(result, dict):
        raise RuntimeError("extension runtime API returned invalid execution metadata payload")
    return dict(result)


def require_capability(capability: str) -> None:
    granted = set(get_granted_capabilities())
    if capability not in granted:
        raise PermissionError(
            f"Extension capability {capability!r} not granted; granted={sorted(granted)!r}"
        )


def memory_read(*, query: str = "", limit: int = 5, tags: list[str] | None = None) -> dict[str, Any]:
    require_capability(CAP_MEMORY_READ)
    result = _invoke_runtime_api(
        "memory.read",
        {"query": query, "limit": int(limit), "tags": list(tags or [])},
    )
    if not isinstance(result, dict):
        raise RuntimeError("extension runtime API returned invalid memory.read payload")
    return result


def memory_write(
    *,
    content: str,
    tags: list[str] | None = None,
    node_type: str = "insight",
) -> dict[str, Any]:
    require_capability(CAP_MEMORY_WRITE)
    result = _invoke_runtime_api(
        "memory.write",
        {"content": content, "tags": list(tags or []), "node_type": node_type},
    )
    if not isinstance(result, dict):
        raise RuntimeError("extension runtime API returned invalid memory.write payload")
    return result


def flow_run(*, flow_name: str, initial_state: dict[str, Any] | None = None) -> dict[str, Any]:
    require_capability(CAP_FLOW_RUN)
    result = _invoke_runtime_api(
        "flow.run",
        {"flow_name": flow_name, "initial_state": dict(initial_state or {})},
    )
    if not isinstance(result, dict):
        raise RuntimeError("extension runtime API returned invalid flow.run payload")
    return result


def event_emit(*, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    require_capability(CAP_EVENT_EMIT)
    result = _invoke_runtime_api(
        "event.emit",
        {"event_type": event_type, "payload": dict(payload or {})},
    )
    if not isinstance(result, dict):
        raise RuntimeError("extension runtime API returned invalid event.emit payload")
    return result


def tool_invoke(*, tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    require_capability(CAP_TOOL_INVOKE)
    result = _invoke_runtime_api(
        "tool.invoke",
        {"tool_name": tool_name, "args": dict(args or {})},
    )
    if not isinstance(result, dict):
        raise RuntimeError("extension runtime API returned invalid tool.invoke payload")
    return result


def outbound_http_enabled() -> bool:
    return CAP_OUTBOUND_HTTP in set(get_granted_capabilities())


__all__ = [
    "event_emit",
    "flow_run",
    "get_execution_metadata",
    "get_granted_capabilities",
    "memory_read",
    "memory_write",
    "outbound_http_enabled",
    "require_capability",
    "tool_invoke",
]
