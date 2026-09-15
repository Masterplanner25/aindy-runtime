from AINDY.core.execution_pipeline.context import _route_eu_type
from AINDY.core.execution_pipeline.shared import Any, logger



def _build_eu_resume_callback(eu_id: str):
    """A 0-arg EU resume that opens its own session.

    ★ Replaces `lambda: ExecutionUnitService(db).resume_execution_unit(eu_id)`, which captured
    the **request-scoped** session. `AGENT_WORKING_RULES` §5 — never share a SQLAlchemy session
    across threads or requests — and this closure did both: it is handed to the scheduler and
    fires on a scheduler thread, arbitrarily later, after the request that owned the session has
    returned and its session has been closed.

    It survived because a closed SQLAlchemy session is not a dead one — it transparently checks
    out a new connection on next use — so the bug was latent rather than visible. That is
    precisely the kind that stops being latent under concurrency, and `FR-15 (a)` made scheduler
    resumes concurrent.

    Capturing only `eu_id` also makes this reconstructible in the sense the rest of `FR-15`
    means: everything it needs is an identifier, so nothing about it is tied to the process or
    the request that registered it.
    """
    def _resume() -> None:
        from AINDY.core.execution_unit_service import ExecutionUnitService
        from AINDY.db.database import SessionLocal

        db = SessionLocal()
        try:
            ExecutionUnitService(db).resume_execution_unit(eu_id)
        finally:
            db.close()

    return _resume


def _detect_wait(self, result: Any) -> tuple[str, dict, Any] | None:
    """Only an explicit `ExecutionWaitSignal` parks the REQUEST's execution unit.

    ★ FR-29 / `WAIT-DETECT-SHAPE-1` (2026-09-14): this used to also treat any handler result
    dict whose ``status`` upper-cased to ``WAITING`` as the request itself waiting. Two things
    return such a dict, and neither is the request waiting:

    - a READ of a waiting run — `GET /platform/flows/runs/{id}` on an app-profile server, where
      a registered result key makes the handler's return the bare row and the row says
      ``status: "waiting"``. Eight reads parked eight readers' units, forever, each with a
      `waiting_flow_runs` FK violation because the scheduler was handed a unit id as a run id.
    - a START of something that suspends — `POST /platform/nodus/run`, whose execution record
      carries ``status: "WAITING"`` with ``waiting_for`` NESTED under ``data``. The old branch
      read neither key and parked the request's unit on the literal event ``"unknown"``, which
      nothing emits. The dict path parked units; it never once resumed one.

    A request's unit describes the request. When the handler returns, the request is done; what
    is waiting is the run it read or started, whose own `flow_runs` row and execution unit carry
    the wait (ACTIVE-COUNT-WAIT-LEAK-1). The handler's result is returned untouched — a caller
    still sees ``data.status == "WAITING"`` — it just no longer changes what the pipeline
    records about the request.
    """
    from AINDY.core.execution_gate import ExecutionWaitSignal

    if isinstance(result, ExecutionWaitSignal):
        return result.wait_for, result.payload, result.wait_condition
    return None


def _safe_transition_eu_waiting(self, ctx, *, wait_for: str, wait_condition=None) -> None:
    eu_id = ctx.metadata.get("eu_id")
    db = ctx.metadata.get("db")
    if not eu_id:
        raise RuntimeError(
            f"WAIT requires ExecutionUnit context - eu_id is absent "
            f"(route={ctx.route_name!r}, wait_for={wait_for!r}). "
            "Ensure the route has an authenticated user_id and a DB session "
            "so an ExecutionUnit can be created before entering WAIT."
        )
    if db is None:
        raise RuntimeError(
            f"WAIT requires ExecutionUnit context - db session is absent "
            f"(route={ctx.route_name!r}, eu_id={eu_id!r}, wait_for={wait_for!r}). "
            "Cannot persist waiting status without a database session."
        )

    try:
        from AINDY.core.execution_unit_service import ExecutionUnitService
        from AINDY.core.wait_condition import WaitCondition

        if wait_condition is None:
            trace_id = str(ctx.metadata.get("trace_id") or ctx.request_id)
            wait_condition = WaitCondition.for_event(wait_for, correlation_id=trace_id)

        eus = ExecutionUnitService(db)
        if not eus.update_status(eu_id, "waiting"):
            raise RuntimeError(f"failed to persist waiting status for eu_id={eu_id!r}")
        if not eus.set_wait_condition(eu_id, wait_condition):
            raise RuntimeError(f"failed to persist wait condition for eu_id={eu_id!r}")

        try:
            from AINDY.kernel.scheduler_engine import PRIORITY_NORMAL, get_scheduler_engine

            trace_id = str(ctx.metadata.get("trace_id") or ctx.request_id)
            get_scheduler_engine().register_wait(
                run_id=eu_id,
                wait_for_event=wait_for,
                tenant_id=str(ctx.user_id or ""),
                eu_id=eu_id,
                resume_callback=_build_eu_resume_callback(eu_id),
                priority=PRIORITY_NORMAL,
                correlation_id=trace_id,
                trace_id=trace_id,
                eu_type=_route_eu_type(ctx.route_name),
                wait_condition=wait_condition,
            )
            logger.debug(
                "[Pipeline] SchedulerEngine.register_wait eu=%s wait_for=%s cond_type=%s trace=%s",
                eu_id,
                wait_for,
                wait_condition.type,
                trace_id,
            )
        except Exception as exc:
            raise RuntimeError(
                f"failed to register resumable wait for eu_id={eu_id!r}"
            ) from exc

        self._record_side_effect(
            ctx,
            "execution_unit.wait",
            status="ok",
            required=True,
        )
        logger.info("[Pipeline] EU->waiting eu_id=%s wait_for=%s", eu_id, wait_for)
    except Exception as exc:
        self._record_side_effect(
            ctx,
            "execution_unit.wait",
            status="failed",
            required=True,
            error=exc,
        )
        logger.debug("execution.eu_transition_waiting_skipped", exc_info=True)
        raise
