"""FR-61 (DEC-123): give old outbound-call events the user of their trace, where that is certain.

Until FR-61 only the agent planner passed a user to ``perform_external_call``, so every other
``external.call.started`` / ``.completed`` / ``.failed`` event, and the ``error.external_call``
event beside a failure, was written with ``user_id`` NULL. FR-60 scopes a trace graph to the
caller's own events, so those events, and every edge through them, dropped out of the caller's
own traces. The app counted 26,452 such rows in 5,279 traces.

New events carry the caller (``external_call_service._calling_user``). This fills the old ones,
**only in a trace whose events all belong to one user**: a trace with no user, or with two, is
left alone, because there the call's owner is not known. FR-60's rule is not changed.

Operator-run, once, through ``aindy-runtime events backfill-call-users``. Dry-run by default.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

#: What ``perform_external_call`` writes.
EXTERNAL_CALL_EVENT_TYPES = (
    "external.call.started",
    "external.call.completed",
    "external.call.failed",
    "error.external_call",
)
BACKFILL_BATCH_SIZE = 500


def backfill_external_call_users(
    db: Session, *, dry_run: bool = True, batch_size: int = BACKFILL_BATCH_SIZE
) -> dict[str, Any]:
    """Fill ``user_id`` on outbound-call events with none, from the one user of their trace.

    Walks the affected traces in ``trace_id`` order, ``batch_size`` traces at a time, and commits
    after each batch. The walk moves past every trace it reads, so it ends however the data looks.
    Running it again finds nothing more to fill.
    """
    from AINDY.db.models.system_event import SystemEvent

    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    userless_call = (SystemEvent.user_id.is_(None), SystemEvent.type.in_(EXTERNAL_CALL_EVENT_TYPES))
    report: dict[str, Any] = {
        "traces_examined": 0,
        "would_fill": 0,
        "filled": 0,
        "skipped_no_user_traces": 0,
        "skipped_shared_traces": 0,
        "batches": 0,
        "dry_run": dry_run,
    }

    last_trace = None
    while True:
        query = (
            db.query(SystemEvent.trace_id)
            .filter(*userless_call, SystemEvent.trace_id.isnot(None))
            .distinct()
        )
        if last_trace is not None:
            query = query.filter(SystemEvent.trace_id > last_trace)
        traces = [row[0] for row in query.order_by(SystemEvent.trace_id).limit(batch_size).all()]
        if not traces:
            break
        last_trace = traces[-1]
        report["traces_examined"] += len(traces)

        owners: dict[str, set] = {trace: set() for trace in traces}
        for trace, user in (
            db.query(SystemEvent.trace_id, SystemEvent.user_id)
            .filter(SystemEvent.trace_id.in_(traces), SystemEvent.user_id.isnot(None))
            .distinct()
            .all()
        ):
            owners[trace].add(user)

        by_owner: dict[Any, list[str]] = {}
        for trace, users in owners.items():
            if not users:
                report["skipped_no_user_traces"] += 1
            elif len(users) > 1:
                report["skipped_shared_traces"] += 1
            else:
                by_owner.setdefault(next(iter(users)), []).append(trace)

        for owner, owner_traces in by_owner.items():
            rows = db.query(SystemEvent).filter(*userless_call, SystemEvent.trace_id.in_(owner_traces))
            if dry_run:
                report["would_fill"] += rows.count()
                continue
            changed = rows.update({SystemEvent.user_id: owner}, synchronize_session=False)
            report["would_fill"] += changed
            report["filled"] += changed
        if dry_run:
            db.rollback()
        else:
            db.commit()
        report["batches"] += 1

    return report
