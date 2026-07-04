from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import uuid
from typing import Any, Optional

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

_STDLIB_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "nodus", "stdlib"))


class WorkerWaitSignal(Exception):
    def __init__(self, event_type: str) -> None:
        self.event_type = event_type
        super().__init__(f"nodus.wait:{event_type}")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


class DeferredMemoryBuiltins:
    def __init__(self, memory_context: dict[str, Any], user_id: str, execution_unit_id: str) -> None:
        self._memory_context = memory_context
        self._user_id = user_id
        self._execution_unit_id = execution_unit_id
        self._writes: list[dict[str, Any]] = []

    def recall(self, tags: Any = None, limit: int = 5, *_args: Any) -> list[dict[str, Any]]:
        nodes = list(self._memory_context.values()) if isinstance(self._memory_context, dict) else []
        if isinstance(tags, str):
            tags = [tags]
        if tags:
            tag_set = set(tags)
            nodes = [n for n in nodes if tag_set.intersection(set((n or {}).get("tags") or []))]
        return [_json_safe(n) for n in nodes[: max(1, int(limit or 1))]]

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not query:
            return []
        lowered = str(query).lower()
        nodes = list(self._memory_context.values()) if isinstance(self._memory_context, dict) else []
        matches = [n for n in nodes if lowered in str((n or {}).get("content") or "").lower()]
        return [_json_safe(n) for n in matches[: max(1, int(limit or 1))]]

    def write(
        self,
        content: str,
        tags: Any = None,
        node_type: str = "insight",
        significance: float = 0.5,
    ) -> dict[str, Any]:
        tags_list = [tags] if isinstance(tags, str) else list(tags or [])
        result = {
            "id": f"deferred-memory-{uuid.uuid4()}",
            "content": content,
            "tags": tags_list,
            "node_type": node_type,
            "significance": significance,
            "source": "nodus_script",
            "memory_type": "deferred",
        }
        self._writes.append(
            {
                "kind": "memory.write",
                "execution_unit_id": self._execution_unit_id,
                "user_id": self._user_id,
                "content": content,
                "tags": tags_list,
                "node_type": node_type,
                "significance": significance,
                "result": result,
                "args": [content, tags_list, node_type, significance],
            }
        )
        return result


def run_agent_tool(
    tool_name: str,
    args: Any,
    *,
    user_id: str,
    run_id: str,
    execution_token: Optional[dict],
    session_factory: Any = None,
) -> dict[str, Any]:
    """Execute an AINDY agent tool from inside a Nodus run (RTR-1 Phase 2a seam).

    This is the capability-enforced bridge from the Nodus VM to
    ``AINDY.agents.tool_registry.execute_tool``. It is registered as the
    ``call_tool(name, args)`` host function so a workflow step can run
    ``let r = call_tool("send_email", {to: "x"})``.

    Fail-closed: a scoped capability token is **required**; without one the call
    is refused (it never reaches the tool). With a token, ``execute_tool``
    enforces ``check_tool_capability`` (token validity/expiry/hash, granted
    tools, required capabilities ⊆ allowed). Returns the same
    ``{"success", "result", "error"}`` contract as ``execute_tool`` (with the
    result made JSON-safe so the Nodus script receives clean values).

    Note: the native ``action tool "x"`` workflow construct is NOT this seam — it
    lowers to nodus's built-in ``__action_tool`` (its own 4-tool stub, no
    capability enforcement) and cannot be overridden. Generated AINDY workflows
    must call ``call_tool(...)``.
    """
    if not execution_token:
        return {
            "success": False,
            "result": None,
            "error": "tool execution requires a capability token",
        }
    tool_args = dict(args) if isinstance(args, dict) else {}

    if session_factory is None:
        from AINDY.db.database import SessionLocal as session_factory

    from AINDY.agents.tool_registry import execute_tool

    db = session_factory()
    try:
        result = execute_tool(
            tool_name=str(tool_name),
            args=tool_args,
            user_id=user_id,
            db=db,
            run_id=run_id,
            execution_token=execution_token,
        )
    except Exception as exc:
        return {"success": False, "result": None, "error": str(exc)}
    finally:
        with contextlib.suppress(Exception):
            db.close()

    return {
        "success": bool(result.get("success")),
        "result": _json_safe(result.get("result")),
        "error": result.get("error"),
    }


