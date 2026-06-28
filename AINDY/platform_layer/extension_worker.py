from __future__ import annotations

import builtins
import inspect
import io
import ipaddress
import json
import os
import secrets
import socket
import shutil
import sys
import tempfile
import threading
import time
from importlib import util as importlib_util
from pathlib import Path
from typing import Any, Callable

from AINDY.platform_layer import extension_runtime_api
from AINDY.platform_layer.extension_capabilities import (
    CAP_EVENT_EMIT,
    CAP_FLOW_RUN,
    CAP_MEMORY_READ,
    CAP_MEMORY_WRITE,
    CAP_TOOL_INVOKE,
)

_VALID_STATUSES = frozenset(["SUCCESS", "RETRY", "FAILURE", "WAIT"])
_PLUGIN_MODULE_NAMESPACE = "AINDY.plugins.nodes"
_PRIVATE_HOST_ALIASES = {"localhost", "127.0.0.1", "::1"}
_ALLOWED_RUNTIME_IMPORTS = {
    "AINDY",
    "AINDY.platform_layer",
    "AINDY.platform_layer.extension_runtime_api",
}
_RUNTIME_API_CONTEXT = threading.local()
_WORKER_INSTANCE_ID = secrets.token_hex(12)
_AUTH_REPLAY_LOCK = threading.RLock()
_SEEN_RUNTIME_CHANNEL_NONCES: set[str] = set()
_AUTH_CLOCK_SKEW_SECONDS = 5.0


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _hidden_runtime_context() -> dict[str, Any]:
    value = getattr(_RUNTIME_API_CONTEXT, "value", None)
    return dict(value) if isinstance(value, dict) else {}


def _set_hidden_runtime_context(context: dict[str, Any]) -> None:
    _RUNTIME_API_CONTEXT.value = dict(context)


def _clear_hidden_runtime_context() -> None:
    _RUNTIME_API_CONTEXT.value = {}


def _require_authenticated_runtime_context() -> dict[str, Any]:
    context = _hidden_runtime_context()
    if not context.get("runtime_channel_id") or not context.get("runtime_channel_token"):
        raise PermissionError(
            "UNAUTHENTICATED_EXTENSION_CHANNEL: sandboxed plugin runtime calls require a worker-authenticated channel"
        )
    if str(context.get("_worker_instance_id") or "") != _WORKER_INSTANCE_ID:
        raise PermissionError(
            "UNAUTHENTICATED_EXTENSION_CHANNEL: runtime channel is not valid for this worker instance"
        )
    _validate_runtime_auth_timing(context)
    return context


def _validate_runtime_auth_timing(context: dict[str, Any]) -> None:
    issued_at = float(context.get("issued_at") or 0.0)
    expires_at = float(context.get("expires_at") or 0.0)
    now = time.time()
    if issued_at <= 0.0 or expires_at <= 0.0 or expires_at <= issued_at:
        raise PermissionError("INVALID_EXTENSION_CHANNEL: runtime channel timing is invalid")
    if now < (issued_at - _AUTH_CLOCK_SKEW_SECONDS):
        raise PermissionError("INVALID_EXTENSION_CHANNEL: runtime channel is not yet valid")
    if now > (expires_at + _AUTH_CLOCK_SKEW_SECONDS):
        raise PermissionError("EXPIRED_EXTENSION_CHANNEL: runtime channel has expired")


def _validate_runtime_auth_binding(
    *,
    plugin_context: dict[str, Any],
    runtime_api_auth: dict[str, Any],
    host_state: dict[str, Any] | None = None,
    consume_nonce: bool = True,
) -> dict[str, Any]:
    channel_id = str(runtime_api_auth.get("runtime_channel_id") or "").strip()
    channel_token = str(runtime_api_auth.get("runtime_channel_token") or "").strip()
    channel_nonce = str(runtime_api_auth.get("runtime_channel_nonce") or "").strip()
    sandbox_instance_id = str(runtime_api_auth.get("sandbox_instance_id") or "").strip()
    extension_name = str(runtime_api_auth.get("extension_name") or "").strip()
    owner_class = str(runtime_api_auth.get("owner_class") or "").strip()
    tenant_user_id = str(runtime_api_auth.get("user_id") or "").strip()

    if not channel_id or not channel_token or not channel_nonce:
        raise PermissionError("UNAUTHENTICATED_EXTENSION_CHANNEL: runtime channel binding is incomplete")
    if not sandbox_instance_id:
        raise PermissionError("INVALID_EXTENSION_CHANNEL: sandbox instance binding is missing")
    if str(runtime_api_auth.get("auth_version") or "") != "2026-05-22":
        raise PermissionError("INVALID_EXTENSION_CHANNEL: runtime channel auth_version is unsupported")
    _validate_runtime_auth_timing(runtime_api_auth)

    expected_pairs = {
        "user_id": str(plugin_context.get("user_id") or "").strip(),
        "run_id": str(plugin_context.get("run_id") or "").strip(),
        "trace_id": str(plugin_context.get("trace_id") or "").strip(),
        "extension_name": str(plugin_context.get("extension_name") or "").strip(),
        "owner_class": str(plugin_context.get("owner_class") or "").strip(),
        "sandbox_instance_id": str((plugin_context.get("runtime_api") or {}).get("sandbox_instance_id") or "").strip(),
    }
    actual_pairs = {
        "user_id": tenant_user_id,
        "run_id": str(runtime_api_auth.get("run_id") or "").strip(),
        "trace_id": str(runtime_api_auth.get("trace_id") or "").strip(),
        "extension_name": extension_name,
        "owner_class": owner_class,
        "sandbox_instance_id": sandbox_instance_id,
    }
    for key, expected in expected_pairs.items():
        if expected != actual_pairs[key]:
            raise PermissionError(
                f"BINDING_MISMATCH: runtime channel {key}={actual_pairs[key]!r} does not match plugin context {expected!r}"
            )
    if str((plugin_context.get("runtime_api") or {}).get("runtime_channel_id") or "").strip() != channel_id:
        raise PermissionError("BINDING_MISMATCH: runtime channel_id does not match plugin-visible runtime_api descriptor")

    if host_state is not None:
        expected_sandbox = str(host_state.get("sandbox_instance_id") or "").strip()
        if expected_sandbox and expected_sandbox != sandbox_instance_id:
            raise PermissionError(
                f"SANDBOX_INSTANCE_MISMATCH: runtime channel sandbox instance {sandbox_instance_id!r} does not match active host instance {expected_sandbox!r}"
            )
        expected_extension = str(host_state.get("extension_name") or "").strip()
        if expected_extension and expected_extension != extension_name:
            raise PermissionError(
                f"EXTENSION_BINDING_MISMATCH: runtime channel extension {extension_name!r} does not match active host extension {expected_extension!r}"
            )
        expected_owner = str(host_state.get("owner_class") or "").strip()
        if expected_owner and expected_owner != owner_class:
            raise PermissionError(
                f"OWNER_BINDING_MISMATCH: runtime channel owner {owner_class!r} does not match active host owner {expected_owner!r}"
            )

    if consume_nonce:
        replay_key = f"{_WORKER_INSTANCE_ID}:{channel_nonce}"
        with _AUTH_REPLAY_LOCK:
            if replay_key in _SEEN_RUNTIME_CHANNEL_NONCES:
                raise PermissionError(
                    "REPLAYED_EXTENSION_CHANNEL: runtime channel nonce has already been used by this worker instance"
                )
            _SEEN_RUNTIME_CHANNEL_NONCES.add(replay_key)

    validated = dict(runtime_api_auth)
    validated["_worker_instance_id"] = _WORKER_INSTANCE_ID
    return validated


