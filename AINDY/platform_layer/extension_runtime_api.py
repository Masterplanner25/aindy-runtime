from __future__ import annotations

from typing import Any

from AINDY.agents.tool_registry import execute_tool
from AINDY.db.database import SessionLocal
from AINDY.kernel.syscall_dispatcher import dispatch_syscall
from AINDY.platform_layer.extension_capabilities import (
    CAP_EVENT_EMIT,
    CAP_FLOW_RUN,
    CAP_MEMORY_READ,
    CAP_MEMORY_WRITE,
    CAP_OUTBOUND_HTTP,
    CAP_TOOL_INVOKE,
)

_RUNTIME_CONTEXT: dict[str, Any] = {
    "user_id": "",
    "capabilities": [],
    "run_id": "",
    "trace_id": "",
    "extension_name": "",
    "owner_class": "",
}
_TRUSTED_RUNTIME_CALL_ACTIVE = False


def _configure_runtime_context(context: dict[str, Any]) -> None:
    _RUNTIME_CONTEXT.clear()
    _RUNTIME_CONTEXT.update(
        {
            "user_id": str(context.get("user_id") or ""),
            "capabilities": list(context.get("granted_capabilities") or []),
            "run_id": str(context.get("run_id") or context.get("trace_id") or ""),
            "trace_id": str(context.get("trace_id") or context.get("run_id") or ""),
            "extension_name": str(context.get("extension_name") or ""),
            "owner_class": str(context.get("owner_class") or ""),
        }
    )


def get_granted_capabilities() -> list[str]:
    return list(_RUNTIME_CONTEXT.get("capabilities") or [])


def _trusted_runtime_call_active() -> bool:
    return _TRUSTED_RUNTIME_CALL_ACTIVE


def _runtime_call(fn):
    global _TRUSTED_RUNTIME_CALL_ACTIVE
    previous = _TRUSTED_RUNTIME_CALL_ACTIVE
    _TRUSTED_RUNTIME_CALL_ACTIVE = True
    try:
        return fn()
    finally:
        _TRUSTED_RUNTIME_CALL_ACTIVE = previous


def require_capability(capability: str) -> None:
    granted = set(get_granted_capabilities())
    if capability not in granted:
        raise PermissionError(
            f"Extension capability {capability!r} not granted; granted={sorted(granted)!r}"
        )


def _require_runtime_tenant() -> str:
    tenant_user_id = str(_RUNTIME_CONTEXT.get("user_id") or "").strip()
    if not tenant_user_id:
        raise PermissionError("TENANT_VIOLATION: extension runtime call requires a tenant-scoped user_id")
    return tenant_user_id


def _extension_call_metadata(operation: str) -> dict[str, Any]:
    tenant_user_id = _require_runtime_tenant()
    extension_name = str(_RUNTIME_CONTEXT.get("extension_name") or "").strip()
    owner_class = str(_RUNTIME_CONTEXT.get("owner_class") or "").strip()
    return {
        "surface": "extension-runtime-api",
        "operation": operation,
        "tenant_user_id": tenant_user_id,
        "extension_name": extension_name,
        "owner_class": owner_class,
    }


def _extension_source_label() -> str:
    extension_name = str(_RUNTIME_CONTEXT.get("extension_name") or "").strip()
    return f"extension:{extension_name}" if extension_name else "extension-runtime-api"


def _sanitize_tool_args(args: dict[str, Any] | None) -> dict[str, Any]:
    sanitized = dict(args or {})
    tenant_user_id = _require_runtime_tenant()
    for key in ("user_id", "tenant_id"):
        if key not in sanitized:
            continue
        value = str(sanitized.get(key) or "").strip()
        if value and value != tenant_user_id:
            raise PermissionError(
                f"TENANT_VIOLATION: extension tool call may not set {key}={value!r} "
                f"outside tenant context {tenant_user_id!r}"
            )
        sanitized[key] = tenant_user_id
    return sanitized


