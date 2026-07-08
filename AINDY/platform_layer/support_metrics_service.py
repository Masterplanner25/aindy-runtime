"""Aggregate observability + execution support metrics (INFINITY-RUNTIME-1, item 3).

Backs the `sys.v1.observability.support_metrics` syscall — the runtime-exposed
aggregate the app-side Infinity support layer fetches (via a `dependency_adapter`)
so request/health data (Step 3) and agent/async execution behavior (Step 4) become
usable *support inputs*, not dashboard-only outputs.

The rollup is **tenant-scoped** (respects syscall tenant isolation): agent-run,
async-job, Infinity-loop-event, and request aggregates are filtered to the caller's
`user_id`. A single coarse platform-health signal (latest `SystemHealthLog`) is
included as a system-level indicator. All aggregation is read-only — no new
persistence — and each section is defensive so one failed sub-query degrades to an
empty section rather than failing the whole call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_HOURS = 24
_MAX_WINDOW_HOURS = 168  # 7 days


def _clamp_window(window_hours: Any) -> int:
    try:
        value = int(window_hours or _DEFAULT_WINDOW_HOURS)
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_HOURS
    return max(1, min(value, _MAX_WINDOW_HOURS))


def _agent_runs(db, user_id, window_start) -> dict[str, Any]:
    from AINDY.db.models import AgentRun

    by_status = {
        str(status): int(count)
        for status, count in (
            db.query(AgentRun.status, func.count())
            .filter(AgentRun.user_id == user_id, AgentRun.created_at >= window_start)
            .group_by(AgentRun.status)
            .all()
        )
    }
    return {"total": sum(by_status.values()), "by_status": by_status}


def _async_jobs(db, user_id, window_start) -> dict[str, Any]:
    from AINDY.db.models.job_log import JobLog

    by_status = {
        str(status): int(count)
        for status, count in (
            db.query(JobLog.status, func.count())
            .filter(JobLog.user_id == user_id, JobLog.created_at >= window_start)
            .group_by(JobLog.status)
            .all()
        )
    }
    return {"total": sum(by_status.values()), "by_status": by_status}


def _infinity_events(db, user_id, window_start) -> dict[str, int]:
    from AINDY.core.system_event_types import SystemEventTypes
    from AINDY.db.models.system_event import SystemEvent

    tracked = {
        SystemEventTypes.RECALL_USED: "recall_used",
        SystemEventTypes.SCORE_COMPUTED: "score_computed",
        SystemEventTypes.NEXT_ACTION_CHOSEN: "next_action_chosen",
    }
    counts = {label: 0 for label in tracked.values()}
    rows = (
        db.query(SystemEvent.type, func.count())
        .filter(
            SystemEvent.user_id == user_id,
            SystemEvent.timestamp >= window_start,
            SystemEvent.type.in_(list(tracked.keys())),
        )
        .group_by(SystemEvent.type)
        .all()
    )
    for event_type, count in rows:
        label = tracked.get(event_type)
        if label:
            counts[label] = int(count)
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


def _requests(db, user_id, window_start_naive) -> dict[str, Any]:
    from AINDY.db.models.request_metric import RequestMetric

    total, avg_latency = (
        db.query(func.count(), func.avg(RequestMetric.duration_ms))
        .filter(
            RequestMetric.user_id == user_id,
            RequestMetric.created_at >= window_start_naive,
        )
        .first()
    ) or (0, None)
    errors = (
        db.query(func.count())
        .filter(
            RequestMetric.user_id == user_id,
            RequestMetric.created_at >= window_start_naive,
            RequestMetric.status_code >= 500,
        )
        .scalar()
    ) or 0
    total = int(total or 0)
    errors = int(errors)
    return {
        "total": total,
        "errors": errors,
        "error_rate_pct": round(100.0 * errors / total, 2) if total else 0.0,
        "avg_latency_ms": round(float(avg_latency), 2) if avg_latency is not None else None,
    }


def _platform_health(db) -> str | None:
    from AINDY.db.models.system_health_log import SystemHealthLog

    latest = db.query(SystemHealthLog).order_by(SystemHealthLog.timestamp.desc()).first()
    return latest.status if latest else None


def _section(name: str, fn, default):
    try:
        return fn()
    except Exception as exc:  # one bad sub-query must not fail the whole rollup
        logger.warning("[SupportMetrics] section %s failed: %s", name, exc)
        return default


def build_support_metrics(db, *, user_id: Any, window_hours: Any = _DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
    """Assemble the tenant-scoped observability + execution support rollup."""
    window_hours = _clamp_window(window_hours)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)
    window_start_naive = window_start.replace(tzinfo=None)

    return {
        "generated_at": now.isoformat(),
        "window_hours": window_hours,
        "observability": {
            "requests": _section(
                "requests", lambda: _requests(db, user_id, window_start_naive),
                {"total": 0, "errors": 0, "error_rate_pct": 0.0, "avg_latency_ms": None},
            ),
            "platform_health_status": _section("health", lambda: _platform_health(db), None),
        },
        "execution": {
            "agent_runs": _section(
                "agent_runs", lambda: _agent_runs(db, user_id, window_start),
                {"total": 0, "by_status": {}},
            ),
            "async_jobs": _section(
                "async_jobs", lambda: _async_jobs(db, user_id, window_start),
                {"total": 0, "by_status": {}},
            ),
        },
        "infinity_events": _section(
            "infinity_events", lambda: _infinity_events(db, user_id, window_start),
            {"recall_used": 0, "score_computed": 0, "next_action_chosen": 0, "total": 0},
        ),
    }
