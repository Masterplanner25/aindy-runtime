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


_STD_SYS_GUARD_MESSAGE = (
    "std:sys is not routed to the AINDY syscall dispatcher under aindy-runtime — the "
    'idiomatic `import "std:sys"` resolves to nodus\'s in-process, ephemeral syscall stub '
    "(no capability enforcement, quota, idempotency, or persistence). Use the bare "
    '`sys("<name>", <payload>)` builtin instead.'
)


def _install_std_sys_guard() -> bool:
    """NODUS-SYS-SURFACE-1 — fail loud on the idiomatic `std:sys` path.

    A `.nd` script has two name-disjoint syscall surfaces: the bare ``sys(name, payload)``
    builtin AINDY registers (routes to ``dispatch_syscall`` — kernel, capabilities,
    Postgres), and nodus's native ``syscall`` builtin reached via ``import "std:sys"``
    (routes to ``nodus.services.syscall_runtime.call_syscall``, a hardcoded 4-syscall
    in-process ephemeral stub). The native path cannot be aliased to ``_sys_dispatch``:
    ``register_function`` forbids overriding a builtin, and the VM resolves native builtins
    before host functions. So convert the silent wrong-backend into an immediate, clear
    error by replacing the underlying ``call_syscall`` (``builtin_syscall`` re-imports it
    per call, so the module-attribute swap takes effect). Never raises during setup.

    Returns True if the guard was installed.
    """
    try:
        import nodus.services.syscall_runtime as _sr

        def _guard(name: Any, payload: Any, *, vm: Any = None) -> Any:
            raise RuntimeError(f"{_STD_SYS_GUARD_MESSAGE} (attempted syscall: {name!r})")

        _sr.call_syscall = _guard
        return True
    except Exception:
        return False


def dispatch_worker_syscall(name: str, payload: Any, *, user_id: str) -> Any:
    """Dispatch a Nodus ``sys()`` call, ensuring app syscalls are registered first (FR-5b).

    App domains register their syscalls into the kernel ``SYSCALL_REGISTRY`` from each
    app's ``bootstrap()`` (via ``kernel.syscall_registry.register_syscall`` with a real
    capability + schema). ``load_plugins()`` runs that bootstrap, but in the Nodus worker
    subprocess ``load_plugins`` is only reached lazily by ``execute_tool`` — the
    ``call_tool`` seam. The ``sys()`` seam had **no** plugin-load entry point, so a
    workflow that used ``sys("sys.v1.<app syscall>", …)`` without a prior ``call_tool``
    dispatched against an unpopulated registry → ``"Unknown syscall"``.

    Loading the stack here (idempotent + memoized; lazy — only when ``sys()`` is actually
    used, so it doesn't tax tool-only or pure-script runs) registers app syscalls in this
    subprocess. Dispatch then flows through the normal capability-enforced pipeline: the app
    syscall keeps its declared capability (e.g. ``analytics.read``), which the worker's
    ``dispatch_syscall`` grants via ``_infer_dispatch_capability`` and the dispatcher
    enforces — so this closes the resolution gap without weakening enforcement.
    """
    try:
        from AINDY.agents.tool_registry import _ensure_tools_loaded
        from AINDY.db.database import SessionLocal
        from AINDY.kernel.syscall_dispatcher import dispatch_syscall

        _ensure_tools_loaded()
        call_payload = dict(payload) if isinstance(payload, dict) else {}
        call_payload.setdefault("user_id", user_id)
        db = SessionLocal()
        try:
            return dispatch_syscall(name, call_payload, db=db, user_id=user_id)
        finally:
            db.close()
    except Exception as exc:
        return {"status": "error", "error": str(exc), "data": None, "syscall": name}