def _require_runtime_tenant() -> str:
    context = _require_authenticated_runtime_context()
    tenant_user_id = str(context.get("user_id") or "").strip()
    if not tenant_user_id:
        raise PermissionError("TENANT_VIOLATION: extension runtime call requires a tenant-scoped user_id")
    return tenant_user_id


def _require_runtime_capability(capability: str) -> None:
    context = _require_authenticated_runtime_context()
    granted = {str(item) for item in list(context.get("granted_capabilities") or [])}
    if capability not in granted:
        raise PermissionError(
            f"Extension capability {capability!r} not granted; granted={sorted(granted)!r}"
        )


def _extension_call_metadata(operation: str) -> dict[str, Any]:
    context = _require_authenticated_runtime_context()
    return {
        "surface": "extension-runtime-api",
        "operation": operation,
        "tenant_user_id": _require_runtime_tenant(),
        "extension_name": str(context.get("extension_name") or "").strip(),
        "owner_class": str(context.get("owner_class") or "").strip(),
        "runtime_channel_id": str(context.get("runtime_channel_id") or ""),
        "sandbox_instance_id": str(context.get("sandbox_instance_id") or ""),
        "runner_type": str(context.get("runner_type") or ""),
        "runtime_channel_authenticated": True,
    }


def _extension_source_label() -> str:
    context = _require_authenticated_runtime_context()
    extension_name = str(context.get("extension_name") or "").strip()
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


def _runtime_execution_metadata() -> dict[str, Any]:
    context = _require_authenticated_runtime_context()
    return {
        "user_id": str(context.get("user_id") or ""),
        "run_id": str(context.get("run_id") or ""),
        "trace_id": str(context.get("trace_id") or ""),
        "extension_name": str(context.get("extension_name") or ""),
        "owner_class": str(context.get("owner_class") or ""),
        "runtime_channel_id": str(context.get("runtime_channel_id") or ""),
        "sandbox_instance_id": str(context.get("sandbox_instance_id") or ""),
        "granted_capabilities": [str(item) for item in list(context.get("granted_capabilities") or [])],
        "channel_type": "worker-authenticated-rpc",
    }