def _remember_factory(memory: DeferredMemoryBuiltins) -> Any:
    def _remember(*args: Any) -> dict[str, Any]:
        content = str(args[0]) if args else ""
        tags = args[1] if len(args) > 1 else None
        node_type = str(args[2]) if len(args) > 2 else "insight"
        significance = float(args[3]) if len(args) > 3 else 0.5
        result = memory.write(content, tags, node_type, significance)
        memory._writes[-1]["kind"] = "remember"
        memory._writes[-1]["args"] = [content, tags, node_type, significance]
        return result

    return _remember


def main() -> int:
    raw = sys.stdin.read()
    payload = json.loads(raw or "{}")

    script = str(payload.get("script") or "")
    if "memory." in script:
        script = re.sub(r'(?m)^(\s*)import\s+"memory"\s*$', r'\1import "memory" as memory', script)
        script = re.sub(r"(?m)^(\s*)import\s+memory\s*$", r'\1import "memory" as memory', script)
    state = dict(payload.get("state") or {})
    memory_context = dict(payload.get("memory_context") or {})
    input_payload = dict(payload.get("input_payload") or {})
    ctx = dict(payload.get("context") or {})
    user_id = str(ctx.get("user_id") or "")
    execution_unit_id = str(ctx.get("execution_unit_id") or "")
    filename = str(ctx.get("filename") or f"<nodus:eu:{execution_unit_id}>")
    trace_id = str(ctx.get("trace_id") or execution_unit_id or "")
    # RTR-1 Phase 2a — tool-calling seam context.
    tool_run_id = str(ctx.get("run_id") or execution_unit_id or "")
    tool_execution_token = ctx.get("execution_token")
    if not isinstance(tool_execution_token, dict):
        tool_execution_token = None

    from nodus.runtime.embedding import NodusRuntime
    from AINDY.nodus.runtime.memory_bridge import AINDYMemoryBridge

    memory_deferral = DeferredMemoryBuiltins(memory_context, user_id, execution_unit_id)
    bridge = AINDYMemoryBridge(user_id=user_id)

    # Host functions registered with the VM.
    def _set_state(key: str, value: Any) -> None:
        state[key] = _json_safe(value)

    def _get_state(key: str) -> Any:
        return state.get(key)

    def _sys_dispatch(name: str, payload_arg: Any) -> Any:
        """Dispatch a Nodus sys() call through the AINDY syscall layer."""
        try:
            from AINDY.db.database import SessionLocal
            from AINDY.kernel.syscall_dispatcher import dispatch_syscall

            call_payload = dict(payload_arg) if isinstance(payload_arg, dict) else {}
            if "user_id" not in call_payload:
                call_payload["user_id"] = user_id

            db = SessionLocal()
            try:
                return dispatch_syscall(
                    name,
                    call_payload,
                    db=db,
                    user_id=user_id,
                )
            finally:
                db.close()
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
                "data": None,
                "syscall": name,
            }

    runtime = NodusRuntime(project_root=_STDLIB_DIR if os.path.isdir(_STDLIB_DIR) else None)
    def _call_tool(tool_name: Any, args: Any) -> Any:
        return run_agent_tool(
            tool_name,
            args,
            user_id=user_id,
            run_id=tool_run_id,
            execution_token=tool_execution_token,
        )

    def _is_retryable_error(error: Any) -> bool:
        """Host function for RTR-1 Phase 2d compiled agent workflows.

        Lets a compiled step's retry loop short-circuit on non-transient tool
        errors, mirroring AGENT_FLOW's ``is_retryable_error`` gate. Kept fail-open
        (retryable) if the classifier is unavailable, so retry budget still applies.
        """
        try:
            from AINDY.core.retry_policy import is_retryable_error

            return bool(is_retryable_error(None if error is None else str(error)))
        except Exception:
            return True

    runtime.register_function("set_state", _set_state, arity=2)
    runtime.register_function("get_state", _get_state, arity=1)
    runtime.register_function("sys", _sys_dispatch, arity=2)
    runtime.register_function("call_tool", _call_tool, arity=2)
    runtime.register_function("is_retryable_error", _is_retryable_error, arity=1)
    runtime.register_function("recall", bridge.recall, arity=3)
    runtime.register_function("remember", bridge.remember, arity=3)
    runtime.register_function("suggest", bridge.get_suggestions, arity=3)
    runtime.register_function("record_outcome", bridge.record_outcome, arity=2)
    runtime.register_function("share", bridge.share, arity=1)
    runtime.register_function("recall_from", bridge.recall_from, arity=4)
    runtime.register_function("recall_all", bridge.recall_all_agents, arity=3)
    runtime.register_function("recall_all_agents", bridge.recall_all_agents, arity=3)
    # stdlib memory.nd calls these private names — must be kept in sync with memory.nd exports.
    runtime.register_function("__memory_stdlib_recall_from", bridge.recall_from, arity=4)
    runtime.register_function("__memory_stdlib_recall_all", bridge.recall_all_agents, arity=3)
    runtime.register_function("__memory_stdlib_share", bridge.share, arity=1)

    def _runtime_emitted_events() -> list[dict[str, Any]]:
        _AINDY_INTERNAL = ("vm_", "runtime.", "nodus.")
        vm = runtime._get_active_vm()
        if vm is None:
            return []
        return [
            {
                "type": e.type,
                "event_type": e.type,
                "payload": e.data or {},
                "user_id": user_id,
                "execution_unit_id": execution_unit_id,
            }
            for e in vm.event_bus.events()
            if not any(e.type.startswith(p) for p in _AINDY_INTERNAL)
        ]

    data_globals = {
        "state": dict(state),
        "memory_context": memory_context,
        "input_payload": input_payload,
        "user_id": user_id,
        "execution_unit_id": execution_unit_id,
        "trace_id": trace_id,
    }

    stdout_buffer = io.StringIO()
    result_payload: dict[str, Any]
    max_execution_ms = int(payload.get("max_execution_ms") or 30_000)

    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stdout_buffer):
        try:
            raw_result = runtime.run_source(
                script,
                filename=filename,
                initial_globals=data_globals,
                timeout_ms=max_execution_ms,
                host_globals={"memory_bridge": bridge},
            )
            emitted_events = _runtime_emitted_events()

            ok = bool((raw_result or {}).get("ok", False))
            error = None if ok else str((raw_result or {}).get("error") or "Nodus execution failed")

            if state.get("nodus_wait_requested"):
                wait_for = str(state.get("nodus_wait_event_type") or "unknown")
                state.pop("nodus_wait_requested", None)
                result_payload = {
                    "status": "waiting",
                    "output_state": _json_safe(state),
                    "emitted_events": _json_safe(emitted_events),
                    "memory_writes": _json_safe(memory_deferral._writes),
                    "error": None,
                    "stdout_log": stdout_buffer.getvalue(),
                    "wait_for": wait_for,
                }
            else:
                result_payload = {
                    "status": "success" if ok else "failure",
                    "output_state": _json_safe(state),
                    "emitted_events": _json_safe(emitted_events),
                    "memory_writes": _json_safe(memory_deferral._writes),
                    "error": error,
                    "stdout_log": stdout_buffer.getvalue(),
                }
        except WorkerWaitSignal as exc:
            state.pop("nodus_wait_requested", None)
            result_payload = {
                "status": "waiting",
                "output_state": _json_safe(state),
                "emitted_events": _json_safe(_runtime_emitted_events()),
                "memory_writes": _json_safe(memory_deferral._writes),
                "error": None,
                "stdout_log": stdout_buffer.getvalue(),
                "wait_for": exc.event_type,
            }
        except Exception as exc:
            result_payload = {
                "status": "failure",
                "output_state": _json_safe(state),
                "emitted_events": _json_safe(_runtime_emitted_events()),
                "memory_writes": _json_safe(memory_deferral._writes),
                "error": str(exc),
                "stdout_log": stdout_buffer.getvalue(),
            }

    sys.stdout.write(json.dumps(result_payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