def run_one(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one Nodus request payload and return the result dict.

    Extracted from ``main()`` so the one-shot entry (``main``) and the warm-worker serve
    loop (``serve_forever``) share the exact same per-request execution. Every per-request
    object — VM, memory bridge, builtins, state, tokens — is rebuilt from ``payload`` on
    each call, so a reused (warm) worker process carries no cross-run state
    (NODUS-WARMPOOL-1 Phase 1).
    """
    # NODUS-WARMPOOL-1 Phase 3 — eager pre-warm. A ``{"__warmup__": true}`` request pays the
    # plugin-stack load cost (``_ensure_tools_loaded``) ahead of real traffic without running
    # a script, so a pre-warmed pool worker is hot before its first real execution. Opt-in
    # (only the pool's prewarm sends it), so tool-less scripts still skip the load.
    if payload.get("__warmup__"):
        try:
            from AINDY.agents.tool_registry import _ensure_tools_loaded

            _ensure_tools_loaded()
        except Exception:  # a warm-up must never fail the worker
            pass
        return {
            "status": "success",
            "output_state": {},
            "emitted_events": [],
            "memory_writes": [],
            "simulated_effects": [],
            "error": None,
            "stdout_log": "",
            "warmed": True,
        }

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
    # AGENT-HARDEN-4 — effect simulation. When set, call_tool is shadowed: no real
    # tool runs, and each call records a predicted "would-write" intent here.
    simulate_mode = bool(ctx.get("simulate"))
    # DUR-2b — per-run at-most-once signal propagated across the subprocess boundary. When
    # set, wrap run_source so the in-subprocess sys()/call_tool() effect gates dedup
    # declaration-free (the parent's contextvar cannot cross into this process).
    durable_effects = bool(ctx.get("durable_effects"))
    simulated_effects: list[dict[str, Any]] = []
    # AGENT-HARDEN-4b — fake tool implementations (the simulated world).
    virtual_tools = ctx.get("virtual_tools")
    if not isinstance(virtual_tools, dict):
        virtual_tools = {}

    from nodus.runtime.embedding import NodusRuntime
    from AINDY.nodus.runtime.memory_bridge import AINDYMemoryBridge

    memory_deferral = DeferredMemoryBuiltins(memory_context, user_id, execution_unit_id)
    # DUR-2c — the per-(run, segment) scope for gating IMMEDIATE bridge writes
    # (remember/record_outcome) so a continuation re-run replays instead of re-writing.
    # Mirrors the parent-side deferred-write scope: run id + the per-segment effect_scope.
    _effect_scope = str(ctx.get("effect_scope") or "")
    _bridge_run_scope = f"{tool_run_id or execution_unit_id}:{_effect_scope}"
    bridge = AINDYMemoryBridge(user_id=user_id, run_scope=_bridge_run_scope)

    # Host functions registered with the VM.
    def _set_state(key: str, value: Any) -> None:
        state[key] = _json_safe(value)

    def _get_state(key: str) -> Any:
        return state.get(key)

    def _sys_dispatch(name: str, payload_arg: Any) -> Any:
        """Dispatch a Nodus sys() call through the AINDY syscall layer.

        Delegates to the module-level ``dispatch_worker_syscall`` so the sys() path loads
        the app plugin stack (registering app syscalls) before dispatch — FR-5b.
        """
        return dispatch_worker_syscall(name, payload_arg, user_id=user_id)

    # GUEST-CONFINE-1 — the guest VM runs submitted script content, not first-party code, so
    # it is denied the three ambient host capabilities. Without these the VM defaults to
    # allow_subprocess/network/env=True and nodus registers the *real* std:subprocess and
    # std:http modules, letting a guest script reach subprocess, network and host env
    # WITHOUT touching the dispatcher, capability token, effect ledger, egress guard or tool
    # registry — demonstrated 2026-08-15 (a guest script created a file on the host, read the
    # real PATH, and performed real DNS).
    #
    # These are deny-by-default and deliberately NOT configurable here. A per-execution
    # environment descriptor is EXEC-ENV-BIND-1; a global env flag would re-open the hole for
    # every run at once, which is the wrong shape. When a guest legitimately needs egress it
    # goes through the mediated paths (sys() / call_tool), which are gated.
    #
    # Note the VM already confines filesystem access: `allowed_paths` defaults to the cwd.
    # The demonstrated host-file write went through subprocess, which bypasses that check
    # entirely — so allow_subprocess=False is what closes it.
    runtime = NodusRuntime(
        project_root=_STDLIB_DIR if os.path.isdir(_STDLIB_DIR) else None,
        allow_subprocess=False,
        allow_network=False,
        allow_env=False,
    )
    def _call_tool(tool_name: Any, args: Any) -> Any:
        if simulate_mode:
            from AINDY.runtime.tool_simulation import simulate_agent_tool

            shadow = simulate_agent_tool(
                tool_name,
                args,
                user_id=user_id,
                run_id=tool_run_id,
                execution_token=tool_execution_token,
                virtual_tools=virtual_tools,
            )
            simulated_effects.append(_json_safe(shadow["would_write"]))
            return shadow["call_result"]
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

    # NODUS-SYS-SURFACE-1 — fail loud if a script reaches nodus's native `syscall` builtin
    # (via `import "std:sys"`) instead of AINDY's `sys(...)` builtin.
    _install_std_sys_guard()

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
    # The adapter always populates payload["max_execution_ms"] (env-resolved via
    # AINDY_NODUS_MAX_EXECUTION_MS). The env fallback here only covers a standalone
    # worker invocation with no budget in the payload, so both entry points agree.
    _payload_budget = payload.get("max_execution_ms")
    if _payload_budget:
        max_execution_ms = int(_payload_budget)
    else:
        _env_budget = os.getenv("AINDY_NODUS_MAX_EXECUTION_MS", "").strip()
        max_execution_ms = int(_env_budget) if _env_budget.isdigit() and int(_env_budget) > 0 else 30_000

    if durable_effects:
        from AINDY.kernel.effect_ledger import durable_effects_scope

        _durable_cm = durable_effects_scope()
    else:
        _durable_cm = contextlib.nullcontext()
    with _durable_cm, contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stdout_buffer):
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
                    "simulated_effects": simulated_effects,
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
                    "simulated_effects": simulated_effects,
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
                "simulated_effects": simulated_effects,
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
                "simulated_effects": simulated_effects,
                "error": str(exc),
                "stdout_log": stdout_buffer.getvalue(),
            }

    return result_payload


def main() -> int:
    """One-shot entry: read a single JSON payload from stdin, run it, write the result.

    The default execution path — the adapter spawns a fresh worker per execution unless
    the warm pool (NODUS-WARMPOOL-1) is enabled.
    """
    raw = sys.stdin.read()
    payload = json.loads(raw or "{}")
    sys.stdout.write(json.dumps(run_one(payload)))
    return 0


# ── Warm-worker serve loop (NODUS-WARMPOOL-1 Phase 1) ─────────────────────────
# A long-lived worker loads the plugin stack once (amortized across many executions),
# then serves requests over a length-prefixed JSON framing on stdin/stdout. Each request
# runs through run_one, which rebuilds all per-request state, so process reuse never leaks
# state between runs. Launched by the pool as `nodus_worker.py --serve`.

def _read_exact(stream, n: int) -> "bytes | None":
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None  # EOF — parent closed the pipe
        buf.extend(chunk)
    return bytes(buf)


def serve_forever() -> int:
    import struct

    # Frame over a private dup of stdout, then point fd 1 at devnull so nothing written
    # during a request can corrupt the protocol stream. (run_one already redirects
    # sys.stdout/err to a buffer; this is belt-and-suspenders at the OS-fd level.)
    raw_in = sys.stdin.buffer
    framing_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())
    os.close(devnull)

    while True:
        header = _read_exact(raw_in, 4)
        if header is None:
            return 0
        (length,) = struct.unpack(">I", header)
        body = _read_exact(raw_in, length)
        if body is None:
            return 0
        try:
            result = run_one(json.loads(body.decode("utf-8")))
        except Exception as exc:  # never let one bad request kill the warm worker
            result = {
                "status": "failure",
                "output_state": {},
                "emitted_events": [],
                "memory_writes": [],
                "simulated_effects": [],
                "error": f"warm worker request error: {exc}",
                "stdout_log": "",
            }
        data = json.dumps(result).encode("utf-8")
        framing_out.write(struct.pack(">I", len(data)))
        framing_out.write(data)
        framing_out.flush()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        raise SystemExit(serve_forever())
    raise SystemExit(main())
