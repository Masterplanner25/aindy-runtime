from AINDY.core.execution_pipeline.shared import Any, logger


def _requires_route_side_effects(self, ctx) -> bool:
    return ctx.metadata.get("db") is not None


def _record_side_effect(self, ctx, name: str, *, status: str, required: bool, error: Any = None) -> None:
    detail: dict[str, Any] = {"status": status, "required": bool(required)}
    if error is not None:
        detail["error"] = str(error)
    ctx.metadata.setdefault("side_effects", {})[name] = detail


def _safe_set_parent_event(self, parent_event_id: str | None) -> Any:
    if not parent_event_id:
        return None
    try:
        from AINDY.platform_layer.trace_context import set_parent_event_id

        return set_parent_event_id(parent_event_id)
    except Exception:
        logger.debug("execution.parent_event_set_skipped", exc_info=True)
        return None


def _safe_reset_parent_event(self, token: Any) -> None:
    if token is None:
        return
    try:
        from AINDY.platform_layer.trace_context import reset_parent_event_id

        reset_parent_event_id(token)
    except Exception:
        logger.debug("execution.parent_event_reset_skipped", exc_info=True)


def _set_event_refs(self, ctx, started_event_id: str | None, *, terminal_event_id: str | None, completed: bool) -> None:
    refs: list[dict[str, str]] = []
    if started_event_id:
        refs.append({"type": "execution.started", "id": str(started_event_id)})
    terminal_type = "execution.completed" if completed else "execution.failed"
    if terminal_event_id:
        refs.append({"type": terminal_type, "id": str(terminal_event_id)})
    ctx.metadata["event_refs"] = refs


def _handle_contract_violation(self, message: str) -> None:
    try:
        from AINDY.config import settings

        if settings.ENFORCE_EXECUTION_CONTRACT:
            raise RuntimeError(message)
    except Exception:
        raise
    logger.warning(message)


def _safe_set_pipeline_active(self) -> Any:
    try:
        from AINDY.platform_layer.trace_context import set_pipeline_active

        return set_pipeline_active(True)
    except Exception:
        logger.debug("execution.pipeline_active_set_skipped", exc_info=True)
        return None


def _safe_reset_pipeline_active(self, token: Any) -> None:
    if token is None:
        return
    try:
        from AINDY.platform_layer.trace_context import reset_pipeline_active

        reset_pipeline_active(token)
    except Exception:
        logger.debug("execution.pipeline_active_reset_skipped", exc_info=True)


def _safe_set_current_execution_context(self, ctx) -> Any:
    try:
        from AINDY.platform_layer.trace_context import set_current_execution_context

        return set_current_execution_context(ctx)
    except Exception:
        logger.debug("execution.current_ctx_set_skipped", exc_info=True)
        return None


def _safe_reset_current_execution_context(self, token: Any) -> None:
    if token is None:
        return
    try:
        from AINDY.platform_layer.trace_context import reset_current_execution_context

        reset_current_execution_context(token)
    except Exception:
        logger.debug("execution.current_ctx_reset_skipped", exc_info=True)


def _safe_bind_syscall_unit(self, ctx) -> Any:
    """Hand the dispatcher the unit this pipeline claimed (QUOTA-ACCRUAL-ORPHAN-1).

    The pipeline claims an ExecutionUnit, admits it, ``mark_started``s it and reaps it —
    and then, until this existed, never told ``SyscallDispatcher`` which unit that was. So
    every syscall a route dispatched minted its OWN unit, accrued its usage there, and the
    pipeline's reap cleared a snapshot no syscall had touched. Measured: five
    ``POST /platform/syscall`` calls left five orphan snapshots with ``tenant_id=""`` that
    survived the purge sweep, while the request's own unit read ``syscall_count: 0``.

    Setting the dispatcher's ContextVars for the handler's duration makes every dispatch
    inside the route NESTED under the request's unit — the same bridge ``worker_loop``
    already builds for a distributed job. Consequences, all intended: usage accrues on the
    unit that is reaped; ``check_quota`` guards a subject that owns the budget; the
    envelope's ``trace_id`` is the request's (FR-26 extended to the syscall envelope);
    and provenance written from ``context.execution_unit_id`` names a real unit.

    Only when BOTH ids exist. Binding a trace with no unit would make nested dispatches
    inherit ``""`` as their unit — the bucket-named-``""`` this entry was filed about.
    """
    eu_id = ctx.metadata.get("eu_id")
    trace_id = ctx.metadata.get("trace_id")
    if not eu_id or not trace_id:
        return None
    try:
        from AINDY.kernel.syscall_dispatcher import _EU_ID_CTX, _TRACE_ID_CTX

        return (_TRACE_ID_CTX.set(str(trace_id)), _EU_ID_CTX.set(str(eu_id)))
    except Exception:
        logger.debug("execution.syscall_unit_bind_skipped", exc_info=True)
        return None


def _safe_unbind_syscall_unit(self, tokens: Any) -> None:
    if not tokens:
        return
    try:
        from AINDY.kernel.syscall_dispatcher import _EU_ID_CTX, _TRACE_ID_CTX

        tok_trace, tok_eu = tokens
        _EU_ID_CTX.reset(tok_eu)
        _TRACE_ID_CTX.reset(tok_trace)
    except Exception:
        logger.debug("execution.syscall_unit_unbind_skipped", exc_info=True)


def _inject_execution_envelope(self, ctx, result, duration_ms: float):
    if not isinstance(result, dict):
        return result
    try:
        from AINDY.core.execution_gate import to_envelope

        result.setdefault(
            "execution_envelope",
            to_envelope(
                eu_id=ctx.metadata.get("eu_id"),
                trace_id=str(ctx.metadata.get("trace_id") or ctx.request_id),
                status="SUCCESS",
                output=None,
                error=None,
                duration_ms=duration_ms,
                attempt_count=1,
            ),
        )
    except Exception:
        logger.debug("execution.envelope_inject_skipped", exc_info=True)
    return result
