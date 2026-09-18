from AINDY.core.execution_pipeline.context import _route_eu_type
from AINDY.core.execution_pipeline.shared import logger


def _safe_require_eu(self, ctx) -> str | None:
    db = ctx.metadata.get("db")
    if db is None or not ctx.user_id:
        return None
    try:
        from AINDY.core.execution_gate import require_execution_unit

        eu = require_execution_unit(
            db=db,
            eu_type=_route_eu_type(ctx.route_name),
            user_id=str(ctx.user_id),
            source_type="route",
            source_id=ctx.request_id,
            correlation_id=ctx.request_id,
            extra={"route_name": ctx.route_name, "workflow_type": ctx.route_name},
        )
        eu_id = str(eu.id) if eu is not None else None
        if not eu_id:
            self._record_side_effect(
                ctx,
                "execution_unit.create",
                status="missing",
                required=True,
                error="require_execution_unit returned no execution unit",
            )
            return None
        ctx.metadata["eu_id"] = eu_id
        # EVENT-OUTBOX-1 (DEC-062) — commit the unit where it is created. `require_execution_unit`
        # only FLUSHES the row; before this it rode whichever commit came next (the handler's, or
        # the `execution.completed` / `.failed` emit's). Now the pipeline ROLLS BACK the request
        # session when the handler raises, so the row must already be durable or the finalize
        # that follows would find nothing to finalise. At this point the session holds only the
        # started event (already committed) and this row.
        try:
            db.commit()
        except Exception as commit_exc:  # noqa: BLE001 — recorded, not fatal
            self._record_side_effect(
                ctx,
                "execution_unit.create",
                status="failed",
                required=True,
                error=f"commit failed: {commit_exc}",
            )
            return eu_id
        self._record_side_effect(
            ctx,
            "execution_unit.create",
            status="ok",
            required=True,
        )
        logger.debug(
            "[Pipeline] EU registered route=%s eu_id=%s trace_id=%s",
            ctx.route_name,
            eu_id,
            ctx.request_id,
        )
        return eu_id
    except Exception as exc:
        self._record_side_effect(
            ctx,
            "execution_unit.create",
            status="failed",
            required=True,
            error=exc,
        )
        logger.warning("execution.eu_register_skipped", exc_info=True)
        return None


def _safe_check_quota(self, ctx, started_event_id: str | None = None) -> bool:
    eu_id = ctx.metadata.get("eu_id")
    if not eu_id or not ctx.user_id:
        return True
    try:
        from AINDY.kernel.resource_manager import get_resource_manager

        rm = get_resource_manager()
        ok, reason = rm.can_execute(str(ctx.user_id), eu_id)
        if not ok:
            self._safe_emit_event(
                ctx,
                event_type="execution.failed",
                parent_event_id=started_event_id,
                payload={"route_name": ctx.route_name, "detail": reason or "quota_exceeded"},
            )
            self._safe_finalize_eu(ctx, "failed")
            self._record_side_effect(
                ctx,
                "quota_check",
                status="quota_exceeded",
                required=False,
                error=reason,
            )
            return False
        return True
    except Exception:
        logger.warning("execution.quota_check_failed (fail open)", exc_info=True)
        return True


def _safe_rm_mark_started(self, ctx) -> None:
    eu_id = ctx.metadata.get("eu_id")
    if not eu_id or not ctx.user_id:
        return
    try:
        from AINDY.kernel.resource_manager import get_resource_manager

        get_resource_manager().mark_started(str(ctx.user_id), eu_id)
    except Exception:
        logger.warning("execution.rm_mark_started_failed (non-fatal)", exc_info=True)


def _safe_rm_mark_completed(self, ctx) -> None:
    eu_id = ctx.metadata.get("eu_id")
    if not eu_id or not ctx.user_id:
        return
    try:
        from AINDY.kernel.resource_manager import get_resource_manager

        get_resource_manager().mark_completed(str(ctx.user_id), eu_id)
    except Exception:
        logger.warning("execution.rm_mark_completed_failed (non-fatal)", exc_info=True)


def _safe_rm_record_and_complete(self, ctx, duration_ms: float) -> None:
    eu_id = ctx.metadata.get("eu_id")
    if not eu_id or not ctx.user_id:
        return
    try:
        from AINDY.kernel.resource_manager import get_resource_manager

        rm = get_resource_manager()
        rm.record_usage(eu_id, {"wall_time_ms": int(duration_ms)})
        rm.mark_completed(str(ctx.user_id), eu_id)
    except Exception:
        logger.warning("execution.rm_record_and_complete_failed (non-fatal)", exc_info=True)


def _safe_rollback_handler_work(self, ctx) -> None:
    """EVENT-OUTBOX-1 (DEC-062) — a handler that raised leaves NO record of work that did not
    happen. Roll the request session back BEFORE the `execution.failed` emit, which otherwise
    commits the request session and lands the handler's pending writes — and the events queued
    on it — as a side effect (`_persist_system_event`'s own comment says it wants to avoid
    exactly that, and then commits two lines later). The unit row survives: it is committed at
    creation (`_safe_require_eu`); the failure event and the finalize are written after this.

    ★ Only when the HANDLER raised. Once the handler has returned its work stands, whatever the
    post-handler machinery does next — a crashed signal flush is the window this entry closes,
    not a reason to undo the work.
    """
    db = ctx.metadata.get("db")
    if db is None or ctx.metadata.get("handler_returned"):
        return
    try:
        db.rollback()
        self._record_side_effect(ctx, "handler.rollback", status="ok", required=False)
    except Exception as exc:  # noqa: BLE001 — recorded; the failure path continues
        self._record_side_effect(ctx, "handler.rollback", status="failed", required=False, error=str(exc))


def _safe_finalize_eu(self, ctx, status: str) -> None:
    if ctx.metadata.get("eu_finalized"):
        return
    eu_id = ctx.metadata.get("eu_id")
    if not eu_id:
        return
    db = ctx.metadata.get("db")
    if db is None:
        return
    ctx.metadata["eu_finalized"] = True
    try:
        from AINDY.core.execution_unit_service import ExecutionUnitService

        if not ExecutionUnitService(db).update_status(eu_id, status):
            self._record_side_effect(
                ctx,
                f"execution_unit.finalize.{status}",
                status="failed",
                required=True,
                error=f"failed to persist status {status!r}",
            )
            return
        # ★ FR-30 (2026-09-15): `update_status` only FLUSHES, and this is the LAST write on the
        # request session — the `execution.completed` / `execution.failed` emit before it
        # committed, and `get_db` tears down with `close()`, no commit. So the terminal status
        # was rolled back on every request since the table existed: every route unit on a live
        # stack sat `executing` forever (196 agent / 255 default / 373 flow / 39 job / 52 task
        # rows on the app's stack, none since 2026-07-23 ever `completed`). Nothing logged it,
        # because nothing failed — it was undone. Commit here, where the status is written.
        db.commit()
        self._record_side_effect(
            ctx,
            f"execution_unit.finalize.{status}",
            status="ok",
            required=True,
        )
        logger.debug("[Pipeline] EU finalised eu_id=%s status=%s", eu_id, status)
    except Exception as exc:
        self._record_side_effect(
            ctx,
            f"execution_unit.finalize.{status}",
            status="failed",
            required=True,
            error=exc,
        )
        logger.warning("execution.eu_finalize_skipped", exc_info=True)