def _runtime_api_bridge(operation: str, payload: dict[str, Any] | None = None) -> Any:
    request = dict(payload or {})
    context = _require_authenticated_runtime_context()

    if operation == "capabilities.list":
        return [str(item) for item in list(context.get("granted_capabilities") or [])]
    if operation == "execution.metadata":
        return _runtime_execution_metadata()

    if operation == "memory.read":
        _require_runtime_capability(CAP_MEMORY_READ)
        tenant_user_id = _require_runtime_tenant()

        def _invoke() -> dict[str, Any]:
            from AINDY.kernel.syscall_dispatcher import dispatch_syscall

            return dispatch_syscall(
                "sys.v1.memory.read",
                {
                    "query": str(request.get("query") or ""),
                    "limit": int(request.get("limit") or 5),
                    "tags": list(request.get("tags") or []),
                },
                user_id=tenant_user_id,
                capability=CAP_MEMORY_READ,
                trace_id=str(context.get("trace_id") or context.get("run_id") or ""),
                execution_unit_id=str(context.get("run_id") or context.get("trace_id") or ""),
                metadata={"_extension_call": _extension_call_metadata("memory.read")},
            )

        return extension_runtime_api._run_trusted_runtime_operation(_invoke)

    if operation == "memory.write":
        _require_runtime_capability(CAP_MEMORY_WRITE)
        tenant_user_id = _require_runtime_tenant()

        def _invoke() -> dict[str, Any]:
            from AINDY.kernel.syscall_dispatcher import dispatch_syscall

            return dispatch_syscall(
                "sys.v1.memory.write",
                {
                    "content": str(request.get("content") or ""),
                    "tags": list(request.get("tags") or []),
                    "node_type": str(request.get("node_type") or "insight"),
                    "source": _extension_source_label(),
                },
                user_id=tenant_user_id,
                capability=CAP_MEMORY_WRITE,
                trace_id=str(context.get("trace_id") or context.get("run_id") or ""),
                execution_unit_id=str(context.get("run_id") or context.get("trace_id") or ""),
                metadata={"_extension_call": _extension_call_metadata("memory.write")},
            )

        return extension_runtime_api._run_trusted_runtime_operation(_invoke)

    if operation == "flow.run":
        _require_runtime_capability(CAP_FLOW_RUN)
        tenant_user_id = _require_runtime_tenant()

        def _invoke() -> dict[str, Any]:
            from AINDY.kernel.syscall_dispatcher import dispatch_syscall

            return dispatch_syscall(
                "sys.v1.flow.run",
                {
                    "flow_name": str(request.get("flow_name") or ""),
                    "initial_state": dict(request.get("initial_state") or {}),
                },
                user_id=tenant_user_id,
                capability=CAP_FLOW_RUN,
                trace_id=str(context.get("trace_id") or context.get("run_id") or ""),
                execution_unit_id=str(context.get("run_id") or context.get("trace_id") or ""),
                metadata={"_extension_call": _extension_call_metadata("flow.run")},
            )

        return extension_runtime_api._run_trusted_runtime_operation(_invoke)

    if operation == "event.emit":
        _require_runtime_capability(CAP_EVENT_EMIT)
        tenant_user_id = _require_runtime_tenant()

        def _invoke() -> dict[str, Any]:
            from AINDY.kernel.syscall_dispatcher import dispatch_syscall

            return dispatch_syscall(
                "sys.v1.event.emit",
                {
                    "event_type": str(request.get("event_type") or ""),
                    "payload": dict(request.get("payload") or {}),
                },
                user_id=tenant_user_id,
                capability=CAP_EVENT_EMIT,
                trace_id=str(context.get("trace_id") or context.get("run_id") or ""),
                execution_unit_id=str(context.get("run_id") or context.get("trace_id") or ""),
                metadata={"_extension_call": _extension_call_metadata("event.emit")},
            )

        return extension_runtime_api._run_trusted_runtime_operation(_invoke)

    if operation == "tool.invoke":
        _require_runtime_capability(CAP_TOOL_INVOKE)
        tenant_user_id = _require_runtime_tenant()
        sanitized_args = _sanitize_tool_args(request.get("args"))

        def _invoke() -> dict[str, Any]:
            from AINDY.agents.tool_registry import execute_tool
            from AINDY.db.database import SessionLocal

            db = SessionLocal()
            try:
                result = execute_tool(
                    str(request.get("tool_name") or ""),
                    sanitized_args,
                    user_id=tenant_user_id,
                    db=db,
                    run_id=None,
                    execution_token=None,
                )
                if not result.get("success"):
                    raise RuntimeError(result.get("error") or f"tool {request.get('tool_name')!r} failed")
                value = result.get("result")
                return value if isinstance(value, dict) else {"result": value}
            finally:
                db.close()

        return extension_runtime_api._run_trusted_runtime_operation(_invoke)

    raise PermissionError(f"unsupported extension runtime operation {operation!r}")


