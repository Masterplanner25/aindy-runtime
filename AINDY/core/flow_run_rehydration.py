"""
FlowRun WAIT-state rehydration — startup recovery for the SchedulerEngine.

PersistentFlowRunner instances are ephemeral: a server restart destroys
every in-memory runner.  Any FlowRun with status="waiting" becomes
permanently stuck — the resume event fires, SchedulerEngine finds no
callback registered for that run_id, and the run stays in WAIT forever.

This module reconstructs PersistentFlowRunner callbacks for every waiting
FlowRun and re-registers them with SchedulerEngine on startup, so the
resume path is intact when the event arrives.

One callback per run, and it owns the run's execution unit
-----------------------------------------------------------
The callback built here (`build_flow_resume_callback`) does three things in order: an atomic
claim of the FlowRun (``waiting → executing``, exactly one instance wins), the run's
execution-unit transition (``waiting → resumed → executing``), then ``PersistentFlowRunner
.resume()`` — all on one session the runner commits. It is the ONLY writer of that unit's
status on resume.

★ Until 2026-09-16 a second module, `wait_rehydration.rehydrate_waiting_eus`, registered a
SECOND scheduler entry per parked run, keyed by the unit id, with a callback that moved the
unit's status on a session it closed without committing — a rollback on every fire. This
docstring called the pair "complementary … removing either would leave the execution in a
broken half-state". That was false: the unit-keyed entry never wrote anything durable, and the
flow callback's step 2 already did the work. Removed (`EU-WAIT-SIGNAL-DEAD-1`'s follow-up);
`test_flow_rehydration_owns_the_unit.py` pins that a restart registers exactly ONE entry per
parked run and that the unit's transition survives the callback's session.

Idempotency
-----------
``scheduler.waiting_for(run_id)`` is checked before each registration, so a second call to this
function skips runs already registered. Safe to call multiple times.

Scope
-----
Event-type waits are the canonical path (``waiting_for`` field).  Time-based
waits are also supported via ``state["trigger_at"]`` / ``state["wait_until"]``
for future time-based WAIT nodes.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable

# Module-level imports keep these patchable in tests via
# "core.flow_run_rehydration.<name>".
from AINDY.core.wait_condition import WaitCondition, _parse_utc_datetime
from AINDY.core.wait_rehydration import ensure_waiting_flow_run_row
from AINDY.kernel.scheduler_engine import get_scheduler_engine

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def derive_wait_condition_from_flow(flow_run) -> "WaitCondition | None":
    """Derive a scheduler-compatible WaitCondition from a FlowRun's persisted fields.

    Priority
    --------
    1. ``waiting_for`` (event name string) → ``WaitCondition.for_event``
       This covers all flow-engine WAIT registrations; it is the canonical field.
    2. ``state["trigger_at"]`` or ``state["wait_until"]`` → ``WaitCondition.for_time``
       Fallback for future time-based WAIT nodes that store a trigger datetime in state.
    3. Returns ``None`` if neither condition is detectable — the caller must skip the run.

    Args:
        flow_run: A ``FlowRun`` ORM instance (or any object with matching attributes).

    Returns:
        A ``WaitCondition``, or ``None`` if the condition cannot be determined.
    """
    run_id = str(getattr(flow_run, "id", "") or "")
    correlation_id = str(flow_run.trace_id or run_id) if run_id else None

    # ── 1. Event-based (canonical path) ──────────────────────────────────────
    waiting_for = getattr(flow_run, "waiting_for", None)
    if waiting_for:
        return WaitCondition.for_event(waiting_for, correlation_id=correlation_id)

    # ── 2. Time-based (state fallback) ───────────────────────────────────────
    state = getattr(flow_run, "state", None) or {}
    if isinstance(state, dict):
        raw_trigger = state.get("trigger_at") or state.get("wait_until")
        if raw_trigger:
            trigger_at = _parse_utc_datetime(raw_trigger)
            if trigger_at is not None:
                return WaitCondition.for_time(trigger_at, correlation_id=correlation_id)
            logger.warning(
                "[flow_rehydrate] run=%s has state trigger but value is unparseable: %r",
                run_id,
                raw_trigger,
            )

    return None


def _reregister_wait(r_id: str, *, flow_name: str, user_id, workflow_type: str, eid: str) -> None:
    """Re-arm a wait whose callback could not run here (FR-31 ask 2). Reads the run's own row for
    the event and correlation, builds the same callback, registers it under the run id."""
    try:
        from AINDY.core.wait_condition import WaitCondition
        from AINDY.db.database import SessionLocal
        from AINDY.db.models.flow_run import FlowRun
        from AINDY.kernel.scheduler_engine import get_scheduler_engine

        db = SessionLocal()
        try:
            run = db.query(FlowRun).filter(FlowRun.id == str(r_id)).first()
            if run is None or run.status != "waiting" or not run.waiting_for:
                return
            event = str(run.waiting_for)
            corr = str(run.trace_id or run.id)
        finally:
            db.close()
        get_scheduler_engine().register_wait(
            run_id=str(r_id),
            wait_for_event=event,
            tenant_id=str(user_id or ""),
            eu_id=str(eid or ""),
            resume_callback=build_flow_resume_callback(
                r_id=str(r_id), flow_name=flow_name, user_id=user_id, workflow_type=workflow_type, eid=eid
            ),
            correlation_id=corr,
            trace_id=corr,
            eu_type="flow",
            wait_condition=WaitCondition.for_event(event, correlation_id=corr),
        )
    except Exception as exc:  # noqa: BLE001 — never let re-arming fail the caller
        logger.warning("[flow_rehydrate] could not re-register the wait for run=%s: %s", r_id, exc)


def build_flow_resume_callback(
    *,
    r_id: str,
    flow_name: str,
    user_id,
    workflow_type: str,
    eid: str,
):
    """Build the 0-arg flow resume closure, shared by every path that resumes a run.

    Mirrors ``_build_agent_resume_callback`` deliberately — same contract, same
    guarantee, so the two halves of the runtime cannot drift apart again. On fire it
    opens its own ``SessionLocal`` and does an **atomic claim**
    (``UPDATE flow_runs SET status='executing' WHERE id=? AND status='waiting'``), so
    exactly one caller proceeds across a duplicate event fire, a second rehydration, or
    multiple instances.

    ★ **The closure captures only plain values, never a live DB session, runner or
    request.** That is what makes a resume reconstructible from identifiers rather than
    carried as state — and therefore what lets one cross a process boundary at all. Every
    argument here derives from the ``FlowRun`` row, so ``resume_reconstruction`` can rebuild
    the call from ``run_id`` alone. Do not add a parameter that cannot be read back off that
    row; doing so silently makes a run un-resumable anywhere but the process that registered
    it, which is `FR-15`'s remaining half.
    """
    def _callback() -> None:
        from AINDY.db.database import SessionLocal
        from AINDY.runtime.flow_engine import FLOW_REGISTRY, PersistentFlowRunner

        flow = FLOW_REGISTRY.get(flow_name)
        if flow is None:
            # FR-31 — the runtime's own dynamic flows were registered lazily (`nodus_execute`)
            # or resolvable only for resume (`agent_execution`), so a rehydrated resume found
            # nothing here on a fresh boot. `nodus_execute` is registered at boot now; this is
            # the belt for a process that skipped boot, and the ONLY way `agent_execution` is
            # resolved. Only a flow the process genuinely does not hold falls through.
            try:
                from AINDY.runtime.nodus_execution_service import (
                    ensure_runtime_flows_registered,
                    resolve_resumable_flow,
                )

                ensure_runtime_flows_registered()
                flow = resolve_resumable_flow(flow_name)
            except Exception:  # pragma: no cover - a failed ensure is the miss below
                flow = None
        if flow is None:
            # ★ FR-31 ask 2 — a skipped resume must NOT consume the run's registration. The
            # scheduler deleted this wait before dispatching us, so without re-registering, a
            # second resume finds nothing and the run is orphaned until the next boot. Put the
            # wait back (same callback), so the next wake — after the flow's plugin loads, or
            # on another instance — can succeed.
            logger.warning(
                "[flow_rehydrate] resume callback: flow=%r not in FLOW_REGISTRY for run=%s — "
                "this process cannot resume it; the wait is RE-REGISTERED so a later wake can",
                flow_name,
                r_id,
            )
            _reregister_wait(r_id, flow_name=flow_name, user_id=user_id, workflow_type=workflow_type, eid=eid)
            return

        _db = SessionLocal()
        try:
            # ── Step 1: FlowRun atomic claim ──────────────────────────
            # UPDATE WHERE status='waiting' ensures exactly one instance
            # proceeds.  All others see rowcount=0 and exit immediately.
            from AINDY.db.models.flow_run import FlowRun as _FlowRun
            from AINDY.kernel.condition_codes import FlowRunStatus

            claimed = (
                _db.query(_FlowRun)
                .filter(
                    _FlowRun.id == r_id,
                    _FlowRun.status == FlowRunStatus.WAITING.value,
                )
                .update(
                    {"status": FlowRunStatus.EXECUTING.value},
                    synchronize_session=False,
                )
            )
            try:
                _db.commit()
            except Exception as _claim_exc:
                logger.warning(
                    "[flow_rehydrate] claim commit failed for run=%s: %s",
                    r_id, _claim_exc,
                )
                try:
                    _db.rollback()
                except Exception:
                    pass
                return  # concurrency commit failure — skip safely

            if claimed == 0:
                logger.info(
                    "[flow_rehydrate] run=%s already claimed by another instance "
                    "— skipping EU resume and flow execution",
                    r_id,
                )
                return

            # ── Step 2: EU status transition ──────────────────────────
            # Only reached by the instance that won the claim.
            # EU idempotency guard in ExecutionUnitService prevents any
            # double-transition if this is somehow called twice.
            if eid:
                try:
                    from AINDY.core.execution_unit_service import ExecutionUnitService
                    ExecutionUnitService(_db).resume_execution_unit(eid)
                except Exception as _eu_exc:
                    logger.warning(
                        "[flow_rehydrate] EU resume failed for eu=%s run=%s "
                        "(non-fatal, flow execution proceeds): %s",
                        eid, r_id, _eu_exc,
                    )

            # ── Step 3: Flow execution ────────────────────────────────
            # FlowRun status is now "executing"; runner.resume()'s
            # internal claim guard is bypassed (status != "waiting").
            runner = PersistentFlowRunner(
                flow=flow,
                db=_db,
                user_id=user_id,
                workflow_type=workflow_type,
            )
            runner.resume(r_id)

        except Exception as _exc:
            logger.warning(
                "[flow_rehydrate] resume callback failed for run=%s: %s",
                r_id,
                _exc,
            )
        finally:
            _db.close()

    return _callback


def rehydrate_waiting_flow_runs(
    db: "Session",
    run_ids: Iterable[str] | None = None,
) -> int:
    """Re-register all waiting FlowRuns with the SchedulerEngine.

    Args:
        db: An active SQLAlchemy session.  Closed by the caller — do NOT
            close it inside this function.

    Returns:
        Number of FlowRuns successfully re-registered.
    """
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.db.models.execution_unit import ExecutionUnit
    from AINDY.kernel.condition_codes import FlowRunStatus

    scheduler = get_scheduler_engine()

    # ── 1. Query all waiting FlowRuns ──────────────────────────────────────────
    scoped_run_ids = {str(run_id) for run_id in (run_ids or []) if run_id}
    try:
        waiting_query = db.query(FlowRun).filter(FlowRun.status == FlowRunStatus.WAITING.value)
        if scoped_run_ids:
            waiting_query = waiting_query.filter(FlowRun.id.in_(scoped_run_ids))
        waiting_runs = waiting_query.all()
    except Exception as exc:
        logger.warning(
            "[flow_rehydrate] Could not query waiting FlowRuns (non-fatal): %s", exc
        )
        return 0

    if not waiting_runs:
        logger.info("[flow_rehydrate] No waiting FlowRuns found — nothing to rehydrate.")
        return 0

    logger.info(
        "[flow_rehydrate] Found %d waiting FlowRun(s) — rehydrating...", len(waiting_runs)
    )

    # ── 2. Pre-load associated EUs for priority and eu_id context ──────────────
    # A single bulk query avoids N+1. EU context is optional — proceed without
    # it if the query fails or the EU has been removed.
    run_ids = [str(r.id) for r in waiting_runs]
    eu_by_run_id: dict[str, object] = {}  # str(flow_run_id) → ExecutionUnit
    try:
        eus = (
            db.query(ExecutionUnit)
            .filter(ExecutionUnit.flow_run_id.in_(run_ids))
            .all()
        )
        for eu in eus:
            if eu.flow_run_id and eu.flow_run_id not in eu_by_run_id:
                eu_by_run_id[eu.flow_run_id] = eu
    except Exception as exc:
        logger.debug(
            "[flow_rehydrate] EU pre-load skipped (continuing without EU context): %s", exc
        )

    rehydrated = 0
    skipped = 0

    for run in waiting_runs:
        run_id = str(run.id)

        # ── EU context (resolved early — needed by both guards below) ─────────
        # Best-effort: EU may not exist if it was cleaned up or never created.
        eu = eu_by_run_id.get(run_id)
        eu_id: str = str(eu.id) if eu else ""
        priority: str = (eu.priority if eu else None) or "normal"

        # ── Guard 1: FlowRun-level callback already registered ────────────────
        # Covers: second call to this function, or any other code path that
        # registered under flow_run.id before us.  Dict assignment in
        # register_wait() would overwrite silently — skip to avoid redundant
        # log noise and unnecessary callback recreation.
        if scheduler.waiting_for(run_id) is not None:
            logger.debug(
                "[flow_rehydrate] run=%s already has FlowRun-level callback "
                "in scheduler registry — skipped",
                run_id,
            )
            skipped += 1
            continue


        # ── Derive wait condition (event or time-based) ───────────────────────
        wait_condition = derive_wait_condition_from_flow(run)
        if wait_condition is None:
            logger.warning(
                "[flow_rehydrate] run=%s has status=waiting but no resolvable wait "
                "condition — cannot rehydrate (manual intervention required)",
                run_id,
            )
            skipped += 1
            continue

        # ── Registration parameters ────────────────────────────────────────────
        tenant_id = str(run.user_id or "system")
        correlation_id = run.trace_id or run_id

        # ── Resume callback ────────────────────────────────────────────────────
        # The closure must capture all values needed at callback time.
        # The startup `db` will be closed long before any event fires — the
        # callback opens its own session via SessionLocal().
        #
        # Execution ordering guarantee
        # ----------------------------
        # 1. FlowRun atomic claim  (UPDATE WHERE status='waiting')
        # 2. EU status transition  (waiting → resumed → executing) — only if claim won
        # 3. Flow execution        (PersistentFlowRunner.resume)    — only if claim won
        #
        # The claim is the single gatekeeper.  If another instance wins the
        # claim (rowcount=0), both EU resume and flow execution are skipped
        # entirely — no bookkeeping side-effects on the losing instance.
        # PersistentFlowRunner.resume() also carries an internal claim guard
        # as a last-line safety net; when called from here, it naturally
        # bypasses that guard because status is already "executing".

        # ── Register with SchedulerEngine ──────────────────────────────────────
        # For time-based waits, wait_for_event is None; the scheduler uses
        # wait_condition.trigger_at instead.  For event-based waits it is the
        # event name string.
        wait_for_event = wait_condition.event_name  # None for time-based
        try:
            scheduler.register_wait(
                run_id=run_id,
                wait_for_event=wait_for_event,
                tenant_id=tenant_id,
                eu_id=eu_id,
                resume_callback=build_flow_resume_callback(
                    r_id=run_id,
                    flow_name=run.flow_name,
                    user_id=run.user_id,
                    workflow_type=run.workflow_type or "flow",
                    eid=eu_id,
                ),
                priority=priority,
                correlation_id=correlation_id,
                trace_id=run.trace_id,
                eu_type="flow",
                wait_condition=wait_condition,
            )
            ensure_waiting_flow_run_row(
                db,
                run_id=run_id,
                event_type=wait_for_event or "__time_wait__",
                correlation_id=correlation_id,
                timeout_at=getattr(run, "wait_deadline", None),
                eu_id=eu_id or None,
                priority=priority,
            )
            rehydrated += 1
            logger.info(
                "[flow_rehydrate] registered run=%s flow=%r condition=%s/%r",
                run_id,
                run.flow_name,
                wait_condition.type,
                wait_condition.event_name or wait_condition.trigger_at,
            )
        except Exception as exc:
            logger.warning(
                "[flow_rehydrate] Failed to register run=%s: %s", run_id, exc
            )
            skipped += 1

    logger.info(
        "[flow_rehydrate] complete — registered=%d skipped=%d total=%d",
        rehydrated,
        skipped,
        len(waiting_runs),
    )
    return rehydrated
