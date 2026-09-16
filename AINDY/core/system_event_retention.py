"""`system_events` retention — a class per event TYPE, pruning LEAVES only (`SYSEVENT-RETENTION-1`).

Design: ``docs/design/SYSEVENT_RETENTION_DESIGN.md`` — read §2 before touching the selection.

The table every execution, signal and causal edge lands in was never pruned; FR-18 removed the
loudest writer and left the class. Two entries depend on rows NOT disappearing: `EVENT-OUTBOX-1`
reads a missing row as "the work never happened", and `AUDIT-CORRELATION-1` joins by convention
with no FK. So a `SystemEvent`'s value is uniform by TYPE, not by age, and this module makes
that the policy.

★ THE FOREIGN KEYS DECIDE MORE THAN A TYPE TABLE CAN (design §2)
------------------------------------------------------------------
Five columns reference `system_events.id`. Four are `NO ACTION` — `system_events.parent_event_id`
(self), `agent_events.system_event_id`, `memory_nodes.source_event_id` / `.root_event_id` — so a
referenced event CANNOT be deleted and a naive batch `DELETE` aborts on the first parent row it
meets. The fifth, `event_edges.source_event_id` / `.target_event_id`, is `CASCADE`: the database
lets that delete through and silently takes the causal edge `build_trace_graph` reads with it.
**Rule: prune leaves only** — no inbound reference from any of the five — and the edge check is
in the predicate precisely because the database would not refuse it.

★ UNCLASSIFIED = KEEP (design §3)
----------------------------------
`SystemEventTypes` declares 46 names; the runtime emits 22 more as literals and the app registers
its own. A literal table keyed on the enum would delete a new type by omission (green-check
variant 12), so the class is a REGISTRY the runtime seeds and the app extends
(`register_event_retention`), and a type with no class is never selected. The pressure to
classify is a gauge, not a reminder: `aindy_system_events_unclassified_rows`.

★ `report` BEFORE `prune`
--------------------------
`AINDY_SYSEVENT_RETENTION` is unset (off — today's behaviour), `report` (select, count, log,
delete NOTHING) or `prune`. A silent prune is indistinguishable from a lost write, so the job logs
and counts PER TYPE and says zero explicitly.
"""
from __future__ import annotations

import fnmatch
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

RETENTION_AUDIT = "audit"
RETENTION_OPERATIONAL = "operational"
RETENTION_KEEPALIVE = "keepalive"
RETENTION_CLASSES: tuple[str, ...] = (RETENTION_AUDIT, RETENTION_OPERATIONAL, RETENTION_KEEPALIVE)

MODE_OFF = "off"
MODE_REPORT = "report"
MODE_PRUNE = "prune"
RETENTION_MODES: tuple[str, ...] = (MODE_OFF, MODE_REPORT, MODE_PRUNE)

ENV_MODE = "AINDY_SYSEVENT_RETENTION"
ENV_INTERVAL_HOURS = "AINDY_SYSEVENT_RETENTION_INTERVAL_HOURS"
ENV_BATCH = "AINDY_SYSEVENT_RETENTION_BATCH"
ENV_OPERATIONAL_DAYS = "AINDY_SYSEVENT_RETENTION_OPERATIONAL_DAYS"
ENV_KEEPALIVE_DAYS = "AINDY_SYSEVENT_RETENTION_KEEPALIVE_DAYS"

DEFAULT_INTERVAL_HOURS = 24
DEFAULT_BATCH_SIZE = 1_000
#: Days a class is kept; ``None`` = never pruned by age (DEC-029).
DEFAULT_MAX_AGE_DAYS: dict[str, Optional[int]] = {
    RETENTION_AUDIT: None,
    RETENTION_OPERATIONAL: 90,
    RETENTION_KEEPALIVE: 7,
}