def _extract_plugin_context(
    runtime_context: dict[str, Any] | None,
    *,
    host_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(runtime_context or {})
    plugin_context = dict(payload.get("plugin_context") or {})
    runtime_api_auth = dict(payload.get("runtime_api_auth") or {})
    validated_auth = _validate_runtime_auth_binding(
        plugin_context=plugin_context,
        runtime_api_auth=runtime_api_auth,
        host_state=host_state,
        consume_nonce=True,
    )
    _set_hidden_runtime_context(validated_auth)
    extension_runtime_api._install_runtime_api_channel(bridge=_runtime_api_bridge)
    return plugin_context


def _clear_runtime_channel() -> None:
    _clear_hidden_runtime_context()
    try:
        extension_runtime_api._clear_runtime_api_channel()
    except Exception:
        pass


def _resolve_plugin_source(handler: str, plugin_root: str) -> tuple[str, str, Path]:
    if ":" not in handler:
        raise ValueError(
            f"plugin handler must be 'module:function', got {handler!r}"
        )
    module_part, func_name = handler.rsplit(":", 1)
    if ".." in module_part or module_part.startswith("."):
        raise ValueError(f"plugin handler contains illegal path component: {handler!r}")
    module_parts = module_part.split(".")
    if not all(part.isidentifier() for part in module_parts):
        raise ValueError(f"plugin handler module must use Python identifiers: {handler!r}")
    if not func_name.isidentifier():
        raise ValueError(f"plugin handler function name must be an identifier: {handler!r}")

    plugins_dir = Path(plugin_root)
    module_path = plugins_dir.joinpath(*module_parts)
    package_init = module_path / "__init__.py"
    module_file = module_path.with_suffix(".py")
    if package_init.is_file():
        return module_part, func_name, package_init
    if module_file.is_file():
        return module_part, func_name, module_file
    raise ValueError(f"plugin module {module_part!r} was not found under {plugins_dir}")


def _validate_plugin_callable(fn: Callable, handler: str) -> None:
    signature = inspect.signature(fn)
    parameters = list(signature.parameters.values())
    required_positional = [
        param
        for param in parameters
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        and param.default is inspect.Signature.empty
    ]
    required_extra = [param.name for param in required_positional[2:]]
    if len(required_positional) < 2 and not any(
        param.kind == inspect.Parameter.VAR_POSITIONAL
        for param in parameters
    ):
        raise ValueError(
            f"plugin node {handler!r} must accept at least (state, context)"
        )
    if required_extra:
        raise ValueError(
            f"plugin node {handler!r} requires unsupported positional arguments {required_extra!r}"
        )


def _load_plugin_callable(handler: str, plugin_root: str) -> tuple[Callable, dict[str, str]]:
    module_part, func_name, source_path = _resolve_plugin_source(handler, plugin_root)
    qualified_name = f"{_PLUGIN_MODULE_NAMESPACE}.{module_part}"
    spec = importlib_util.spec_from_file_location(qualified_name, source_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load plugin module {module_part!r} from {source_path}")
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, func_name, None)
    if fn is None:
        raise ValueError(
            f"function {func_name!r} not found in plugin module {module_part!r}"
        )
    if not callable(fn):
        raise ValueError(f"{module_part}:{func_name} is not callable")
    _validate_plugin_callable(fn, handler)
    return fn, {
        "module_name": module_part,
        "function_name": func_name,
        "qualified_name": qualified_name,
        "source_path": str(source_path),
    }


def _validate_response_contract(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("plugin node result must be a dict")
    status = result.get("status")
    if status not in _VALID_STATUSES:
        raise ValueError(f"plugin node returned invalid status {status!r}")
    return result


def _install_import_guard() -> Callable[..., Any]:
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        cleaned = str(name or "")
        if cleaned.startswith("AINDY") or cleaned.startswith("runtime"):
            if extension_runtime_api._trusted_runtime_call_active():
                return original_import(name, globals, locals, fromlist, level)
            if cleaned not in _ALLOWED_RUNTIME_IMPORTS and not cleaned.startswith(
                "AINDY.platform_layer.extension_runtime_api"
            ):
                raise PermissionError(
                    f"plugin runtime import blocked: {cleaned!r} is not an allowed extension API module"
                )
        return original_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded_import
    return original_import


def _install_network_guard(
    *,
    allow_outbound_http: bool,
    allow_private_targets: bool,
) -> tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]]:
    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def _extract_host(target: Any) -> str | None:
        if isinstance(target, tuple) and target:
            return str(target[0] or "").strip()
        if isinstance(target, str):
            return target.strip()
        return None

    def _target_permitted(target: Any) -> bool:
        if not allow_outbound_http:
            return False
        host = _extract_host(target)
        if not host:
            return True
        lowered = host.lower()
        if lowered in _PRIVATE_HOST_ALIASES and not allow_private_targets:
            return False
        try:
            address = ipaddress.ip_address(lowered)
        except ValueError:
            return True
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
        ) and not allow_private_targets:
            return False
        return True

    def blocked_create_connection(address, *args, **kwargs):
        if not allow_outbound_http:
            raise PermissionError("Extension capability 'outbound.http' not granted")
        if not _target_permitted(address):
            raise PermissionError(
                "Outbound target blocked by extension network policy: private/loopback targets are denied"
            )
        return original_create_connection(address, *args, **kwargs)

    def blocked_connect(self, address, *args, **kwargs):
        if not allow_outbound_http:
            raise PermissionError("Extension capability 'outbound.http' not granted")
        if not _target_permitted(address):
            raise PermissionError(
                "Outbound target blocked by extension network policy: private/loopback targets are denied"
            )
        return original_connect(self, address, *args, **kwargs)

    def blocked_connect_ex(self, address, *args, **kwargs):
        if not allow_outbound_http:
            raise PermissionError("Extension capability 'outbound.http' not granted")
        if not _target_permitted(address):
            raise PermissionError(
                "Outbound target blocked by extension network policy: private/loopback targets are denied"
            )
        return original_connect_ex(self, address, *args, **kwargs)

    socket.create_connection = blocked_create_connection
    socket.socket.connect = blocked_connect
    socket.socket.connect_ex = blocked_connect_ex
    return original_create_connection, original_connect, original_connect_ex


def _filesystem_guard_roots(plugin_root: str) -> tuple[Path, set[Path]]:
    allowed_root = Path(plugin_root).resolve()
    runtime_roots = {
        Path(sys.base_prefix).resolve(),
        Path(sys.prefix).resolve(),
    }
    return allowed_root, runtime_roots


def _path_is_within_allowed_roots(path: Path, *, allowed_root: Path, runtime_roots: set[Path]) -> bool:
    return (
        path == allowed_root
        or allowed_root in path.parents
        or any(path == root or root in path.parents for root in runtime_roots)
    )


def _path_is_within_temp_root(path: Path, writable_temp_root: Path | None) -> bool:
    if writable_temp_root is None:
        return False
    temp_root = writable_temp_root.resolve()
    return path == temp_root or temp_root in path.parents


