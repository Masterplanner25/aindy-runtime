from __future__ import annotations

import contextlib
import os
import sys
import logging
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from AINDY.core.execution_record_service import build_execution_record as build_canonical_execution_record
from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext
from AINDY.runtime.nodus_runtime_adapter import NodusRuntimeAdapter
from AINDY.runtime.nodus_security import (
    ALLOWED_OPERATION_CAPABILITIES,
    NodusSecurityError,
    authorize_nodus_execution,
)
from AINDY.platform_layer.user_ids import parse_user_id
from AINDY.platform_layer.user_ids import require_user_id

logger = logging.getLogger(__name__)


def build_nodus_execution_summary(nodus_result) -> dict[str, Any]:
    """
    Normalize a Nodus runtime result into the shared summary shape used by flow
    execution, platform formatting, and direct route helpers.
    """
    return {
        "status": getattr(nodus_result, "status", None),
        "output_state": getattr(nodus_result, "output_state", {}) or {},
        "events_emitted": len(getattr(nodus_result, "emitted_events", []) or []),
        "memory_writes": len(getattr(nodus_result, "memory_writes", []) or []),
        "simulated_effects": list(getattr(nodus_result, "simulated_effects", []) or []),
        "error": getattr(nodus_result, "error", None),
    }


def build_nodus_execution_record(
    *,
    flow_status: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    nodus_summary: dict[str, Any] | None = None,
    nodus_status: str | None = None,
    output_state: dict[str, Any] | None = None,
    events: list[Any] | None = None,
    memory_writes: list[Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """
    Build the canonical Nodus execution record used across flow-backed and
    direct runtime entrypoints. Callers can wrap this record in route-specific
    envelopes without re-deriving execution metadata.
    """
    summary = dict(nodus_summary or {})
    normalized_output = output_state
    if normalized_output is None:
        normalized_output = summary.get("output_state") or {}
    normalized_events = list(events or [])
    normalized_writes = list(memory_writes or [])
    normalized_status = nodus_status or summary.get("status")
    normalized_error = error
    if normalized_error is None:
        normalized_error = summary.get("error")

    return {
        "status": flow_status,
        "trace_id": trace_id,
        "run_id": run_id,
        "nodus_status": normalized_status,
        "output_state": normalized_output,
        "events": normalized_events,
        "memory_writes": normalized_writes,
        "events_emitted": summary.get("events_emitted", len(normalized_events)),
        "memory_writes_count": summary.get("memory_writes", len(normalized_writes)),
        "error": normalized_error,
        "execution_record": build_canonical_execution_record(
            run_id=run_id,
            trace_id=trace_id or run_id or normalized_status,
            execution_unit_id=run_id or trace_id,
            workflow_type="nodus_execute",
            status=flow_status or normalized_status,
            error=normalized_error,
            actor="nodus",
            source="nodus",
            result_summary=summary,
            correlation_id=trace_id or run_id,
        ),
    }


def ensure_nodus_script_flow_registered() -> None:
    """
    Register the canonical Nodus script flow and its nodes exactly once.
    """
    import AINDY.runtime.nodus_adapter  # noqa: F401
    from AINDY.runtime.flow_engine import FLOW_REGISTRY, register_flow
    from AINDY.runtime.nodus_runtime_adapter import NODUS_SCRIPT_FLOW

    if "nodus_execute" not in FLOW_REGISTRY:
        register_flow("nodus_execute", NODUS_SCRIPT_FLOW)


def _run_nodus_via_flow_direct(
    *,
    script: str,
    input_payload: dict[str, Any],
    error_policy: str,
    db: Session,
    user_id: str,
    workflow_type: str = "nodus_execute",
    trace_id: str | None = None,
    extra_initial_state: dict[str, Any] | None = None,
    node_max_retries: Optional[int] = None,
) -> dict[str, Any]:
    """
    Internal Nodus execution implementation.

    Called by the sys.v1.nodus.execute syscall handler and by
    run_nodus_script_via_flow() when user_id is absent.
    Do not call directly from new code — use run_nodus_script_via_flow() or
    dispatch sys.v1.nodus.execute through the SyscallDispatcher.

    node_max_retries
        When provided, overrides the default flow-node retry limit for the
        ``nodus.execute`` node in this run.  The value is injected as
        ``flow["node_configs"]["nodus.execute"]["max_retries"]`` so the flow
        engine's retry gate can resolve the correct RetryPolicy per-run without
        touching the shared NODUS_SCRIPT_FLOW constant.

        None (default) → the flow-engine default (3 attempts) applies unchanged.
    """
    from AINDY.runtime import enforce_engine_boundary
    from AINDY.runtime.flow_engine import FLOW_REGISTRY, PersistentFlowRunner
    from AINDY.utils.uuid_utils import normalize_uuid

    enforce_engine_boundary(
        entrypoint="nodus.run",
        workflow_type=workflow_type,
    )
    ensure_nodus_script_flow_registered()

    # Build a per-run flow dict.  When node_max_retries is supplied we inject
    # node_configs so the retry gate in PersistentFlowRunner can honour it.
    # The shared NODUS_SCRIPT_FLOW constant is never mutated.
    flow = FLOW_REGISTRY["nodus_execute"]
    if node_max_retries is not None:
        flow = {
            **flow,
            "node_configs": {"nodus.execute": {"max_retries": node_max_retries}},
        }

    runner = PersistentFlowRunner(
        flow=flow,
        db=db,
        user_id=normalize_uuid(user_id) if user_id else None,
        workflow_type=workflow_type,
    )
    initial_state = {
        "nodus_script": script,
        "nodus_input_payload": input_payload,
        "nodus_error_policy": error_policy,
    }
    if trace_id is not None:
        initial_state["trace_id"] = trace_id
    if extra_initial_state:
        initial_state.update(extra_initial_state)
    return runner.start(
        initial_state=initial_state,
        flow_name="nodus_execute",
    )


def run_nodus_script_via_flow(
    *,
    script: str,
    input_payload: dict[str, Any],
    error_policy: str,
    db: Session,
    user_id: str,
    workflow_type: str = "nodus_execute",
    trace_id: str | None = None,
    extra_initial_state: dict[str, Any] | None = None,
    node_max_retries: Optional[int] = None,
) -> dict[str, Any]:
    """
    Execute a Nodus script through the canonical flow-backed orchestration path.

    Routes through sys.v1.nodus.execute for unified capability enforcement,
    quota tracking, and observability. Falls back to _run_nodus_via_flow_direct()
    for anonymous/system calls (user_id absent).
    """
    from AINDY.runtime import enforce_engine_boundary

    enforce_engine_boundary(
        entrypoint="nodus.run",
        workflow_type=workflow_type,
    )
    if not user_id:
        logger.debug(
            "[run_nodus_script_via_flow] no user_id — executing directly "
            "(syscall layer requires identity)"
        )
        return _run_nodus_via_flow_direct(
            script=script,
            input_payload=input_payload,
            error_policy=error_policy,
            db=db,
            user_id=user_id,
            workflow_type=workflow_type,
            trace_id=trace_id,
            extra_initial_state=extra_initial_state,
            node_max_retries=node_max_retries,
        )

    import uuid as _uuid
    from AINDY.kernel.syscall_dispatcher import get_dispatcher, SyscallContext

    _trace_id = trace_id or str(_uuid.uuid4())
    ctx = SyscallContext(
        execution_unit_id=_trace_id,
        user_id=str(user_id),
        capabilities=["nodus.execute", "flow.run"],
        trace_id=_trace_id,
        metadata={"_db": db, "_extra_initial_state": extra_initial_state},
    )
    _nodus_payload: dict[str, Any] = {
        "script": script,
        "input_payload": input_payload or {},
        "error_policy": error_policy,
        "workflow_type": workflow_type,
    }
    if trace_id is not None:
        _nodus_payload["trace_id"] = trace_id
    if node_max_retries is not None:
        _nodus_payload["node_max_retries"] = node_max_retries
    result = get_dispatcher().dispatch("sys.v1.nodus.execute", _nodus_payload, ctx)
    if result["status"] == "error":
        raise RuntimeError(
            f"sys.v1.nodus.execute failed: {result.get('error', '')}"
        )
    return result["data"]["nodus_result"]


def format_nodus_flow_result(flow_result: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a flow-backed Nodus execution result into the stable route shape.
    """
    final_state = flow_result.get("state") or {}
    nodus_result = flow_result.get("data") or {}
    if not isinstance(nodus_result, dict) or "status" not in nodus_result:
        nodus_result = final_state.get("nodus_execute_result") or {}

    return build_nodus_execution_record(
        flow_status=flow_result.get("status"),
        trace_id=flow_result.get("trace_id"),
        run_id=flow_result.get("run_id"),
        nodus_summary=nodus_result,
        nodus_status=final_state.get("nodus_status") or nodus_result.get("status"),
        output_state=nodus_result.get("output_state") or final_state.get("nodus_output_state") or {},
        events=final_state.get("nodus_events") or [],
        memory_writes=final_state.get("nodus_memory_writes") or [],
        error=(
            nodus_result.get("error")
            or final_state.get("nodus_handled_error")
            or (None if flow_result.get("status") != "FAILED" else flow_result.get("error"))
        ),
    )

def execute_agent_flow_orchestration(
    *,
    run_id: str,
    plan: dict[str, Any],
    user_id: str,
    db: Session,
    correlation_id: str | None = None,
    execution_token: dict[str, Any] | None = None,
    capability_token: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Canonical flow-backed agent execution path.

    The adapter remains import-compatible, but actual flow orchestration lives
    here so agent_runtime and any compatibility wrappers converge on one
    runtime-owned execution shell.
    """
    from datetime import datetime, timezone

    from AINDY.agents.capability_service import check_execution_capability
    from AINDY.core.execution_signal_helper import queue_system_event, record_agent_event
    from AINDY.db.models import AgentRun, AgentStep
    from AINDY.runtime.flow_engine import PersistentFlowRunner
    from AINDY.runtime.nodus_adapter import AGENT_FLOW, _db_run_id

    emit_system_event = queue_system_event

    try:
        steps = (plan or {}).get("steps", [])
        scoped_token = execution_token or capability_token
        flow_capability_check = {"ok": False, "error": "missing scoped capability token"}
        if scoped_token is not None:
            flow_capability_check = check_execution_capability(
                token=scoped_token,
                run_id=run_id,
                user_id=user_id,
                capability_name="execute_flow",
            )
        if not flow_capability_check["ok"]:
            agent_run = db.query(AgentRun).filter(AgentRun.id == _db_run_id(run_id)).first()
            if agent_run and agent_run.status == "executing":
                agent_run.status = "failed"
                agent_run.completed_at = datetime.now(timezone.utc)
                agent_run.error_message = flow_capability_check["error"]
                agent_run.result = {"steps": []}
            emit_system_event(
                db=db,
                event_type="capability.denied",
                user_id=user_id,
                trace_id=correlation_id,
                source="agent",
                payload={
                    "run_id": str(run_id),
                    "capability": "execute_flow",
                    "error": flow_capability_check["error"],
                },
                required=True,
            )
            record_agent_event(
                run_id=run_id,
                user_id=user_id,
                event_type="CAPABILITY_DENIED",
                db=db,
                correlation_id=correlation_id,
                payload={
                    "capability": "execute_flow",
                    "error": flow_capability_check["error"],
                },
                required=True,
            )
            logger.warning(
                "[NodusExecutionService] Flow capability denied for AgentRun %s: %s",
                run_id,
                flow_capability_check["error"],
            )
            return {"status": "FAILED", "error": flow_capability_check["error"]}
        emit_system_event(
            db=db,
            event_type="capability.allowed",
            user_id=user_id,
            trace_id=correlation_id,
            source="agent",
            payload={"run_id": str(run_id), "capability": "execute_flow"},
            required=True,
        )

        initial_state = {
            "agent_run_id": run_id,
            "user_id": user_id,
            "steps": steps,
            "memory_context": (plan or {}).get("memory_context", {}),
            "current_step_index": 0,
            "step_results": [],
            "correlation_id": correlation_id,
            "execution_token": scoped_token,
        }

        runner = PersistentFlowRunner(
            flow=AGENT_FLOW,
            db=db,
            user_id=user_id,
            workflow_type="agent_execution",
        )

        logger.info(
            "[NodusExecutionService] Starting flow for AgentRun %s (%d steps)",
            run_id,
            len(steps),
        )
        flow_result = runner.start(initial_state, flow_name="agent_execution")

        flow_run_id = flow_result.get("run_id")
        if flow_run_id:
            agent_run = db.query(AgentRun).filter(AgentRun.id == _db_run_id(run_id)).first()
            if agent_run:
                agent_run.flow_run_id = str(flow_run_id)
                db.commit()

        if flow_result.get("status") != "SUCCESS":
            agent_run = db.query(AgentRun).filter(AgentRun.id == _db_run_id(run_id)).first()
            if agent_run and agent_run.status == "executing":
                completed_steps = (
                    db.query(AgentStep)
                    .filter(AgentStep.run_id == _db_run_id(run_id))
                    .order_by(AgentStep.step_index.asc())
                    .all()
                )
                step_results = [
                    {
                        "step_index": s.step_index,
                        "tool": s.tool_name,
                        "status": s.status,
                        "result": s.result,
                        "error": s.error_message,
                    }
                    for s in completed_steps
                ]
                agent_run.status = "failed"
                agent_run.completed_at = datetime.now(timezone.utc)
                agent_run.result = {"steps": step_results}
                agent_run.error_message = flow_result.get("error", "Flow execution failed")
                db.commit()

        return flow_result
    except Exception as exc:
        logger.warning("[NodusExecutionService] execute_agent_flow_orchestration failed: %s", exc)
        return {"status": "FAILED", "error": str(exc)}


def reconstruct_agent_step_results(
    steps_meta: list[dict[str, Any]],
    output_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Map a compiled plan's per-step ``__step_N_result`` outputs to step results.

    Returns ``(step_results, any_failed)`` where each entry is the AGENT_FLOW-shape
    ``{step_index, tool, status, result, error}``. A step whose result_key is
    absent from ``output_state`` did not run and is skipped — since RTR-1 Phase 2d
    a failed step ``throw``s and halts the workflow, so a trailing absent step was
    halted by an earlier failure (halt-on-first-failure), not silently dropped.
    """
    step_results: list[dict[str, Any]] = []
    any_failed = False
    for meta in steps_meta:
        tool_result = output_state.get(meta["result_key"])
        if tool_result is None:
            continue
        if not isinstance(tool_result, dict):
            tool_result = {"success": False, "result": None, "error": str(tool_result)}
        ok = bool(tool_result.get("success"))
        if not ok:
            any_failed = True
        step_results.append(
            {
                "step_index": meta["index"],
                "tool": meta["tool"],
                "status": "success" if ok else "failed",
                "result": tool_result.get("result"),
                "error": tool_result.get("error"),
            }
        )
    return step_results, any_failed


def _run_agent_segment_flow(
    *,
    run_id: str,
    compiled: dict[str, Any],
    user_id: str,
    db: Session,
    correlation_id: str | None,
    scoped_token: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    """Run ONE compiled segment through the flow-backed Nodus path.

    Persists an ``AgentStep`` row + step event for each step that ran and returns
    ``(segment_step_results, segment_failed, flow_result)``. ``segment_failed`` is
    True when the flow errored, any step failed, or a step was halted (fewer
    results than the segment declared). Does not touch ``AgentRun`` status — the
    caller owns run-level transitions across the whole segment chain.
    """
    from datetime import datetime, timezone

    from AINDY.core.execution_signal_helper import record_agent_event
    from AINDY.db.models import AgentStep
    from AINDY.runtime.nodus_adapter import _db_run_id

    script = compiled["source"] + f"\nrun_workflow({compiled['workflow_name']})\n"
    # execution_token + agent_run_id ride in extra_initial_state so the
    # nodus.execute node threads them to the call_tool seam (context only).
    flow_result = run_nodus_script_via_flow(
        script=script,
        input_payload=compiled["input_payload"],
        error_policy="halt",
        db=db,
        user_id=user_id,
        # Must be a "nodus"-labelled workflow_type: run_nodus_script_via_flow calls
        # enforce_engine_boundary(entrypoint="nodus.run"), which rejects a label
        # without "nodus" as a Python-DAG flow. The nodus_vm agent path IS nodus-
        # backed (it compiles the plan to a native workflow), so it is labelled
        # accordingly. (AGENT_FLOW keeps "agent_execution" — it uses flow.run.)
        workflow_type="nodus_agent_execution",
        trace_id=correlation_id,
        extra_initial_state={
            "execution_token": scoped_token,
            "agent_run_id": str(run_id),
        },
    )

    output_state = format_nodus_flow_result(flow_result).get("output_state") or {}
    step_results, any_failed = reconstruct_agent_step_results(compiled["steps"], output_state)
    ran_by_index = {r["step_index"] for r in step_results}

    now = datetime.now(timezone.utc)
    for meta in compiled["steps"]:
        if meta["index"] not in ran_by_index:
            continue
        entry = next(r for r in step_results if r["step_index"] == meta["index"])
        db.add(
            AgentStep(
                run_id=_db_run_id(run_id),
                step_index=meta["index"],
                tool_name=meta["tool"],
                tool_args=meta["args"],
                risk_level=meta["risk_level"],
                description=meta["description"],
                status=entry["status"],
                result=entry["result"],
                error_message=entry["error"],
                executed_at=now,
                correlation_id=correlation_id,
            )
        )
        record_agent_event(
            run_id=run_id, user_id=user_id,
            event_type="AGENT_STEP_COMPLETED" if entry["status"] == "success" else "AGENT_STEP_FAILED",
            db=db, correlation_id=correlation_id,
            payload={"step_index": meta["index"], "tool": meta["tool"], "status": entry["status"]},
            required=False,
        )

    flow_ok = flow_result.get("status") == "SUCCESS"
    segment_failed = any_failed or (not flow_ok) or (len(step_results) != len(compiled["steps"]))
    return step_results, segment_failed, flow_result


def _build_agent_resume_callback(
    *,
    run_id: str,
    segments: list[dict[str, Any]],
    next_segment_index: int,
    accumulated: list[dict[str, Any]],
    user_id: str,
    correlation_id: str | None,
    scoped_token: dict[str, Any] | None,
    total_tool_steps: int,
):
    """Build the 0-arg resume closure shared by live-registration and rehydration.

    On fire it opens its own ``SessionLocal`` and does an **atomic claim** —
    ``UPDATE agent_runs SET status='executing', wait_state=NULL WHERE id=? AND
    status='waiting'`` — so exactly one caller proceeds even across a duplicate
    event-fire, the resume watchdog, a second rehydration, or multiple instances.
    The claim winner runs ``_execute_agent_segment_chain`` for the next segment,
    carrying the accumulated step results forward (completed segments never re-run).
    The closure captures only plain values, never a live DB session.
    """
    def _resume() -> None:
        from AINDY.db.database import SessionLocal
        from AINDY.db.models import AgentRun
        from AINDY.platform_layer.async_execution_context import (
            activate_async_execution_context,
            deactivate_async_execution_context,
        )
        from AINDY.runtime.nodus_adapter import _db_run_id

        _db = SessionLocal()
        try:
            claimed = (
                _db.query(AgentRun)
                .filter(AgentRun.id == _db_run_id(run_id), AgentRun.status == "waiting")
                .update({"status": "executing", "wait_state": None}, synchronize_session=False)
            )
            _db.commit()
            if not claimed:
                logger.info(
                    "[NodusExecutionService] agent resume skipped for %s (not in waiting / already claimed)",
                    run_id,
                )
                return
            # Refresh an expired capability token before running the segment. A run
            # parked on a WAIT across a long wait / restart may have a token past its
            # TTL; refresh_token reuses the same grants on a fresh clock (no policy
            # re-eval), so tools execute instead of failing. Persisted for later resumes.
            effective_token = scoped_token
            try:
                from AINDY.agents.capability_service import refresh_token, token_is_expired

                if token_is_expired(effective_token):
                    refreshed = refresh_token(effective_token)
                    if refreshed is not None:
                        effective_token = refreshed
                        run = _db.query(AgentRun).filter(AgentRun.id == _db_run_id(run_id)).first()
                        if run is not None:
                            run.capability_token = refreshed
                            run.execution_token = refreshed.get("execution_token")
                            _db.commit()
                        logger.info(
                            "[NodusExecutionService] refreshed expired capability token for %s", run_id
                        )
            except Exception as exc:  # non-fatal: fall back to the original token
                logger.warning(
                    "[NodusExecutionService] capability token refresh skipped for %s: %s", run_id, exc
                )
            # The resume callback fires from the scheduler (event notify, resume
            # watchdog, or cross-restart rehydration) with NO ExecutionPipeline
            # wrapper, so is_pipeline_active() is False for the entire resumed
            # segment. The initial run gets its execution context implicitly from
            # the request pipeline; a resumed segment must establish the equivalent
            # itself. Activate the async-execution context — the same signal the
            # flow runner uses for background execution — so every execution.* event
            # emitted anywhere in the resumed chain (the flow runner's
            # execution.started, EU status syncs, etc.) satisfies the
            # ExecutionContract guard instead of raising under
            # ENFORCE_EXECUTION_CONTRACT and stranding the run at 'executing' (#152).
            _async_token = activate_async_execution_context()
            try:
                _execute_agent_segment_chain(
                    run_id=run_id,
                    segments=segments,
                    segment_index=next_segment_index,
                    accumulated=accumulated,
                    user_id=user_id,
                    db=_db,
                    correlation_id=correlation_id,
                    scoped_token=effective_token,
                    total_tool_steps=total_tool_steps,
                )
            finally:
                deactivate_async_execution_context(_async_token)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[NodusExecutionService] agent segment resume failed for %s: %s", run_id, exc)
            with contextlib.suppress(Exception):
                _db.rollback()
        finally:
            with contextlib.suppress(Exception):
                _db.close()

    return _resume


def _register_agent_wait(
    *,
    run_id: str,
    event_type: str,
    correlation_key: str | None,
    user_id: str,
    correlation_id: str | None,
    resume_callback,
) -> None:
    """Register an agent segment wait with the scheduler (shared by both paths).

    The scheduler's in-memory ``_waiting`` + ``notify_event`` path drives the
    callback; the ``WaitingFlowRun`` DB backup is skipped for ``eu_type="agent"``
    (it FKs to ``flow_runs``). Cross-restart durability comes from the AgentRun
    row itself (``status="waiting"`` + ``wait_state``), rehydrated at startup by
    ``rehydrate_waiting_agent_runs``.
    """
    corr = correlation_key or correlation_id
    try:
        from AINDY.core.wait_condition import WaitCondition
        from AINDY.kernel.scheduler_engine import get_scheduler_engine

        get_scheduler_engine().register_wait(
            run_id=str(run_id),
            wait_for_event=event_type,
            tenant_id=str(user_id or ""),
            eu_id=str(run_id),
            resume_callback=resume_callback,
            correlation_id=corr,
            trace_id=str(correlation_id or run_id),
            eu_type="agent",
            wait_condition=WaitCondition.for_event(event_type, correlation_id=corr),
        )
    except Exception as exc:
        logger.warning("[NodusExecutionService] agent wait registration failed for %s: %s", run_id, exc)


def _register_agent_segment_wait(
    *,
    run_id: str,
    wait: dict[str, Any],
    segments: list[dict[str, Any]],
    segment_index: int,
    accumulated: list[dict[str, Any]],
    user_id: str,
    correlation_id: str | None,
    scoped_token: dict[str, Any] | None,
    total_tool_steps: int,
) -> None:
    """Register a live-process wait that resumes at the NEXT segment on its event."""
    callback = _build_agent_resume_callback(
        run_id=run_id,
        segments=segments,
        next_segment_index=segment_index + 1,
        accumulated=accumulated,
        user_id=user_id,
        correlation_id=correlation_id,
        scoped_token=scoped_token,
        total_tool_steps=total_tool_steps,
    )
    _register_agent_wait(
        run_id=run_id,
        event_type=wait["event_type"],
        correlation_key=wait.get("correlation_key"),
        user_id=user_id,
        correlation_id=correlation_id,
        resume_callback=callback,
    )


def _sync_agent_eu_status(db: Session, run_id: str, status: str) -> None:
    """Best-effort mirror of a terminal AgentRun status onto its ExecutionUnit.

    The synchronous entry path (agent_runtime.execution) updates the EU after the
    call returns, but a run that reaches a terminal state via the Phase 2e resume
    callback has no such caller — so the chain syncs the EU itself. Non-fatal.
    """
    try:
        from AINDY.core.execution_unit_service import ExecutionUnitService

        eu = ExecutionUnitService(db).get_by_source("agent_run", str(run_id))
        if eu:
            ExecutionUnitService(db).update_status(eu.id, status)
    except Exception:
        logger.debug("[NodusExecutionService] agent EU status sync skipped", exc_info=True)


def _execute_agent_segment_chain(
    *,
    run_id: str,
    segments: list[dict[str, Any]],
    segment_index: int,
    accumulated: list[dict[str, Any]],
    user_id: str,
    db: Session,
    correlation_id: str | None,
    scoped_token: dict[str, Any] | None,
    total_tool_steps: int,
) -> dict[str, Any]:
    """Run one plan segment, then complete / fail / suspend the AgentRun.

    Runs exactly one segment per call. On success with a trailing wait, it parks
    the run (``status="waiting"``) and registers a resume for the next segment.
    On success of the terminal segment it completes the run. On any step/flow
    failure it fails the run. ``accumulated`` carries prior segments' step results
    so the run's ``result`` and counters reflect the whole plan, not just this
    segment.
    """
    from datetime import datetime, timezone

    from AINDY.core.execution_signal_helper import record_agent_event
    from AINDY.db.models import AgentRun
    from AINDY.runtime.agent_plan_compiler import compile_agent_segment
    from AINDY.runtime.nodus_adapter import _db_run_id

    try:
        # ── Cooperative cancel checkpoint (segment boundary) ──────────────────────
        # AGENT-HARDEN-1: sys.v1.agent.cancel flips a non-terminal run to
        # 'cancelled' via an atomic CAS committed in a separate session. Observe it
        # here — before this segment's tools run — so a cancel halts the chain
        # between steps without corrupting mid-tool state. The cancel syscall owns
        # the terminal transition + CANCELLED event; the chain only bails.
        _cancel_check = (
            db.query(AgentRun.status)
            .filter(AgentRun.id == _db_run_id(run_id))
            .first()
        )
        if _cancel_check is not None and _cancel_check[0] == "cancelled":
            logger.info(
                "[NodusExecutionService] agent run %s cancelled before segment %d — halting",
                run_id,
                segment_index,
            )
            return {"status": "CANCELLED", "run_id": str(run_id)}

        seg = segments[segment_index]
        seg_results: list[dict[str, Any]] = []
        segment_failed = False
        flow_result: dict[str, Any] = {"status": "SUCCESS"}

        if seg["tool_steps"]:
            compiled = compile_agent_segment(
                seg["tool_steps"],
                base_index=seg["base_index"],
                workflow_name=f"agent_plan_seg{segment_index}",
            )
            seg_results, segment_failed, flow_result = _run_agent_segment_flow(
                run_id=run_id,
                compiled=compiled,
                user_id=user_id,
                db=db,
                correlation_id=correlation_id,
                scoped_token=scoped_token,
            )

        accumulated = accumulated + seg_results
        ran = len(accumulated)
        now = datetime.now(timezone.utc)
        run = db.query(AgentRun).filter(AgentRun.id == _db_run_id(run_id)).first()
        flow_run_id = flow_result.get("run_id")

        # ── Cooperative cancel checkpoint (post-segment) ──────────────────────────
        # AGENT-HARDEN-1: a cancel that won the CAS while this segment's tools ran
        # is authoritative — honor the terminal 'cancelled' state and do NOT clobber
        # it with completed/failed/waiting. The segment's committed AgentStep rows
        # stand; the chain simply stops (no resume is registered, so no next segment
        # runs). Step-event commits inside the segment expire the session, so this
        # re-read reflects the cancel.
        if run is not None and run.status == "cancelled":
            logger.info(
                "[NodusExecutionService] agent run %s cancelled during segment %d — halting",
                run_id,
                segment_index,
            )
            return {"status": "CANCELLED", "run_id": str(run_id)}

        # ── Failure: fail the whole run, stop the chain ───────────────────────────
        if segment_failed:
            if run:
                if flow_run_id:
                    run.flow_run_id = str(flow_run_id)
                run.steps_completed = ran
                run.current_step = ran
                run.result = {"steps": accumulated}
                run.completed_at = now
                run.status = "failed"
                run.wait_state = None
                first_failed = next((r for r in seg_results if r["status"] == "failed"), None)
                if first_failed is not None and first_failed.get("error"):
                    run.error_message = (
                        f"step {first_failed['step_index']} "
                        f"({first_failed['tool']}) failed: {first_failed['error']}"
                    )
                else:
                    run.error_message = flow_result.get("error") or "one or more agent steps failed"
            db.commit()
            _sync_agent_eu_status(db, run_id, "failed")
            record_agent_event(
                run_id=run_id, user_id=user_id, event_type="FAILED", db=db,
                correlation_id=correlation_id,
                payload={"steps_completed": ran, "steps_total": total_tool_steps},
                required=False,
            )
            return flow_result

        # ── Success + trailing wait: park the run, register the resume ────────────
        if seg["wait"] is not None:
            if run:
                if flow_run_id:
                    run.flow_run_id = str(flow_run_id)
                run.steps_completed = ran
                run.current_step = ran
                run.result = {"steps": accumulated}
                run.status = "waiting"
                # Durable wait descriptor for cross-restart rehydration (Phase 2e).
                run.wait_state = {
                    "event_type": seg["wait"]["event_type"],
                    "correlation_key": seg["wait"].get("correlation_key"),
                    "resume_segment_index": segment_index + 1,
                }
            db.commit()
            _register_agent_segment_wait(
                run_id=run_id, wait=seg["wait"], segments=segments,
                segment_index=segment_index, accumulated=accumulated,
                user_id=user_id, correlation_id=correlation_id,
                scoped_token=scoped_token, total_tool_steps=total_tool_steps,
            )
            record_agent_event(
                run_id=run_id, user_id=user_id, event_type="WAITING", db=db,
                correlation_id=correlation_id,
                payload={
                    "wait_for": seg["wait"]["event_type"],
                    "steps_completed": ran, "steps_total": total_tool_steps,
                },
                required=False,
            )
            return {"status": "WAITING", "wait_for": seg["wait"]["event_type"], "run_id": str(run_id)}

        # ── Success + terminal segment: verify, then complete the run ─────────────
        completed_ok = ran == total_tool_steps

        # Verifier stage (AGENT-HARDEN-6): on a fully-run plan, check declared
        # per-step post-conditions (`expects`) before marking the run complete.
        # Plans with no `expects` verify vacuously (checked == 0) — no behavior
        # change. Failure marks the run 'verify_failed' and rolls back reversible
        # effects via the AGENT-HARDEN-3 compensators.
        verdict = None
        if completed_ok:
            from AINDY.core.verifier import extract_post_conditions, verify_post_conditions

            verdict = verify_post_conditions(
                extract_post_conditions(getattr(run, "plan", None) if run else None),
                accumulated,
            )

        if completed_ok and verdict is not None and not verdict["ok"]:
            if run:
                if flow_run_id:
                    run.flow_run_id = str(flow_run_id)
                run.steps_completed = ran
                run.current_step = ran
                run.result = {"steps": accumulated, "verify": verdict}
                run.completed_at = now
                run.status = "verify_failed"
                run.wait_state = None
                run.error_message = (
                    f"post-condition verification failed: "
                    f"{len(verdict['failures'])} check(s) did not hold"
                )
            db.commit()
            _sync_agent_eu_status(db, run_id, "verify_failed")
            # Roll back reversible effects (best-effort — the run stays verify_failed
            # even if undo hits a snag; irreversible effects are surfaced by undo).
            undo_summary: dict[str, Any] = {}
            try:
                from AINDY.core.effect_compensation import undo_run_effects

                undo_summary = undo_run_effects(str(run_id), db=db, context=None)
            except Exception as exc:
                logger.warning(
                    "[NodusExecutionService] undo after verify_failed for %s failed: %s",
                    run_id, exc,
                )
            record_agent_event(
                run_id=run_id, user_id=user_id, event_type="VERIFY_FAILED", db=db,
                correlation_id=correlation_id,
                payload={
                    "failures": verdict["failures"],
                    "checked": verdict["checked"],
                    "reversed": undo_summary.get("reversed", []),
                    "irreversible": undo_summary.get("irreversible", []),
                },
                required=False,
            )
            return {"status": "VERIFY_FAILED", "run_id": str(run_id), "verify": verdict}

        if run:
            if flow_run_id:
                run.flow_run_id = str(flow_run_id)
            run.steps_completed = ran
            run.current_step = ran
            run.result = {"steps": accumulated}
            run.completed_at = now
            run.status = "completed" if completed_ok else "failed"
            run.wait_state = None
            if not completed_ok:
                run.error_message = "one or more agent steps did not run"
        db.commit()
        _sync_agent_eu_status(db, run_id, "completed" if completed_ok else "failed")
        record_agent_event(
            run_id=run_id, user_id=user_id,
            event_type="COMPLETED" if completed_ok else "FAILED", db=db,
            correlation_id=correlation_id,
            payload={"steps_completed": ran, "steps_total": total_tool_steps},
            required=False,
        )
        # Emit VERIFIED only when post-conditions were actually checked and held.
        if completed_ok and verdict is not None and verdict["checked"] > 0:
            record_agent_event(
                run_id=run_id, user_id=user_id, event_type="VERIFIED", db=db,
                correlation_id=correlation_id,
                payload={"checked": verdict["checked"]},
                required=False,
            )
        return flow_result
    except Exception as exc:
        logger.warning("[NodusExecutionService] agent segment chain failed for %s: %s", run_id, exc)
        return {"status": "FAILED", "error": str(exc)}


def execute_agent_run_via_workflow(
    *,
    run_id: str,
    plan: dict[str, Any],
    user_id: str,
    db: Session,
    correlation_id: str | None = None,
    execution_token: dict[str, Any] | None = None,
    capability_token: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """RTR-1 Phase 2c–2e — OPT-IN VM-backed agent execution.

    Splits the plan into segments at WAIT boundaries (``split_agent_plan``) and
    runs each segment as a native Nodus workflow through the canonical
    flow-backed path (``run_nodus_script_via_flow``), so each step's tool call
    goes through the capability-enforced ``call_tool`` seam. ``AgentStep`` rows,
    status/counters, result, and completion events are reconstructed from each
    segment's output state.

    Reproduced vs AGENT_FLOW: tool calls (capability-enforced), AgentStep
    recording, status/counters, result reconstruction, capability + completion
    events, per-step **retry** (risk-based ``max_attempts`` with non-transient
    short-circuit, Phase 2d), **halt-on-first-failure** (a failed step ``throw``s,
    Phase 2d), and **mid-plan WAIT/RESUME** (Phase 2e): a plan WAIT step
    (``{"wait_for": "<event.type>"}``) parks the run at ``status="waiting"`` and
    registers a live-process resume that runs the next segment when the event
    fires — completed segments are never re-run, so tool calls never fire twice.

    Live-process durability: the wait rides the scheduler's in-memory
    ``_waiting``/``notify_event`` path; cross-restart rehydration of a waiting
    agent run is the documented follow-up. Selected via
    ``AINDY_AGENT_EXECUTION_BACKEND=nodus_vm``; ``AGENT_FLOW`` remains the default
    until this path is proven at parity on real Postgres.
    """
    from datetime import datetime, timezone

    from AINDY.agents.capability_service import check_execution_capability
    from AINDY.core.execution_signal_helper import queue_system_event, record_agent_event
    from AINDY.db.models import AgentRun
    from AINDY.runtime.agent_plan_compiler import split_agent_plan
    from AINDY.runtime.nodus_adapter import _db_run_id

    def _fail_run(message: str) -> None:
        run = db.query(AgentRun).filter(AgentRun.id == _db_run_id(run_id)).first()
        if run and run.status == "executing":
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.error_message = message
            run.result = {"steps": []}
            db.commit()

    try:
        scoped_token = execution_token or capability_token

        # ── Flow-level capability gate (mirrors execute_agent_flow_orchestration) ─
        flow_capability_check = {"ok": False, "error": "missing scoped capability token"}
        if scoped_token is not None:
            flow_capability_check = check_execution_capability(
                token=scoped_token,
                run_id=run_id,
                user_id=user_id,
                capability_name="execute_flow",
            )
        if not flow_capability_check["ok"]:
            _fail_run(flow_capability_check["error"])
            queue_system_event(
                db=db, event_type="capability.denied", user_id=user_id,
                trace_id=correlation_id, source="agent",
                payload={"run_id": str(run_id), "capability": "execute_flow", "error": flow_capability_check["error"]},
                required=True,
            )
            record_agent_event(
                run_id=run_id, user_id=user_id, event_type="CAPABILITY_DENIED", db=db,
                correlation_id=correlation_id,
                payload={"capability": "execute_flow", "error": flow_capability_check["error"]},
                required=True,
            )
            return {"status": "FAILED", "error": flow_capability_check["error"]}
        queue_system_event(
            db=db, event_type="capability.allowed", user_id=user_id,
            trace_id=correlation_id, source="agent",
            payload={"run_id": str(run_id), "capability": "execute_flow"}, required=True,
        )

        # ── Split the plan at WAIT boundaries, run the first segment ───────────────
        try:
            segments = split_agent_plan(plan)
        except ValueError as exc:
            _fail_run(str(exc))
            return {"status": "FAILED", "error": str(exc)}

        total_tool_steps = sum(len(s["tool_steps"]) for s in segments)
        return _execute_agent_segment_chain(
            run_id=run_id,
            segments=segments,
            segment_index=0,
            accumulated=[],
            user_id=user_id,
            db=db,
            correlation_id=correlation_id,
            scoped_token=scoped_token,
            total_tool_steps=total_tool_steps,
        )
    except Exception as exc:
        logger.warning("[NodusExecutionService] execute_agent_run_via_workflow failed: %s", exc)
        return {"status": "FAILED", "error": str(exc)}


def _extract_simulated_effects(flow_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the predicted ``simulated_effects`` out of a flow-backed nodus result.

    They ride ``nodus_execute_result`` (the execution summary) in the flow state,
    with a fallback to the ``data`` envelope. Empty for a non-simulate run.
    """
    if not isinstance(flow_result, dict):
        return []
    for bucket_key in ("state", "data"):
        bucket = flow_result.get(bucket_key)
        if not isinstance(bucket, dict):
            continue
        summary = bucket.get("nodus_execute_result")
        if isinstance(summary, dict) and summary.get("simulated_effects"):
            return list(summary["simulated_effects"])
        if bucket.get("simulated_effects"):
            return list(bucket["simulated_effects"])
    return []


def simulate_agent_run(
    *,
    run_id: str,
    plan: dict[str, Any],
    user_id: str,
    db: Session,
    execution_token: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """AGENT-HARDEN-4 (PR2) — run a plan in simulate mode; predicted-effect report.

    Splits the plan and runs every tool segment through the flow-backed nodus path
    with ``simulate=True`` (WAIT boundaries are ignored — the whole plan is
    previewed). No tool executes: each ``call_tool`` is shadowed, producing a
    predicted result + a ``would_write`` intent. The report ``{simulated, steps,
    simulated_effects, ...}`` is persisted under ``run.result["simulation"]`` for the
    apps ``AgentApprovalInbox`` **without changing the run's status** — this is a
    preview, not an execution. Returns the report.
    """
    from AINDY.db.models import AgentRun
    from AINDY.runtime.agent_plan_compiler import compile_agent_segment, split_agent_plan
    from AINDY.runtime.nodus_adapter import _db_run_id

    try:
        segments = split_agent_plan(plan)
    except ValueError as exc:
        return {"simulated": True, "error": str(exc), "steps": [], "simulated_effects": []}

    all_steps: list[dict[str, Any]] = []
    all_effects: list[dict[str, Any]] = []
    for index, seg in enumerate(segments):
        if not seg["tool_steps"]:
            continue
        compiled = compile_agent_segment(
            seg["tool_steps"], base_index=seg["base_index"], workflow_name=f"agent_sim_seg{index}"
        )
        script = compiled["source"] + f"\nrun_workflow({compiled['workflow_name']})\n"
        try:
            flow_result = run_nodus_script_via_flow(
                script=script,
                input_payload=compiled["input_payload"],
                error_policy="halt",
                db=db,
                user_id=user_id,
                workflow_type="nodus_agent_execution",
                trace_id=correlation_id,
                extra_initial_state={
                    "execution_token": execution_token,
                    "agent_run_id": str(run_id),
                    "simulate": True,
                },
            )
        except Exception as exc:
            logger.warning(
                "[NodusExecutionService] simulate segment %d failed for %s: %s", index, run_id, exc
            )
            continue
        all_effects.extend(_extract_simulated_effects(flow_result))
        output_state = format_nodus_flow_result(flow_result).get("output_state") or {}
        step_results, _ = reconstruct_agent_step_results(compiled["steps"], output_state)
        all_steps.extend(step_results)

    report = {
        "simulated": True,
        "steps": all_steps,
        "simulated_effects": all_effects,
        "steps_total": len(all_steps),
        "effects_total": len(all_effects),
    }

    # Persist for the approval inbox — never touch run status (this is a preview).
    run = db.query(AgentRun).filter(AgentRun.id == _db_run_id(run_id)).first()
    if run is not None:
        merged = dict(run.result or {})
        merged["simulation"] = report
        run.result = merged
        db.commit()
    return report


def execute_agent_run_via_nodus(
    *,
    run_id: str,
    plan: dict[str, Any],
    user_id: str,
    db: Session,
    correlation_id: str | None = None,
    execution_token: dict[str, Any] | None = None,
    capability_token: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Canonical Nodus/runtime entrypoint for agent execution.

    Agentics still relies on flow orchestration for retry and checkpoint
    semantics, but agent_runtime should enter that execution path through this
    runtime service instead of importing the adapter directly.

    Retry policy: no custom retry logic here — retry decisions are owned by
    the flow wrapper (flow_engine._FLOW_RETRY_POLICY) and the per-step adapter
    (_step_policy in nodus_adapter._execute_agent_step). This function executes
    exactly once per invocation; the flow engine controls whether it is retried.

    Backend selection (RTR-1 Phase 2c): when
    ``AINDY_AGENT_EXECUTION_BACKEND=nodus_vm`` the run is compiled to a native
    Nodus workflow and executed through the VM (``execute_agent_run_via_workflow``).
    The default (``agent_flow``) uses the static AGENT_FLOW Python DAG below.
    """
    import os

    if os.getenv("AINDY_AGENT_EXECUTION_BACKEND", "agent_flow").strip().lower() == "nodus_vm":
        return execute_agent_run_via_workflow(
            run_id=run_id,
            plan=plan,
            user_id=user_id,
            db=db,
            correlation_id=correlation_id,
            execution_token=execution_token,
            capability_token=capability_token,
        )

    from AINDY.runtime.nodus_adapter import NodusAgentAdapter

    adapter_entrypoint = NodusAgentAdapter.execute_with_flow
    if not getattr(adapter_entrypoint, "__aindy_compat_wrapper__", False):
        return adapter_entrypoint(
            run_id=run_id,
            plan=plan,
            user_id=user_id,
            db=db,
            correlation_id=correlation_id,
            execution_token=execution_token,
            capability_token=capability_token,
        )

    return execute_agent_flow_orchestration(
        run_id=run_id,
        plan=plan,
        user_id=user_id,
        db=db,
        correlation_id=correlation_id,
        execution_token=execution_token,
        capability_token=capability_token,
    )


def execute_nodus_runtime(
    *,
    db: Session,
    user_id: str,
    execution_unit_id: str,
    script: str | None = None,
    file_path: str | None = None,
    memory_context: dict[str, Any] | None = None,
    input_payload: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    allowed_operations: Optional[list[str]] = None,
    event_sink=None,
    max_execution_ms: Optional[int] = None,
    execution_token: dict[str, Any] | None = None,
    run_id: str | None = None,
    adapter_cls=None,
    context_cls=None,
    simulate: bool = False,
):
    """
    Canonical Nodus runtime entrypoint used by both route helpers and flow nodes.

    Legacy call sites may still shape the response differently, but actual VM
    execution should converge through this helper so adapter wiring, context
    injection, and file/script dispatch live in one place.

    simulate (AGENT-HARDEN-4): when True, the call_tool seam is shadowed — tools
    are not executed; each call records a predicted "would-write" intent returned
    in ``NodusExecutionResult.simulated_effects``.
    """
    if not script and not file_path:
        raise ValueError("Provide either script or file_path")

    parsed_user_id = parse_user_id(user_id)
    normalized_user_id = str(parsed_user_id) if parsed_user_id is not None else str(user_id)
    if adapter_cls is None or context_cls is None:
        from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext as _RuntimeExecutionContext
        from AINDY.runtime.nodus_runtime_adapter import NodusRuntimeAdapter as _RuntimeAdapter

        adapter_cls = adapter_cls or _RuntimeAdapter
        context_cls = context_cls or _RuntimeExecutionContext

    execution_context = context_cls(
        user_id=normalized_user_id,
        execution_unit_id=execution_unit_id,
        memory_context=memory_context or {},
        input_payload=input_payload or {},
        state=state or {},
        allowed_operations=allowed_operations,
        event_sink=event_sink,
        max_execution_ms=max_execution_ms,
        run_id=str(run_id or execution_unit_id or ""),
        execution_token=execution_token,
        simulate=bool(simulate),
    )
    adapter = adapter_cls(db=db)
    if script is not None:
        return adapter.run_script(script, execution_context)
    return adapter.run_file(file_path, execution_context)


def execute_nodus_task_payload(
    *,
    task_name: str,
    task_code: str,
    db: Session,
    user_id: str,
    session_tags: Optional[list[str]] = None,
    allowed_operations: Optional[list[str]] = None,
    execution_id: Optional[str] = None,
    capability_token: Optional[dict] = None,
    logger=None,
) -> dict[str, Any]:
    normalized_user_id = str(require_user_id(user_id))
    operation_name = task_name
    eu_id = execution_id or f"memory.nodus.{task_name}"

    # Gate: ensure a DB-backed ExecutionUnit exists BEFORE the VM starts so the
    # run is always recoverable even if the process dies mid-execution.
    _pre_eu = None
    try:
        from AINDY.core.execution_gate import require_execution_unit as _require_eu
        _pre_eu = _require_eu(
            db=db,
            eu_type="job",
            user_id=normalized_user_id,
            source_type="memory_nodus_execute",
            source_id=eu_id,
            correlation_id=eu_id,
            extra={"task_name": task_name, "workflow_type": "memory_nodus_execute"},
        )
    except Exception:
        pass  # EU gate is non-fatal; execution proceeds regardless

    try:
        security_context = authorize_nodus_execution(
            task_code=task_code,
            allowed_operations=allowed_operations,
            capability_token=capability_token,
            execution_id=execution_id,
            user_id=normalized_user_id,
        )

        from AINDY.nodus.runtime.embedding import NodusRuntime  # noqa: F401

        from AINDY.db.dao.memory_node_dao import MemoryNodeDAO
        from AINDY.runtime.memory import MemoryOrchestrator
        from AINDY.runtime.memory.memory_feedback import MemoryFeedbackEngine
        from AINDY.memory.bridge import create_memory_node

        orchestrator = MemoryOrchestrator(MemoryNodeDAO)
        feedback_engine = MemoryFeedbackEngine()

        memory_context = orchestrator.get_context(
            user_id=normalized_user_id,
            query=task_name or "",
            operation_type="nodus_execution",
            db=db,
            max_tokens=800,
            metadata={
                "tags": session_tags or [],
                "node_types": [],
                "limit": 3,
            },
        )

        nodus_result = execute_nodus_runtime(
            db=db,
            user_id=normalized_user_id,
            execution_unit_id=eu_id,
            script=task_code,
            memory_context=memory_context.formatted,
            input_payload={
                "task_name": task_name,
                "memory_ids": memory_context.ids,
                "allowed_operations": security_context["allowed_operations"],
                "required_capabilities": security_context["required_capabilities"],
                "restricted_operations": security_context["restricted_operations"],
            },
            state={
                "memory_ids": memory_context.ids,
                "allowed_operations": security_context["allowed_operations"],
            },
            allowed_operations=security_context["allowed_operations"],
            adapter_cls=NodusRuntimeAdapter,
            context_cls=NodusExecutionContext,
        )
        try:
            if _pre_eu is not None:
                from AINDY.core.execution_unit_service import ExecutionUnitService
                ExecutionUnitService(db).update_status(
                    _pre_eu.id,
                    "completed" if nodus_result.status == "success" else "failed",
                )
        except Exception:
            pass

        summary = build_nodus_execution_summary(nodus_result)
        result = build_nodus_execution_record(
            flow_status="executed" if nodus_result.status == "success" else "failed",
            trace_id=eu_id,
            run_id=eu_id,
            nodus_summary=summary,
            nodus_status=nodus_result.status,
            output_state=nodus_result.output_state,
            events=nodus_result.emitted_events,
            memory_writes=nodus_result.memory_writes,
            error=nodus_result.error,
        )
        result["ok"] = nodus_result.status == "success"
        result["allowed_operations"] = security_context["allowed_operations"]

        try:
            result_preview = result.get("output_state") or result.get("error") or result.get("status")
            create_memory_node(
                content=f"Nodus operation '{operation_name}' executed: {str(result_preview)[:500]}",
                source="nodus_task",
                tags=(session_tags or []) + ["nodus", "task_execution"],
                user_id=normalized_user_id,
                db=db,
                node_type="outcome",
            )
        except Exception as exc:
            if logger:
                logger.warning(
                    "nodus_memory_capture_failed operation=%s user=%s: %s",
                    operation_name,
                    normalized_user_id,
                    exc,
                )

        try:
            success_score = 1.0 if result.get("ok") else 0.0
            feedback_engine.record_usage(
                memory_ids=memory_context.ids,
                success_score=success_score,
                db=db,
            )
        except Exception as exc:
            if logger:
                logger.warning(
                    "nodus_feedback_failed operation=%s user=%s memory_ids=%s: %s",
                    operation_name,
                    normalized_user_id,
                    memory_context.ids,
                    exc,
                )

        return {
            "task_name": task_name,
            "status": "executed" if result.get("ok") else "failed",
            "memory_bridge": "restricted",
            "session_tags": session_tags,
            "allowed_operations": security_context["allowed_operations"],
            "required_capabilities": security_context["required_capabilities"],
            "restricted_operations": security_context["restricted_operations"],
            "result": result,
        }

    except ImportError as exc:
        return {
            "task_name": task_name,
            "status": "bridge_ready",
            "message": (
                "Nodus runtime unavailable. Run: pip install -r AINDY/requirements.txt "
                "to enable Nodus script execution. Memory Bridge is available for direct API calls."
            ),
            "detail": str(exc),
            "allowed_operations": allowed_operations or sorted(ALLOWED_OPERATION_CAPABILITIES.keys()),
            "available_operations": [
                "POST /memory/recall/v3",
                "POST /memory/suggest",
                "POST /memory/nodes/{id}/feedback",
            ],
        }
    except NodusSecurityError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "nodus_security_violation",
                "message": str(exc),
            },
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "nodus_execute_failed", "message": "Operation execution failed", "details": str(exc)},
        ) from exc