# ---------------------------------------------------------------------------
# The seed table (design §4). Exact names or `fnmatch` globs; exact wins over glob.
#
# ★ Failure-shaped events are AUDIT regardless of family: `execution.failed` is audit while
#   `execution.started` is operational — a failure is the row a support conversation starts
#   from; its siblings say the run existed.
# ★ `autonomy.decision` = operational is the one class DECIDED rather than derived (DEC-028):
#   a decision old enough to prune is one nobody is still asking about, and the leaf rule keeps
#   any decision that led to a dispatch (it becomes a parent).
# ---------------------------------------------------------------------------
RUNTIME_RETENTION_SEED: dict[str, str] = {
    # audit — never by age
    "execution.failed": RETENTION_AUDIT,
    "flow.node.failed": RETENTION_AUDIT,
    "agent.step.failed": RETENTION_AUDIT,
    "async_job.failed": RETENTION_AUDIT,
    "nodus.execute.failed": RETENTION_AUDIT,
    "embedding.failed": RETENTION_AUDIT,
    "external.call.failed": RETENTION_AUDIT,
    "capability.*": RETENTION_AUDIT,
    "auth.*": RETENTION_AUDIT,
    "platform.*": RETENTION_AUDIT,
    "dlq.drained": RETENTION_AUDIT,
    "flow_run.dead_lettered": RETENTION_AUDIT,
    "queue.failure_rate_alert": RETENTION_AUDIT,
    "startup.recovery.*": RETENTION_AUDIT,
    "next_action.*": RETENTION_AUDIT,
    "error.*": RETENTION_AUDIT,
    "WAIT_TIMEOUT": RETENTION_AUDIT,
    "agent.message.acknowledged": RETENTION_AUDIT,
    # operational — the execution ledger; a window, then only volume
    "execution.*": RETENTION_OPERATIONAL,
    "flow.*": RETENTION_OPERATIONAL,
    "agent.step": RETENTION_OPERATIONAL,
    "agent.step.completed": RETENTION_OPERATIONAL,
    "async_job.*": RETENTION_OPERATIONAL,
    "nodus.*": RETENTION_OPERATIONAL,
    "embedding.*": RETENTION_OPERATIONAL,
    "syscall.executed": RETENTION_OPERATIONAL,
    "scheduler.queued": RETENTION_OPERATIONAL,
    "memory.write": RETENTION_OPERATIONAL,
    "reasoning.signal": RETENTION_OPERATIONAL,
    "recall.used": RETENTION_OPERATIONAL,
    "score.computed": RETENTION_OPERATIONAL,
    "analytics.score.updated": RETENTION_OPERATIONAL,
    "masterplan.goal_state.changed": RETENTION_OPERATIONAL,
    "autonomy.decision": RETENTION_OPERATIONAL,
    "autonomy.window": RETENTION_OPERATIONAL,
    "external.call.*": RETENTION_OPERATIONAL,
    "feedback.*": RETENTION_OPERATIONAL,
    "client.*": RETENTION_OPERATIONAL,
    # keepalive — proves a loop ran; worthless after the next one
    "watchdog.scan.completed": RETENTION_KEEPALIVE,
    "health.liveness.completed": RETENTION_KEEPALIVE,
}

_REGISTERED: dict[str, str] = {}


def register_event_retention(event_type: str, retention_class: str) -> str:
    """Declare the retention class of an event type. Exact names or `fnmatch` globs.

    A declaration OVERRIDES the runtime seed for that pattern — the app owns its own types and
    may re-class a runtime one. Unknown classes are refused loudly: a misspelled class must not
    read as "unclassified = keep" and silently disable pruning for that type.
    """
    if not event_type or not str(event_type).strip():
        raise ValueError("event_type must be a non-empty string")
    if retention_class not in RETENTION_CLASSES:
        raise ValueError(
            f"register_event_retention({event_type!r}): {retention_class!r} is not one of "
            f"{list(RETENTION_CLASSES)}"
        )
    _REGISTERED[str(event_type).strip()] = retention_class
    return retention_class


def reset_event_retention_registry() -> None:
    """Test isolation helper."""
    _REGISTERED.clear()


def _effective_table() -> dict[str, str]:
    table = dict(RUNTIME_RETENTION_SEED)
    table.update(_REGISTERED)
    return table


def retention_class_for(event_type: str) -> Optional[str]:
    """The class of an event type, or ``None`` when nothing declared one (= keep).

    Exact match beats a glob; among globs the LONGEST pattern wins, so `execution.failed`
    (exact, audit) beats `execution.*` (operational) and `agent.step.failed` beats `agent.*`.
    """
    table = _effective_table()
    if event_type in table:
        return table[event_type]
    best: Optional[tuple[int, str]] = None
    for pattern, klass in table.items():
        if any(ch in pattern for ch in "*?[") and fnmatch.fnmatchcase(event_type, pattern):
            if best is None or len(pattern) > best[0]:
                best = (len(pattern), klass)
    return best[1] if best else None