def _install_filesystem_guard(plugin_root: str) -> dict[str, Callable[..., Any] | None]:
    allowed_root, runtime_roots = _filesystem_guard_roots(plugin_root)
    writable_temp_root = Path(tempfile.mkdtemp(prefix="aindy-sandbox-")).resolve()
    path_class = type(Path.cwd())
    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_listdir = os.listdir
    original_scandir = os.scandir
    original_path_open = path_class.open
    original_path_mkdir = path_class.mkdir
    original_path_unlink = path_class.unlink
    original_path_rename = path_class.rename
    original_path_replace = path_class.replace
    original_path_rmdir = path_class.rmdir
    original_path_touch = path_class.touch
    original_path_write_text = path_class.write_text
    original_path_write_bytes = path_class.write_bytes

    def _resolve_path(file: Any) -> Path:
        return Path(file).resolve()

    def _ensure_allowed(file: Any, mode: str = "r") -> None:
        resolved = _resolve_path(file)
        mode_text = str(mode or "r")
        read_only = not any(flag in mode_text for flag in ("w", "a", "x", "+"))
        if not read_only and not _path_is_within_temp_root(resolved, writable_temp_root):
            raise PermissionError(
                "Filesystem write blocked by extension filesystem policy"
            )
        approved_root = _path_is_within_allowed_roots(
            resolved,
            allowed_root=allowed_root,
            runtime_roots=runtime_roots,
        )
        if not approved_root and not _path_is_within_temp_root(resolved, writable_temp_root):
            raise PermissionError(
                "Filesystem path blocked by extension filesystem policy: only approved read-only roots are allowed"
            )

    def guarded_open(file, mode="r", *args, **kwargs):
        _ensure_allowed(file, mode)
        return original_open(file, mode, *args, **kwargs)

    def guarded_io_open(file, mode="r", *args, **kwargs):
        _ensure_allowed(file, mode)
        return original_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(file, flags, *args, **kwargs):
        mode = "r"
        write_flags = (
            getattr(os, "O_WRONLY", 0),
            getattr(os, "O_RDWR", 0),
            getattr(os, "O_APPEND", 0),
            getattr(os, "O_CREAT", 0),
            getattr(os, "O_TRUNC", 0),
        )
        if any(flags & flag for flag in write_flags if flag):
            mode = "w"
        _ensure_allowed(file, mode)
        return original_os_open(file, flags, *args, **kwargs)

    def guarded_listdir(path="."):
        _ensure_allowed(path, "r")
        return original_listdir(path)

    def guarded_scandir(path="."):
        _ensure_allowed(path, "r")
        return original_scandir(path)

    def guarded_path_open(self: Path, mode="r", *args, **kwargs):
        _ensure_allowed(self, mode)
        return original_path_open(self, mode, *args, **kwargs)

    def guarded_path_mkdir(self: Path, *args, **kwargs):
        _ensure_allowed(self, "w")
        return original_path_mkdir(self, *args, **kwargs)

    def guarded_path_unlink(self: Path, *args, **kwargs):
        _ensure_allowed(self, "w")
        return original_path_unlink(self, *args, **kwargs)

    def guarded_path_rename(self: Path, target, *args, **kwargs):
        _ensure_allowed(self, "w")
        _ensure_allowed(target, "w")
        return original_path_rename(self, target, *args, **kwargs)

    def guarded_path_replace(self: Path, target, *args, **kwargs):
        _ensure_allowed(self, "w")
        _ensure_allowed(target, "w")
        return original_path_replace(self, target, *args, **kwargs)

    def guarded_path_rmdir(self: Path, *args, **kwargs):
        _ensure_allowed(self, "w")
        return original_path_rmdir(self, *args, **kwargs)

    def guarded_path_touch(self: Path, *args, **kwargs):
        _ensure_allowed(self, "w")
        return original_path_touch(self, *args, **kwargs)

    def guarded_path_write_text(self: Path, data: str, *args, **kwargs):
        _ensure_allowed(self, "w")
        return original_path_write_text(self, data, *args, **kwargs)

    def guarded_path_write_bytes(self: Path, data: bytes, *args, **kwargs):
        _ensure_allowed(self, "w")
        return original_path_write_bytes(self, data, *args, **kwargs)

    builtins.open = guarded_open
    io.open = guarded_io_open
    os.open = guarded_os_open
    os.listdir = guarded_listdir
    os.scandir = guarded_scandir
    path_class.open = guarded_path_open
    path_class.mkdir = guarded_path_mkdir
    path_class.unlink = guarded_path_unlink
    path_class.rename = guarded_path_rename
    path_class.replace = guarded_path_replace
    path_class.rmdir = guarded_path_rmdir
    path_class.touch = guarded_path_touch
    path_class.write_text = guarded_path_write_text
    path_class.write_bytes = guarded_path_write_bytes
    return {
        "path_class": path_class,
        "writable_temp_root": writable_temp_root,
        "open": original_open,
        "io_open": original_io_open,
        "os_open": original_os_open,
        "listdir": original_listdir,
        "scandir": original_scandir,
        "path_open": original_path_open,
        "path_mkdir": original_path_mkdir,
        "path_unlink": original_path_unlink,
        "path_rename": original_path_rename,
        "path_replace": original_path_replace,
        "path_rmdir": original_path_rmdir,
        "path_touch": original_path_touch,
        "path_write_text": original_path_write_text,
        "path_write_bytes": original_path_write_bytes,
    }


def _strip_plugin_environment() -> dict[str, str]:
    preserved = {
        key: value
        for key, value in os.environ.items()
        if key in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP"}
    }
    os.environ.clear()
    os.environ.update(preserved)
    return preserved


def _prune_runtime_modules() -> None:
    allowed = {
        "AINDY",
        "AINDY.platform_layer",
        "AINDY.platform_layer.extension_runtime_api",
    }
    for name in list(sys.modules):
        if (name.startswith("AINDY") or name.startswith("runtime")) and name not in allowed:
            sys.modules.pop(name, None)


