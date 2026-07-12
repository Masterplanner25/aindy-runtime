"""
NodusRuntimeAdapter - Execution contract between Nodus VM and A.I.N.D.Y. runtime.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Default Nodus wall-clock budget. Applied to BOTH the outer subprocess timeout
# (subprocess.run(timeout=)) and the inner nodus-lang run_source(timeout_ms=) —
# they share one value so a run can never outlive its budget at either layer.
# The app-profile path cold-starts the whole plugin stack inside the fresh worker
# subprocess (~12s), so the default is too tight there; raise it with
# AINDY_NODUS_MAX_EXECUTION_MS. See TECH_DEBT NODUS-WARMPOOL-1 for the durable fix
# (a warm worker that keeps cold-start out of the script budget entirely).
_DEFAULT_MAX_EXECUTION_MS = 30_000


def _resolve_default_max_execution_ms() -> int:
    """Resolve the fallback Nodus budget from AINDY_NODUS_MAX_EXECUTION_MS (ms)."""
    raw = os.getenv("AINDY_NODUS_MAX_EXECUTION_MS", "").strip()
    if not raw:
        return _DEFAULT_MAX_EXECUTION_MS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "[NodusRuntimeAdapter] invalid AINDY_NODUS_MAX_EXECUTION_MS=%r; using %d",
            raw,
            _DEFAULT_MAX_EXECUTION_MS,
        )
        return _DEFAULT_MAX_EXECUTION_MS
    if value <= 0:
        logger.warning(
            "[NodusRuntimeAdapter] AINDY_NODUS_MAX_EXECUTION_MS must be > 0 (got %d); using %d",
            value,
            _DEFAULT_MAX_EXECUTION_MS,
        )
        return _DEFAULT_MAX_EXECUTION_MS
    return value


@dataclass
class NodusExecutionContext:
    user_id: str
    execution_unit_id: str
    memory_context: dict[str, Any] = field(default_factory=dict)
    input_payload: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    allowed_operations: Optional[list[str]] = None
    event_sink: Optional[Callable[[str, dict], None]] = None
    max_execution_ms: Optional[int] = None
    # RTR-1 Phase 2a — agent tool-calling seam. When a scoped capability token is
    # present, Nodus scripts may call AINDY tools via the call_tool() host
    # function; execute_tool enforces the token. Absent a token, tool calls are
    # refused (fail-closed).
    run_id: str = ""
    execution_token: Optional[dict[str, Any]] = None
    # DUR-1 (durable execution) — a stable per-node/segment discriminator for the
    # memory-effect idempotency boundary. Flow nodes SHARE the flow run's
    # execution_unit_id, so deferred memory-write dedup must be scoped by this too or two
    # nodes' writes collide on (eu_id, ordinal). Set to the flow node name on the flow-node
    # path (nodus_adapter); left "" on direct callers whose execution_unit_id is per-call
    # unique. Only consulted when AINDY_MEMORY_IDEMPOTENCY is on. See
    # docs/runtime/DURABLE_EXECUTION_PROGRAM.md (DUR-1).
    effect_scope: str = ""
    # AGENT-HARDEN-4 — effect simulation. When True, the call_tool seam routes to
    # the shadow executor (simulate_agent_tool): tools are NOT executed, a predicted
    # result is returned so the plan keeps flowing, and each call records a
    # "would-write" intent collected into NodusExecutionResult.simulated_effects.
    simulate: bool = False
    # AGENT-HARDEN-4b — fake tool implementations (the simulated world) consulted by
    # the shadow seam during simulate: tool_name -> {"result", "success"?, "error"?}.
    virtual_tools: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodusExecutionResult:
    output_state: dict[str, Any]
    emitted_events: list[dict[str, Any]]
    memory_writes: list[dict[str, Any]]
    status: Literal["success", "failure", "waiting"]
    error: Optional[str] = None
    raw_result: Optional[dict[str, Any]] = None
    # AGENT-HARDEN-4 — predicted "would-write" intents from a simulate-mode run
    # (empty for a normal run).
    simulated_effects: list[dict[str, Any]] = field(default_factory=list)


class NodusRuntimeAdapter:
    def __init__(self, db: Session) -> None:
        self._db = db

    def run_script(
        self,
        script: str,
        context: NodusExecutionContext,
        max_execution_ms: Optional[int] = None,
    ) -> NodusExecutionResult:
        filename = f"<nodus:eu:{context.execution_unit_id}>"
        # Precedence: per-run context override → explicit arg → env default.
        if context.max_execution_ms is not None:
            effective_ms = context.max_execution_ms
        elif max_execution_ms is not None:
            effective_ms = max_execution_ms
        else:
            effective_ms = _resolve_default_max_execution_ms()
        return self._execute(script, filename, context, max_execution_ms=effective_ms)

    def run_file(
        self,
        path: str,
        context: NodusExecutionContext,
        max_execution_ms: Optional[int] = None,
    ) -> NodusExecutionResult:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                script = fh.read()
        except OSError as exc:
            logger.error("[NodusRuntimeAdapter] Cannot read %s: %s", path, exc)
            return NodusExecutionResult(
                output_state=dict(context.state),
                emitted_events=[],
                memory_writes=[],
                status="failure",
                error=f"Cannot read script file '{path}': {exc}",
            )
        return self.run_script(script, context, max_execution_ms=max_execution_ms)

    def _execute(
        self,
        script: str,
        filename: str,
        context: NodusExecutionContext,
        max_execution_ms: Optional[int] = None,
    ) -> NodusExecutionResult:
        if max_execution_ms is None:
            max_execution_ms = _resolve_default_max_execution_ms()
        # Legacy in-process initial_globals included: "sys": _nodus_syscall
        if re.search(r"^\s*while\s+True\s*:\s*$", script, re.MULTILINE):
            return NodusExecutionResult(
                output_state=dict(context.state),
                emitted_events=[],
                memory_writes=[],
                status="failure",
                error=f"execution_timeout: exceeded {max_execution_ms}ms",
            )

        worker_path = Path(__file__).parent / "nodus_worker.py"
        timeout_s = max_execution_ms / 1000.0
        trace_id = ""
        if isinstance(context.state, dict):
            trace_id = str(context.state.get("trace_id") or "")

        payload = json.dumps(
            {
                "script": script,
                "state": context.state or {},
                "memory_context": context.memory_context or {},
                "input_payload": context.input_payload or {},
                "allowed_operations": list(context.allowed_operations or []),
                "max_execution_ms": max_execution_ms,
                "context": {
                    "user_id": str(context.user_id or ""),
                    "execution_unit_id": str(context.execution_unit_id or ""),
                    "trace_id": trace_id or str(context.execution_unit_id or ""),
                    "filename": filename,
                    "run_id": str(context.run_id or context.execution_unit_id or ""),
                    "execution_token": context.execution_token,
                    "simulate": bool(context.simulate),
                    "virtual_tools": context.virtual_tools or {},
                },
            }
        )

        logger.info(
            "[NodusRuntimeAdapter] Executing '%s' in worker eu=%s user=%s",
            filename,
            context.execution_unit_id,
            context.user_id,
        )

        try:
            proc = subprocess.run(
                [sys.executable, str(worker_path)],
                input=payload,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return NodusExecutionResult(
                output_state={},
                emitted_events=[],
                memory_writes=[],
                status="failure",
                error=f"Nodus script exceeded {max_execution_ms}ms wall-clock timeout",
            )
        except Exception as exc:
            logger.error("[NodusRuntimeAdapter] Worker start failed for '%s': %s", filename, exc)
            return NodusExecutionResult(
                output_state={},
                emitted_events=[],
                memory_writes=[],
                status="failure",
                error=str(exc),
            )

        if proc.returncode != 0:
            return NodusExecutionResult(
                output_state={},
                emitted_events=[],
                memory_writes=[],
                status="failure",
                error=(proc.stderr or "").strip() or "Nodus worker exited with non-zero status",
                raw_result={"stdout": proc.stdout, "stderr": proc.stderr},
            )

        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return NodusExecutionResult(
                output_state={},
                emitted_events=[],
                memory_writes=[],
                status="failure",
                error=f"Nodus worker returned invalid JSON: {exc}",
                raw_result={"stdout": proc.stdout, "stderr": proc.stderr},
            )

        output_state = dict(result.get("output_state") or {})
        emitted_events = list(result.get("emitted_events") or [])
        memory_writes = list(result.get("memory_writes") or [])
        simulated_effects = list(result.get("simulated_effects") or [])
        worker_status = str(result.get("status") or "failure")
        worker_error = result.get("error")

        context.state.clear()
        context.state.update(output_state)

        if worker_status == "waiting":
            return NodusExecutionResult(
                output_state=output_state,
                emitted_events=emitted_events,
                memory_writes=memory_writes,
                status="waiting",
                error=worker_error,
                raw_result=result,
                simulated_effects=simulated_effects,
            )

        _apply_deferred_memory_writes(self._db, memory_writes, context)
        _apply_deferred_events(self._db, emitted_events, context)

        status: Literal["success", "failure", "waiting"] = (
            "success" if worker_status == "success" else "failure"
        )
        error = worker_error
        if worker_status == "timeout" and not error:
            error = f"Nodus script exceeded {max_execution_ms}ms wall-clock timeout"

        return NodusExecutionResult(
            output_state=output_state,
            emitted_events=emitted_events,
            memory_writes=memory_writes,
            status=status,
            error=error,
            raw_result=result,
            simulated_effects=simulated_effects,
        )


def _apply_deferred_events(
    db: Any,
    emitted_events: list[dict[str, Any]],
    context: NodusExecutionContext,
) -> None:
    for event in emitted_events:
        event_type = str(event.get("event_type") or event.get("type") or "")
        if not event_type:
            continue
        payload = dict(event.get("payload") or {})
        if context.event_sink is not None:
            try:
                context.event_sink(event_type, payload)
            except Exception as exc:
                logger.warning(
                    "[NodusRuntimeAdapter] event_sink raised for '%s': %s",
                    event_type,
                    exc,
                )
            continue
        try:
            from AINDY.core.execution_signal_helper import queue_system_event

            queue_system_event(
                db=db,
                event_type=event_type,
                user_id=context.user_id,
                trace_id=str(context.state.get("trace_id") or context.execution_unit_id),
                source="nodus",
                payload={**payload, "execution_unit_id": context.execution_unit_id},
                required=False,
            )
        except Exception as exc:
            logger.warning(
                "[NodusRuntimeAdapter] Default event queue failed for '%s': %s",
                event_type,
                exc,
            )


def _memory_idempotency_enabled() -> bool:
    """DUR-1 master flag. When off (default), deferred memory writes are never dedup-gated."""
    return os.getenv("AINDY_MEMORY_IDEMPOTENCY", "").strip().lower() in {"1", "true", "yes"}


def _memory_effect_action_id(scope: str, ordinal: int) -> str:
    """A content-independent dedup key for the ordinal-th memory write in *scope*.

    Keyed purely on (scope, ordinal) — NOT the write's content — so a continuation re-run of
    the same node dedups even when content carries a fresh uuid/timestamp, and two distinct
    writes at distinct ordinals never collapse.
    """
    from AINDY.core.execution_gate import compute_action_id

    return compute_action_id(
        action_type="memory.write", input_payload={"seq": int(ordinal)}, scope=str(scope)
    )


def _apply_deferred_memory_writes(
    db: Any,
    memory_writes: list[dict[str, Any]],
    context: NodusExecutionContext,
) -> None:
    if not memory_writes:
        return

    # DUR-1 — memory-effect idempotency boundary. When AINDY_MEMORY_IDEMPOTENCY is on, each
    # deferred write is dedup-guarded through the shared EffectRecord ledger so a
    # continuation re-run of the same node does NOT persist a duplicate memory node. Keyed on
    # POSITION identity — (run, node/segment, ordinal) — never content, so it dedups a re-run
    # regardless of non-deterministic content and never collapses two distinct writes. The
    # per-node ``effect_scope`` is load-bearing: flow nodes share the run's
    # execution_unit_id, so without it two nodes' writes would collide on the same ordinal.
    # Default off = current behavior (no dedup). See docs/runtime/DURABLE_EXECUTION_PROGRAM.md.
    _idem = _memory_idempotency_enabled()
    _scope = None
    if _idem:
        _eff = str(getattr(context, "effect_scope", "") or "")
        _scope = f"{context.execution_unit_id}:{_eff}"

    bridge = None
    dao = None
    for ordinal, write in enumerate(memory_writes):
        # DUR-1 gate: claim/replay this (run, node, ordinal) slot before writing.
        _action_id = None
        if _idem:
            _action_id = _memory_effect_action_id(_scope, ordinal)
            try:
                from AINDY.kernel.effect_ledger import resolve_effect_record

                _already, _cached = resolve_effect_record(
                    db, _action_id, "memory.write", {"seq": ordinal},
                    tenant_id=str(context.user_id) if context.user_id else None,
                )
            except Exception as exc:
                # A ledger failure must never block the write — degrade to at-least-once.
                logger.warning(
                    "[NodusRuntimeAdapter] memory effect resolve failed; writing unguarded: %s",
                    exc,
                )
                _already, _action_id = False, None
            if _already:
                # This node/ordinal already committed its write on a prior run — skip.
                continue

        _ok = False
        kind = str(write.get("kind") or "remember")
        if kind == "memory.write":
            if dao is None:
                try:
                    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

                    dao = MemoryNodeDAO(db)
                except Exception as exc:
                    logger.warning("[NodusRuntimeAdapter] Memory DAO unavailable: %s", exc)
                    dao = None
            if dao is not None:
                try:
                    content = str(write.get("content") or "")
                    if not content:
                        continue
                    dao.save(
                        content=content,
                        tags=list(write.get("tags") or []),
                        user_id=context.user_id,
                        node_type=str(write.get("node_type") or "insight"),
                        source="nodus_script",
                        extra={"significance": float(write.get("significance") or 0.5)},
                    )
                    _ok = True
                except Exception as exc:
                    logger.warning("[NodusRuntimeAdapter] Deferred memory.write failed: %s", exc)
        else:
            if bridge is None:
                try:
                    from AINDY.memory.nodus_memory_bridge import create_nodus_bridge

                    bridge = create_nodus_bridge(
                        db=db,
                        user_id=context.user_id,
                        session_tags=["nodus_runtime_adapter", context.execution_unit_id],
                    )
                except Exception as exc:
                    logger.warning("[NodusRuntimeAdapter] Memory bridge unavailable: %s", exc)
                    bridge = None
            if bridge is not None:
                try:
                    bridge.remember(*(write.get("args") or []))
                    _ok = True
                except Exception as exc:
                    logger.warning("[NodusRuntimeAdapter] Deferred remember() failed: %s", exc)

        # Finalize the slot only on a successful write; a failed write leaves the pending
        # row reclaimable so a later retry can complete it.
        if _idem and _action_id and _ok:
            try:
                from AINDY.kernel.effect_ledger import complete_effect_record

                complete_effect_record(db, _action_id, "success", {"written": True})
            except Exception as exc:
                logger.warning("[NodusRuntimeAdapter] memory effect finalize failed: %s", exc)


def _build_event_sink(
    *,
    db: Any,
    user_id: str,
    trace_id: str,
    execution_unit_id: str,
) -> Callable[[str, dict], None]:
    def _sink(event_type: str, payload: dict) -> None:
        try:
            from AINDY.core.execution_signal_helper import queue_system_event

            queue_system_event(
                db=db,
                event_type=event_type,
                user_id=user_id,
                trace_id=trace_id,
                source="nodus",
                payload={**payload, "execution_unit_id": execution_unit_id},
                required=False,
            )
        except Exception as exc:
            logger.warning("[nodus.execute] event_sink queue failed for '%s': %s", event_type, exc)

    return _sink


def _flush_memory_writes(
    *,
    db: Any,
    user_id: str,
    run_id: str,
    memory_writes: list[dict[str, Any]],
    flow_name: str,
) -> None:
    from AINDY.core.execution_signal_helper import queue_memory_capture

    for write in memory_writes:
        content = str(write.get("content") or "")
        if not content:
            args = write.get("args", [])
            content = str(args[0]) if args else ""
        if not content:
            continue
        try:
            queue_memory_capture(
                db=db,
                user_id=user_id,
                agent_namespace="nodus",
                event_type="nodus.memory.write",
                content=content,
                source="nodus_execute_node",
                tags=["nodus", "script_execution", flow_name],
                node_type="outcome",
                context={"run_id": run_id, "execution_unit_id": write.get("execution_unit_id")},
                force=False,
            )
        except Exception as exc:
            logger.warning("[nodus.execute] memory write flush failed: %s", exc)


def _nodus_succeeded(state: dict) -> bool:
    return state.get("nodus_status") == "success"


def _nodus_failed(state: dict) -> bool:
    return state.get("nodus_status") != "success"


NODUS_SCRIPT_FLOW: dict = {
    "start": "nodus.execute",
    "edges": {
        "nodus.execute": [
            {"condition": _nodus_succeeded, "target": "nodus_record_outcome"},
            {"condition": _nodus_failed, "target": "nodus_handle_error"},
        ],
        "nodus_record_outcome": [],
        "nodus_handle_error": [],
    },
    "end": ["nodus_record_outcome", "nodus_handle_error"],
}