def classified_types() -> dict[str, str]:
    """Every exact (non-glob) declaration and its class — for tests and the report."""
    return {k: v for k, v in _effective_table().items() if not any(ch in k for ch in "*?[")}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        return default


def retention_mode() -> str:
    """``off`` (unset/blank — today's behaviour), ``report`` or ``prune``; anything else is ``off``.

    ★ An unrecognised value is OFF, not report: a typo must never delete rows.
    """
    raw = (os.getenv(ENV_MODE) or "").strip().lower()
    return raw if raw in (MODE_REPORT, MODE_PRUNE) else MODE_OFF


def retention_enabled() -> bool:
    return retention_mode() != MODE_OFF


def retention_interval_hours() -> int:
    return _int_env(ENV_INTERVAL_HOURS, DEFAULT_INTERVAL_HOURS)


def retention_batch_size() -> int:
    return _int_env(ENV_BATCH, DEFAULT_BATCH_SIZE)


def max_age_days() -> dict[str, Optional[int]]:
    """Per-class age ceiling after env overrides. ``audit`` has no override — it is never by age."""
    ages = dict(DEFAULT_MAX_AGE_DAYS)
    ages[RETENTION_OPERATIONAL] = _int_env(ENV_OPERATIONAL_DAYS, DEFAULT_MAX_AGE_DAYS[RETENTION_OPERATIONAL])
    ages[RETENTION_KEEPALIVE] = _int_env(ENV_KEEPALIVE_DAYS, DEFAULT_MAX_AGE_DAYS[RETENTION_KEEPALIVE])
    return ages


# ---------------------------------------------------------------------------
# Selection — leaves only (design §2). This predicate IS the safety property.
# ---------------------------------------------------------------------------

def _leaf_filter(SystemEvent):
    """The five referrer anti-joins. A row any of them references is never eligible."""
    from sqlalchemy import exists
    from sqlalchemy.orm import aliased

    from AINDY.db.models.agent_event import AgentEvent
    from AINDY.db.models.event_edge import EventEdge
    from AINDY.memory.memory_persistence import MemoryNodeModel as MemoryNode

    Child = aliased(SystemEvent)
    return [
        ~exists().where(Child.parent_event_id == SystemEvent.id),
        ~exists().where(AgentEvent.system_event_id == SystemEvent.id),
        ~exists().where(MemoryNode.source_event_id == SystemEvent.id),
        ~exists().where(MemoryNode.root_event_id == SystemEvent.id),
        # ★ the CASCADE — the database would allow this delete; the causal graph would not survive it
        ~exists().where(EventEdge.source_event_id == SystemEvent.id),
        ~exists().where(EventEdge.target_event_id == SystemEvent.id),
    ]


def select_prunable_ids(db, *, event_type: str, cutoff: datetime, limit: int) -> list[Any]:
    """Ids of LEAF events of one type older than ``cutoff``, at most ``limit``."""
    from AINDY.db.models.system_event import SystemEvent

    query = (
        db.query(SystemEvent.id)
        .filter(SystemEvent.type == event_type, SystemEvent.timestamp < cutoff)
        .filter(*_leaf_filter(SystemEvent))
        .order_by(SystemEvent.timestamp.asc())
        .limit(limit)
    )
    return [row[0] for row in query.all()]


def _types_present(db) -> dict[str, int]:
    from sqlalchemy import func

    from AINDY.db.models.system_event import SystemEvent

    rows = db.query(SystemEvent.type, func.count(SystemEvent.id)).group_by(SystemEvent.type).all()
    return {str(t): int(n) for t, n in rows}


# ---------------------------------------------------------------------------
# The job body
# ---------------------------------------------------------------------------