def memory_read(*, query: str = "", limit: int = 5, tags: list[str] | None = None) -> dict[str, Any]:
    require_capability(CAP_MEMORY_READ)
    tenant_user_id = _require_runtime_tenant()
    return _runtime_call(
        lambda: dispatch_syscall(
            "sys.v1.memory.read",
            {"query": query, "limit": int(limit), "tags": list(tags or [])},
            user_id=tenant_user_id,
            capability=CAP_MEMORY_READ,
            trace_id=str(_RUNTIME_CONTEXT.get("trace_id") or _RUNTIME_CONTEXT.get("run_id") or ""),
            execution_unit_id=str(_RUNTIME_CONTEXT.get("run_id") or _RUNTIME_CONTEXT.get("trace_id") or ""),
            metadata={"_extension_call": _extension_call_metadata("memory.read")},
        )
    )


def memory_write(
    *,
    content: str,
    tags: list[str] | None = None,
    node_type: str = "execution",
) -> dict[str, Any]:
    require_capability(CAP_MEMORY_WRITE)
    tenant_user_id = _require_runtime_tenant()
    return _runtime_call(
        lambda: dispatch_syscall(
            "sys.v1.memory.write",
            {
                "content": content,
                "tags": list(tags or []),
                "node_type": node_type,
                "source": _extension_source_label(),
            },
            user_id=tenant_user_id,
            capability=CAP_MEMORY_WRITE,
            trace_id=str(_RUNTIME_CONTEXT.get("trace_id") or _RUNTIME_CONTEXT.get("run_id") or ""),
            execution_unit_id=str(_RUNTIME_CONTEXT.get("run_id") or _RUNTIME_CONTEXT.get("trace_id") or ""),
            metadata={"_extension_call": _extension_call_metadata("memory.write")},
        )
    )


def flow_run(*, flow_name: str, initial_state: dict[str, Any] | None = None) -> dict[str, Any]:
    require_capability(CAP_FLOW_RUN)
    tenant_user_id = _require_runtime_tenant()
    return _runtime_call(
        lambda: dispatch_syscall(
            "sys.v1.flow.run",
            {"flow_name": flow_name, "initial_state": dict(initial_state or {})},
            user_id=tenant_user_id,
            capability=CAP_FLOW_RUN,
            trace_id=str(_RUNTIME_CONTEXT.get("trace_id") or _RUNTIME_CONTEXT.get("run_id") or ""),
            execution_unit_id=str(_RUNTIME_CONTEXT.get("run_id") or _RUNTIME_CONTEXT.get("trace_id") or ""),
            metadata={"_extension_call": _extension_call_metadata("flow.run")},
        )
    )


def event_emit(*, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    require_capability(CAP_EVENT_EMIT)
    tenant_user_id = _require_runtime_tenant()
    return _runtime_call(
        lambda: dispatch_syscall(
            "sys.v1.event.emit",
            {"event_type": event_type, "payload": dict(payload or {})},
            user_id=tenant_user_id,
            capability=CAP_EVENT_EMIT,
            trace_id=str(_RUNTIME_CONTEXT.get("trace_id") or _RUNTIME_CONTEXT.get("run_id") or ""),
            execution_unit_id=str(_RUNTIME_CONTEXT.get("run_id") or _RUNTIME_CONTEXT.get("trace_id") or ""),
            metadata={"_extension_call": _extension_call_metadata("event.emit")},
        )
    )


def tool_invoke(*, tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    require_capability(CAP_TOOL_INVOKE)
    tenant_user_id = _require_runtime_tenant()
    sanitized_args = _sanitize_tool_args(args)
    def _invoke() -> dict[str, Any]:
        db = SessionLocal()
        try:
            result = execute_tool(
                tool_name,
                sanitized_args,
                user_id=tenant_user_id,
                db=db,
                run_id=None,
                execution_token=None,
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error") or f"tool {tool_name!r} failed")
            value = result.get("result")
            return value if isinstance(value, dict) else {"result": value}
        finally:
            db.close()

    return _runtime_call(_invoke)


def outbound_http_enabled() -> bool:
    return CAP_OUTBOUND_HTTP in set(get_granted_capabilities())