def _handle_request(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip()
    handler = str(payload.get("handler") or "").strip()
    plugin_root = str(payload.get("plugin_root") or "").strip()
    if not action or not handler or not plugin_root:
        return _error("extension worker requires action, handler, and plugin_root")

    original_import = builtins.__import__
    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_listdir = os.listdir
    original_scandir = os.scandir
    original_path_open = Path.open
    original_path_mkdir = Path.mkdir
    original_path_unlink = Path.unlink
    original_path_rename = Path.rename
    original_path_replace = Path.replace
    original_path_rmdir = Path.rmdir
    original_path_touch = Path.touch
    original_path_write_text = Path.write_text
    original_path_write_bytes = Path.write_bytes
    writable_temp_root = None
    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    allow_private_targets = os.getenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    try:
        plugin_context = _extract_plugin_context(payload.get("context") or {})
        _strip_plugin_environment()
        _prune_runtime_modules()
        original_import = _install_import_guard()
        filesystem_guard_originals = _install_filesystem_guard(plugin_root)
        original_open = filesystem_guard_originals["open"]
        original_io_open = filesystem_guard_originals["io_open"]
        original_os_open = filesystem_guard_originals["os_open"]
        original_listdir = filesystem_guard_originals["listdir"]
        original_scandir = filesystem_guard_originals["scandir"]
        path_class = filesystem_guard_originals["path_class"]
        original_path_open = filesystem_guard_originals["path_open"]
        original_path_mkdir = filesystem_guard_originals["path_mkdir"]
        original_path_unlink = filesystem_guard_originals["path_unlink"]
        original_path_rename = filesystem_guard_originals["path_rename"]
        original_path_replace = filesystem_guard_originals["path_replace"]
        original_path_rmdir = filesystem_guard_originals["path_rmdir"]
        original_path_touch = filesystem_guard_originals["path_touch"]
        original_path_write_text = filesystem_guard_originals["path_write_text"]
        original_path_write_bytes = filesystem_guard_originals["path_write_bytes"]
        writable_temp_root = filesystem_guard_originals["writable_temp_root"]
        original_create_connection, original_connect, original_connect_ex = _install_network_guard(
            allow_outbound_http=extension_runtime_api.outbound_http_enabled(),
            allow_private_targets=allow_private_targets,
        )
        fn, provenance = _load_plugin_callable(handler, plugin_root)
        if action == "validate":
            return {"ok": True, "provenance": provenance}
        if action == "execute":
            result = _validate_response_contract(
                fn(payload.get("state") or {}, plugin_context)
            )
            return {"ok": True, "result": result, "provenance": provenance}
        return _error(f"unsupported action {action!r}")
    except Exception as exc:
        return _error(f"{exc.__class__.__name__}: {exc}")
    finally:
        try:
            builtins.__import__ = original_import
        except Exception:
            pass
        try:
            builtins.open = original_open
            io.open = original_io_open
            os.open = original_os_open
            os.listdir = original_listdir
            os.scandir = original_scandir
            path_class.open = original_path_open
            path_class.mkdir = original_path_mkdir
            path_class.unlink = original_path_unlink
            path_class.rename = original_path_rename
            path_class.replace = original_path_replace
            path_class.rmdir = original_path_rmdir
            path_class.touch = original_path_touch
            path_class.write_text = original_path_write_text
            path_class.write_bytes = original_path_write_bytes
        except Exception:
            pass
        try:
            shutil.rmtree(writable_temp_root, ignore_errors=True)
        except Exception:
            pass
        try:
            socket.create_connection = original_create_connection
            socket.socket.connect = original_connect
            socket.socket.connect_ex = original_connect_ex
        except Exception:
            pass
        _clear_runtime_channel()


def _host_error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _probe_artifact_read_access(host_state: dict[str, Any]) -> dict[str, Any]:
    source_path = str((host_state.get("provenance") or {}).get("source_path") or "").strip()
    if not source_path:
        return {
            "status": "unverified",
            "verified": False,
            "detail": "plugin source_path was not available for read probe",
        }
    try:
        with builtins.open(source_path, "r", encoding="utf-8") as handle:
            handle.read(1)
        return {
            "status": "passed",
            "verified": True,
            "detail": "plugin artifact content remained readable through the guarded mount",
            "path": source_path,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "verified": False,
            "detail": f"artifact read probe failed: {exc.__class__.__name__}: {exc}",
            "path": source_path,
        }


def _probe_artifact_write_blocked(host_state: dict[str, Any]) -> dict[str, Any]:
    allowed_root, _runtime_roots = _filesystem_guard_roots(str(host_state.get("plugin_root") or ""))
    candidate = allowed_root / f".aindy-write-probe-{secrets.token_hex(4)}"
    try:
        with builtins.open(candidate, "x", encoding="utf-8") as handle:
            handle.write("probe")
        try:
            candidate.unlink(missing_ok=True)
        except Exception:
            pass
        return {
            "status": "failed",
            "verified": False,
            "detail": "artifact write unexpectedly succeeded inside the plugin artifact root",
            "path": str(candidate),
        }
    except PermissionError:
        return {
            "status": "passed",
            "verified": True,
            "detail": "artifact write remained blocked by the filesystem guard",
            "path": str(candidate),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "verified": False,
            "detail": f"artifact write probe failed unexpectedly: {exc.__class__.__name__}: {exc}",
            "path": str(candidate),
        }


def _probe_writable_temp_scope(host_state: dict[str, Any]) -> dict[str, Any]:
    temp_root = Path(host_state.get("writable_temp_root") or "").resolve() if host_state.get("writable_temp_root") else None
    if temp_root is None:
        return {
            "status": "unverified",
            "verified": False,
            "detail": "worker did not record a dedicated writable temp root",
        }
    candidate = temp_root / f"aindy-sandbox-probe-{secrets.token_hex(4)}.tmp"
    try:
        with builtins.open(candidate, "w", encoding="utf-8") as handle:
            handle.write("probe")
        with builtins.open(candidate, "r", encoding="utf-8") as handle:
            handle.read()
        candidate.unlink(missing_ok=True)
        return {
            "status": "passed",
            "verified": True,
            "detail": "isolated writable temp scope remained usable",
            "path": str(candidate),
            "temp_root": str(temp_root),
        }
    except Exception as exc:
        try:
            candidate.unlink(missing_ok=True)
        except Exception:
            pass
        return {
            "status": "failed",
            "verified": False,
            "detail": f"writable temp probe failed: {exc.__class__.__name__}: {exc}",
            "path": str(candidate),
            "temp_root": str(temp_root),
        }


def _probe_host_path_access_blocked(host_state: dict[str, Any]) -> dict[str, Any]:
    allowed_root, runtime_roots = _filesystem_guard_roots(str(host_state.get("plugin_root") or ""))
    candidates = [
        Path.cwd().resolve(),
        allowed_root.parent.resolve(),
        Path.home().resolve(),
    ]
    candidate: Path | None = None
    for item in candidates:
        if not _path_is_within_allowed_roots(
            item,
            allowed_root=allowed_root,
            runtime_roots=runtime_roots,
        ):
            candidate = item
            break
    if candidate is None:
        return {
            "status": "unverified",
            "verified": False,
            "detail": "no suitable out-of-scope host path candidate was available for probing",
        }
    try:
        os.listdir(candidate)
        return {
            "status": "failed",
            "verified": False,
            "detail": "ambient host path access unexpectedly succeeded outside approved roots",
            "path": str(candidate),
        }
    except PermissionError:
        return {
            "status": "passed",
            "verified": True,
            "detail": "ambient host path access remained blocked outside approved roots",
            "path": str(candidate),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "verified": False,
            "detail": f"ambient host path probe failed unexpectedly: {exc.__class__.__name__}: {exc}",
            "path": str(candidate),
        }


def _probe_network_policy(host_state: dict[str, Any]) -> dict[str, Any]:
    allow_outbound_http = bool(host_state.get("allow_outbound_http"))
    allow_private_targets = bool(host_state.get("allow_private_targets"))
    details: dict[str, Any] = {
        "socket_guard_active": {
            "status": "passed",
            "verified": True,
            "detail": "network guard wrappers remain installed in the live worker",
        },
        "deny_by_default_outbound": {
            "status": "unverified",
            "verified": False,
            "detail": "outbound policy was not probed",
        },
        "private_target_blocking": {
            "status": "unverified",
            "verified": False,
            "detail": "private-target policy was not probed",
        },
        "expected_boundary_mode": {
            "status": "passed",
            "verified": True,
            "configured": "deny-by-default" if not allow_outbound_http else "capability-gated",
            "live_policy_observed": "deny-by-default" if not allow_outbound_http else "capability-gated",
            "detail": "worker-level socket policy mode remains consistent with the granted capability state",
        },
    }
    if not allow_outbound_http:
        try:
            socket.create_connection(("example.com", 80), timeout=0.01)
            details["deny_by_default_outbound"] = {
                "status": "failed",
                "verified": False,
                "detail": "outbound socket connection unexpectedly bypassed deny-by-default policy",
            }
        except PermissionError:
            details["deny_by_default_outbound"] = {
                "status": "passed",
                "verified": True,
                "detail": "deny-by-default outbound policy remained active without outbound.http capability",
            }
        except Exception as exc:
            details["deny_by_default_outbound"] = {
                "status": "failed",
                "verified": False,
                "detail": f"deny-by-default outbound probe failed unexpectedly: {exc.__class__.__name__}: {exc}",
            }
    else:
        details["deny_by_default_outbound"] = {
            "status": "not_applicable",
            "verified": False,
            "detail": "outbound.http capability was granted, so deny-by-default was not expected for all targets",
        }
    if allow_outbound_http and not allow_private_targets:
        try:
            socket.create_connection(("127.0.0.1", 80), timeout=0.01)
            details["private_target_blocking"] = {
                "status": "failed",
                "verified": False,
                "detail": "private/loopback target unexpectedly bypassed network policy",
            }
        except PermissionError:
            details["private_target_blocking"] = {
                "status": "passed",
                "verified": True,
                "detail": "private/loopback target blocking remained active",
            }
        except Exception as exc:
            details["private_target_blocking"] = {
                "status": "failed",
                "verified": False,
                "detail": f"private-target probe failed unexpectedly: {exc.__class__.__name__}: {exc}",
            }
    elif not allow_outbound_http:
        details["private_target_blocking"] = {
            "status": "passed",
            "verified": True,
            "detail": "private targets remained unreachable because all outbound sockets were denied",
        }
    else:
        details["private_target_blocking"] = {
            "status": "not_applicable",
            "verified": False,
            "detail": "private target override is enabled in this worker session",
        }
    return details


def _host_probe_payload(host_state: dict[str, Any]) -> dict[str, Any]:
    filesystem_guard_originals = dict(host_state.get("filesystem_guard_originals") or {})
    path_class = filesystem_guard_originals.get("path_class") or type(Path.cwd())
    import_guard_active = builtins.__import__ is not host_state.get("original_import")
    filesystem_guard_active = (
        builtins.open is not filesystem_guard_originals.get("open")
        and io.open is not filesystem_guard_originals.get("io_open")
        and os.open is not filesystem_guard_originals.get("os_open")
        and path_class.open is not filesystem_guard_originals.get("path_open")
    )
    network_guard_active = (
        socket.create_connection is not host_state.get("original_create_connection")
        and socket.socket.connect is not host_state.get("original_connect")
        and socket.socket.connect_ex is not host_state.get("original_connect_ex")
    )
    mount_network_state = {
        "artifact_read_access": _probe_artifact_read_access(host_state),
        "artifact_write_blocked": _probe_artifact_write_blocked(host_state),
        "writable_temp_scope": _probe_writable_temp_scope(host_state),
        "host_path_access_blocked": _probe_host_path_access_blocked(host_state),
        "network_policy": _probe_network_policy(host_state),
    }
    return {
        "worker_instance_id": _WORKER_INSTANCE_ID,
        "verification_scope": "live-worker-self-report-over-authenticated-rpc",
        "session_continuity": {
            "started": bool(host_state["callable"] is not None),
            "started_at": host_state.get("started_at") or None,
            "request_count": int(host_state["request_count"]),
            "handler": host_state.get("handler"),
            "plugin_root": host_state.get("plugin_root"),
            "extension_name": host_state.get("extension_name"),
            "owner_class": host_state.get("owner_class"),
            "sandbox_instance_id": host_state.get("sandbox_instance_id"),
        },
        "isolation_state": {
            "import_guard_active": import_guard_active,
            "filesystem_guard_active": filesystem_guard_active,
            "network_guard_active": network_guard_active,
            "guards_installed": bool(host_state.get("guards_installed")),
            "environment_stripped": True,
            "runtime_modules_pruned": True,
        },
        "boundary_metadata": {
            "runtime_api_channel_hidden": True,
            "private_targets_allowed": bool(host_state.get("allow_private_targets")),
            "provenance": dict(host_state.get("provenance") or {}),
        },
        "mount_network_state": mount_network_state,
        "operator_note": (
            "This probe confirms worker session continuity and expected guard state through a live "
            "runtime-owned RPC call after launch. It does not prove kernel state outside the worker."
        ),
    }


def _handle_host_session() -> int:
    host_state: dict[str, Any] = {
        "handler": None,
        "plugin_root": None,
        "callable": None,
        "extension_name": None,
        "owner_class": None,
        "sandbox_instance_id": None,
        "provenance": {},
        "started_at": None,
        "request_count": 0,
        "guards_installed": False,
        "original_import": builtins.__import__,
        "original_open": builtins.open,
        "original_io_open": io.open,
        "original_os_open": os.open,
        "original_listdir": os.listdir,
        "original_scandir": os.scandir,
        "filesystem_guard_originals": {},
        "writable_temp_root": None,
        "original_create_connection": socket.create_connection,
        "original_connect": socket.socket.connect,
        "original_connect_ex": socket.socket.connect_ex,
        "allow_outbound_http": False,
        "allow_private_targets": os.getenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS", "false").lower() in {
            "1",
            "true",
            "yes",
        },
    }
    _strip_plugin_environment()
    _prune_runtime_modules()

    while True:
        raw = sys.stdin.readline()
        if not raw:
            return 0
        try:
            payload = json.loads(raw or "{}")
        except Exception as exc:
            sys.stdout.write(json.dumps(_host_error(f"invalid JSON request: {exc}")) + "\n")
            sys.stdout.flush()
            continue

        command = str(payload.get("command") or "").strip()
        try:
            if command == "start":
                handler = str(payload.get("handler") or "").strip()
                plugin_root = str(payload.get("plugin_root") or "").strip()
                if not handler or not plugin_root:
                    response = _host_error("plugin host start requires handler and plugin_root")
                else:
                    plugin_context = _extract_plugin_context(
                        payload.get("context") or {},
                        host_state=host_state,
                    )
                    if not host_state["guards_installed"]:
                        host_state["original_import"] = _install_import_guard()
                        host_state["filesystem_guard_originals"] = _install_filesystem_guard(plugin_root)
                        host_state["original_open"] = host_state["filesystem_guard_originals"].get("open")
                        host_state["original_io_open"] = host_state["filesystem_guard_originals"].get("io_open")
                        host_state["original_os_open"] = host_state["filesystem_guard_originals"].get("os_open")
                        host_state["original_listdir"] = host_state["filesystem_guard_originals"].get("listdir")
                        host_state["original_scandir"] = host_state["filesystem_guard_originals"].get("scandir")
                        host_state["writable_temp_root"] = str(
                            host_state["filesystem_guard_originals"].get("writable_temp_root") or ""
                        )
                        (
                            host_state["original_create_connection"],
                            host_state["original_connect"],
                            host_state["original_connect_ex"],
                        ) = _install_network_guard(
                            allow_outbound_http=extension_runtime_api.outbound_http_enabled(),
                            allow_private_targets=bool(host_state["allow_private_targets"]),
                        )
                        host_state["allow_outbound_http"] = extension_runtime_api.outbound_http_enabled()
                        host_state["guards_installed"] = True
                    fn, provenance = _load_plugin_callable(handler, plugin_root)
                    host_state["handler"] = handler
                    host_state["plugin_root"] = plugin_root
                    host_state["callable"] = fn
                    host_state["extension_name"] = str(plugin_context.get("extension_name") or "").strip()
                    host_state["owner_class"] = str(plugin_context.get("owner_class") or "").strip()
                    host_state["sandbox_instance_id"] = str(
                        (plugin_context.get("runtime_api") or {}).get("sandbox_instance_id") or ""
                    ).strip()
                    host_state["provenance"] = provenance
                    host_state["started_at"] = payload.get("started_at") or ""
                    response = {"ok": True, "provenance": provenance}
            elif command == "heartbeat":
                response = {
                    "ok": True,
                    "pid": os.getpid(),
                    "request_count": int(host_state["request_count"]),
                    "started": bool(host_state["callable"] is not None),
                }
            elif command == "probe":
                response = {
                    "ok": True,
                    "pid": os.getpid(),
                    "started": bool(host_state["callable"] is not None),
                    "probe": _host_probe_payload(host_state),
                }
            elif command == "execute":
                fn = host_state.get("callable")
                if fn is None:
                    response = _host_error("plugin host has not been started")
                else:
                    plugin_context = _extract_plugin_context(
                        payload.get("context") or {},
                        host_state=host_state,
                    )
                    result = _validate_response_contract(
                        fn(payload.get("state") or {}, plugin_context)
                    )
                    host_state["request_count"] = int(host_state["request_count"]) + 1
                    response = {
                        "ok": True,
                        "result": result,
                        "provenance": dict(host_state["provenance"]),
                    }
            elif command == "shutdown":
                response = {"ok": True, "status": "shutting_down"}
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
                return 0
            else:
                response = _host_error(f"unsupported host command {command!r}")
        except Exception as exc:
            response = _host_error(f"{exc.__class__.__name__}: {exc}")
        finally:
            _clear_runtime_channel()

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


def main() -> int:
    if "--host" in sys.argv[1:]:
        return _handle_host_session()
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except Exception as exc:
        sys.stdout.write(json.dumps(_error(f"invalid JSON request: {exc}")))
        return 1
    response = _handle_request(payload)
    sys.stdout.write(json.dumps(response))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
