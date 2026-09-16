from AINDY.runtime.flow_engine.serialization import _json_safe
from AINDY.runtime.flow_engine.shared import Session, logger, parse_user_id


def route_event(
    event_type: str,
    payload: dict,
    db: Session,
    user_id: str = None,
    run_id: str | None = None,
) -> list[dict]:
    """Deliver *payload* to waiting run(s) on *event_type* and wake them.

    ★ **With ``run_id`` this is a PER-RUN resume: the payload is injected into that run only and
    the wake is scoped to it end to end** (local scan, Redis broadcast, cross-instance fallback).
    `RESUME-FANOUT-UNSCOPED-1`: `POST …/runs/{A}/resume` checked that A belonged to the caller
    and was waiting on the event, then called this with no run id — and this injected the
    payload into, and woke, EVERY run parked on that event name, any tenant (observed live:
    `results: [{run_id: <B>…}, {run_id: <A>…}]`). Event names are conventional strings
    (`review.approved`), so collisions are the normal case. The ownership check protected the
    path parameter; the effect ignored it.

    ★ Why not correlation alone: a flow WAIT's correlation is the run's `trace_id`, and a
    trace is shared by every run started under one request (or by a `flow.run` from inside a
    running flow), so two sibling runs waiting on one event have the same correlation. The run
    id is the only thing that is unique to the run.

    Without ``run_id`` this is the BROADCAST form — every matching wait, filtered only by the
    payload's ``correlation_id``. Nothing in the runtime calls that form today; it is kept for
    an explicit broadcast verb, which needs its own scope, not the per-run route's.

    ★ **WAIT-TYPED-CONTRACT-1 — the payload is CHECKED before it is injected.** A waiting node
    that declared ``resume_schema`` left a pending request on the run's state; the payload is
    validated against it here, with the dispatcher's own validator, BEFORE any row is written
    or any wake is published. Per-run form: a rejection raises `ResumePayloadRejected` and
    nothing happens — the run stays ``waiting``, its scheduler entry stays registered. Broadcast
    form: a rejecting run is skipped from injection (logged, counted); the wake still goes out
    by event name, which the run then sees as a payload-less wake — the semantics it already had
    for a bus emit. A run with no declaration is ``untyped`` and behaves exactly as before.
    Every outcome lands on ``aindy_flow_resume_payload_total{outcome}``.
    """
    from AINDY.core.pending_request import (
        OUTCOME_REJECTED,
        ResumePayloadRejected,
        check_resume_payload,
    )
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.kernel.scheduler_engine import get_scheduler_engine
    from AINDY.platform_layer.metrics import flow_resume_payload_total

    scheduler = get_scheduler_engine()
    corr = (payload or {}).get("correlation_id") or None
    results: list[dict] = []
    if run_id is not None:
        target = (
            db.query(FlowRun)
            .filter(FlowRun.id == str(run_id), FlowRun.status == "waiting")
            .first()
        )
        query_runs = [target] if target is not None else []
        # The wait was registered with correlation = the run's trace id (`runner_steps.py`
        # WAIT branch; `flow_run_rehydration.py` restores the same). Carry it so a wait
        # registered with a correlation is matched, not skipped — the run id is the filter.
        if target is not None and corr is None:
            corr = str(target.trace_id or target.id)
        if target is None:
            logger.debug(
                "[route_event] run=%s is not waiting - skipping injection", run_id
            )
    else:
        matching_run_ids = scheduler.peek_matching_run_ids(event_type, correlation_id=corr)
        if not matching_run_ids:
            logger.debug(
                "[route_event] no waiting runs matched event=%s corr=%s - skipping injection",
                event_type,
                corr,
            )
            query_runs = []
        else:
            query_runs = (
                db.query(FlowRun)
                .filter(FlowRun.id.in_(matching_run_ids), FlowRun.status == "waiting")
                .all()
            )

    # ── Check before touching anything ───────────────────────────────────────
    # Decided for EVERY run first, so a per-run rejection leaves no partial write behind, and
    # a broadcast skips exactly the runs whose declaration refuses this payload.
    admitted: list = []
    for run in query_runs:
        try:
            outcome = check_resume_payload(
                run.state, payload, run_id=str(run.id), event_type=event_type
            )
        except ResumePayloadRejected as rejected:
            flow_resume_payload_total.labels(outcome=OUTCOME_REJECTED).inc()
            logger.warning(
                "[route_event] payload REJECTED run=%s event=%s: %s",
                run.id,
                event_type,
                "; ".join(rejected.errors),
            )
            if run_id is not None:
                raise
            continue
        flow_resume_payload_total.labels(outcome=outcome).inc()
        admitted.append(run)

    for run in admitted:
        try:
            state = dict(run.state or {})
            state["event"] = payload
            run.state = _json_safe(state)
            db.flush()
            results.append({"run_id": str(run.id), "payload_injected": True})
            logger.debug(
                "[route_event] payload injected run=%s event=%s",
                run.id,
                event_type,
            )
        except Exception as exc:
            logger.warning("[route_event] payload inject failed run=%s: %s", run.id, exc)

    try:
        db.commit()
    except Exception as exc:
        logger.warning("[route_event] state commit failed event=%s: %s", event_type, exc)

    woken: int | None = None
    try:
        from AINDY.kernel.event_bus import publish_event

        woken = publish_event(event_type, correlation_id=corr, run_id=run_id)
        logger.info(
            "[route_event] publish_event resumed=%d event=%s corr=%s run=%s",
            woken,
            event_type,
            corr,
            run_id,
        )
    except Exception as exc:
        logger.warning("[route_event] publish_event failed event=%s: %s", event_type, exc)
    # FR-31 ask 3 — say whether anything was WOKEN, not only whether the payload was stored.
    # `payload_injected: true` with nothing registered to wake is exactly how a resume that would
    # never happen read as `resumed: true` on the wire. The payload is on the row regardless: the
    # next boot's rehydration re-registers the run and the next wake delivers it.
    if run_id is not None and results:
        for entry in results:
            entry["woken"] = bool(woken)
        if not woken:
            logger.warning(
                "[route_event] payload injected for run=%s but NO wait was registered to wake — "
                "the run is not being resumed by this call; it will be on the next wake after "
                "rehydration (event=%s)",
                run_id,
                event_type,
            )
    return results


def record_outcome(
    event_type: str,
    flow_name: str,
    success: bool,
    execution_time_ms: int = 0,
    user_id: str = None,
    workflow_type: str = None,
    metadata: dict = None,
    db: Session = None,
) -> None:
    if not db:
        return
    from AINDY.db.models.flow_run import EventOutcome

    try:
        outcome = EventOutcome(
            event_type=event_type,
            flow_name=flow_name,
            workflow_type=workflow_type,
            success=success,
            execution_time_ms=execution_time_ms,
            user_id=parse_user_id(user_id),
            event_metadata=metadata or {},
        )
        db.add(outcome)
        db.commit()
    except Exception as exc:
        logger.warning("record_outcome failed: %s", exc)