def prune_system_events(
    *,
    mode: Optional[str] = None,
    batch_size: Optional[int] = None,
    db=None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Select (and in ``prune`` mode delete) age-expired LEAF events, per classified type.

    Deletes in COMMITTED BATCHES so a five-week backlog is never one transaction holding a
    pooled connection (`RT-MEMTXN-LEAK-1`'s lesson, as `prune_cascade_debris` already encodes).
    Logs and counts per type; says zero explicitly. ``report`` mode runs the same selection and
    deletes nothing — the report an operator reads before the first real run.
    """
    effective_mode = mode or retention_mode()
    if effective_mode not in (MODE_REPORT, MODE_PRUNE):
        return {"mode": MODE_OFF, "types": {}, "unclassified_rows": 0, "deleted": 0, "batches": 0}
    size = batch_size or retention_batch_size()
    if size < 1:
        raise ValueError("batch_size must be >= 1")
    moment = now or datetime.now(timezone.utc)
    ages = max_age_days()

    from AINDY.db.database import SessionLocal

    owns_session = db is None
    session = db or SessionLocal()
    report: dict[str, Any] = {"mode": effective_mode, "types": {}, "unclassified_rows": 0, "deleted": 0, "batches": 0}
    try:
        present = _types_present(session)
        for event_type, rows in sorted(present.items()):
            klass = retention_class_for(event_type)
            if klass is None:
                report["unclassified_rows"] += rows
                continue
            days = ages.get(klass)
            if days is None:
                continue  # audit — never by age
            cutoff = moment - timedelta(days=days)
            counted = _apply_for_type(session, event_type=event_type, cutoff=cutoff, size=size, delete=effective_mode == MODE_PRUNE)
            if counted["eligible"] or counted["deleted"]:
                report["types"][event_type] = {"class": klass, "older_than_days": days, **counted}
                report["deleted"] += counted["deleted"]
                report["batches"] += counted["batches"]
                _log_type(effective_mode, event_type, klass, days, counted)
        _observe(report)
        if not report["types"]:
            logger.info("[sysevent_retention] %s: nothing eligible (unclassified rows: %d)", effective_mode, report["unclassified_rows"])
        return report
    finally:
        if owns_session:
            session.close()


def count_prunable(db, *, event_type: str, cutoff: datetime) -> int:
    """How many LEAF events of one type are older than ``cutoff`` — the report's number."""
    from AINDY.db.models.system_event import SystemEvent

    return int(
        db.query(SystemEvent.id)
        .filter(SystemEvent.type == event_type, SystemEvent.timestamp < cutoff)
        .filter(*_leaf_filter(SystemEvent))
        .count()
    )


def _apply_for_type(session, *, event_type: str, cutoff: datetime, size: int, delete: bool) -> dict[str, int]:
    from AINDY.db.models.system_event import SystemEvent

    if not delete:
        # report: the SAME predicate prune would use, counted whole — not paged, so the number an
        # operator reads is the number prune would delete, however large the backlog
        return {"eligible": count_prunable(session, event_type=event_type, cutoff=cutoff), "deleted": 0, "batches": 0}

    eligible = deleted = batches = 0
    while True:
        ids = select_prunable_ids(session, event_type=event_type, cutoff=cutoff, limit=size)
        if not ids:
            break
        session.query(SystemEvent).filter(SystemEvent.id.in_(ids)).delete(synchronize_session=False)
        session.commit()  # one committed batch — never one long transaction on a pooled connection
        eligible += len(ids)
        deleted += len(ids)
        batches += 1
        if len(ids) < size:
            break
    return {"eligible": eligible, "deleted": deleted, "batches": batches}


def _log_type(mode: str, event_type: str, klass: str, days: int, counted: dict[str, int]) -> None:
    if mode == MODE_PRUNE:
        logger.info(
            "[sysevent_retention] pruned %d rows of %s (class=%s, older than %dd, %d batch(es))",
            counted["deleted"], event_type, klass, days, counted["batches"],
        )
    else:
        logger.info(
            "[sysevent_retention] report: %d rows of %s would be pruned (class=%s, older than %dd)",
            counted["eligible"], event_type, klass, days,
        )


def _observe(report: dict[str, Any]) -> None:
    """Metrics never decide anything; a failure here is swallowed."""
    try:
        from AINDY.platform_layer.metrics import system_events_pruned_total, system_events_unclassified_rows

        for event_type, entry in report["types"].items():
            if entry["deleted"]:
                system_events_pruned_total.labels(type=event_type).inc(entry["deleted"])
        system_events_unclassified_rows.set(report["unclassified_rows"])
    except Exception:  # noqa: BLE001
        pass


def describe_retention(types: Optional[Iterable[str]] = None) -> dict[str, Optional[str]]:
    """``{event_type: class-or-None}`` for the given types (or every exact declaration)."""
    names = list(types) if types is not None else sorted(classified_types())
    return {t: retention_class_for(t) for t in names}
