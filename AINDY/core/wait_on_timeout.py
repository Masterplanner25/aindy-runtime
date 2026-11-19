"""FR-55 (a), DEC-136 — a wait that resumes at its own deadline instead of failing.

A guest that calls ``await_event_for(event_type, schema, seconds)`` parks like ``await_event``.
The runner sets the run's ``wait_deadline`` from ``seconds`` and records the policy on the run's
state under :data:`WAIT_ON_TIMEOUT_KEY`. When the deadline passes, :func:`resume_due_deadline_waits`
(a leader-only scheduler job, every 15 s) delivers :func:`timeout_marker` through the same per-run
resume path a ``POST …/resume`` uses, and the resumed script gets the marker back from the same
call. Nothing here touches a plain ``await_event``: no record, so every scan treats it as before.

The three scans that FAIL a waiting run past a deadline (``expire_timed_out_waits``,
``expire_timed_out_wait_flows`` and the stuck-run watchdog) consult :func:`resumes_at_deadline` and
leave such a run to this job.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: run.state key — ``{"policy": "resume", "event": <wait_for>, "seconds": <int>}``.
WAIT_ON_TIMEOUT_KEY = "wait_on_timeout"
POLICY_RESUME = "resume"

MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 30 * 24 * 60 * 60  # 30 days


def validate_wait_seconds(seconds: Any) -> int:
    """Whole seconds in [1, 30 days], rounded up. Raises ``ValueError`` otherwise."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError(f"await_event_for() needs a number of seconds, got {seconds!r}")
    whole = int(seconds) if float(seconds).is_integer() else int(seconds) + 1
    if whole < MIN_WAIT_SECONDS or whole > MAX_WAIT_SECONDS:
        raise ValueError(
            f"await_event_for() seconds must be between {MIN_WAIT_SECONDS} and {MAX_WAIT_SECONDS}, got {seconds!r}"
        )
    return whole


def build_record(event: str, seconds: int) -> dict:
    return {"policy": POLICY_RESUME, "event": str(event), "seconds": int(seconds)}


def deadline_after(seconds: int, now: datetime | None = None) -> datetime:
    from AINDY.kernel.clock import utcnow

    return (now or utcnow()) + timedelta(seconds=int(seconds))


def resumes_at_deadline(flow_run: Any) -> bool:
    """True when this waiting run asked to be resumed, not failed, at its deadline.

    The record must name the event the run is waiting on NOW: a record left by an earlier wait
    never changes what happens to a later one.
    """
    state = getattr(flow_run, "state", None) or {}
    record = state.get(WAIT_ON_TIMEOUT_KEY) if isinstance(state, dict) else None
    return (
        isinstance(record, dict)
        and record.get("policy") == POLICY_RESUME
        and bool(getattr(flow_run, "waiting_for", None))
        and record.get("event") == getattr(flow_run, "waiting_for", None)
    )


def timeout_marker(deadline: datetime | None) -> dict:
    if deadline is not None and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return {"timed_out": True, "deadline": deadline.isoformat() if deadline else None}


def resume_due_deadline_waits(db, now: datetime | None = None) -> int:
    """Deliver the timeout marker to every waiting run whose resume deadline has passed.

    Returns how many runs were handed a marker or re-woken. Per run: a run that already holds an
    injected payload (a real event that won the race, or a marker delivered on an earlier tick
    whose wake found nothing registered, e.g. just after a restart) is only re-woken, never
    overwritten; the resume claim is a compare-and-set, so a second wake is harmless.
    """
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.kernel.event_bus import publish_event
    from AINDY.runtime.flow_engine import route_event

    from AINDY.kernel.clock import utcnow

    now = now or utcnow()
    due = (
        db.query(FlowRun)
        .filter(
            FlowRun.status == "waiting",
            FlowRun.wait_deadline.is_not(None),
            FlowRun.wait_deadline <= now,
        )
        .all()
    )
    handled = 0
    for run in due:
        if not resumes_at_deadline(run):
            continue
        run_id = str(run.id)
        event = str(run.waiting_for)
        try:
            if "event" in (run.state or {}):
                publish_event(event, correlation_id=str(run.trace_id or run_id), run_id=run_id)
            else:
                route_event(event, timeout_marker(run.wait_deadline), db, run_id=run_id, check_schema=False)
            handled += 1
        except Exception as exc:  # one run's failure must not stop the rest
            logger.warning("[wait_on_timeout] resume at deadline failed run=%s event=%s: %s", run_id, event, exc)
            try:
                db.rollback()
            except Exception:
                pass
    return handled
