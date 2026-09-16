from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PRIORITY_HIGH = "high"
PRIORITY_NORMAL = "normal"
PRIORITY_LOW = "low"
PRIORITY_ORDER = (PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW)
MAX_PER_SCHEDULE_CYCLE = 10


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


_MAX_PRE_REHYDRATION_BUFFER = _int_env(
    "AINDY_SCHEDULER_PRE_REHYDRATION_BUFFER",
    1000,
)


def correlation_admits(
    wait_correlation: str | None,
    emit_correlation: str | None,
    *,
    run_scoped: bool,
) -> bool:
    """THE correlation rule for waking a wait — one function, every path (`WAIT-PAYLOAD-PATH-1` (b)).

    1. **A run-scoped wake is decisive.** When the caller named the run (`run_id`), correlation
       does not get a veto: the run id is the only key unique to the run
       (`RESUME-FANOUT-UNSCOPED-1`), and a wake that names a run and then declines to wake it
       because of a secondary key is a silent no-op. Observed: `POST …/runs/{id}/resume` with a
       payload that happened to carry a client-side ``correlation_id`` key injected the payload,
       reported ``resumed: true``, and never woke the run — its own key vetoed it.
    2. **Otherwise correlation vetoes only when BOTH sides carry one and they differ.** A wait
       registered without one matches any emit; an emit without one matches any wait.

    Until 2026-09-15 the local scan (`waits.py`) applied rule 2 and the cross-instance fallback
    (`cross_instance.py`) applied a stricter one — skip whenever the emit carries an id the
    wait's does not equal — so a wait registered with ``correlation_id=None`` resumed locally on
    any emit and NEVER cross-instance, because `_notify_scheduler_of_event` always supplies one.
    Two instances, two answers to the same emit. Three hand-copies of a rule is how they drift;
    this is the one copy.
    """
    if run_scoped:
        return True
    wait_corr = wait_correlation or None
    emit_corr = emit_correlation or None
    return not (wait_corr and emit_corr and wait_corr != emit_corr)


def _get_session_factory():
    from AINDY.db.database import SessionLocal

    return SessionLocal


def _get_instance_id() -> str:
    return os.getenv("HOSTNAME", "local")


def _emit_dispatch_failure(item: "ScheduledItem", exc: Exception) -> None:
    try:
        logger.critical(
            "[Scheduler] DISPATCH_FAILURE run_id=%s eu=%s tenant=%s type=%s retries=%d exc=%r",
            item.run_id,
            item.execution_unit_id,
            item.tenant_id,
            item.eu_type,
            item.retry_count,
            str(exc),
        )
    except Exception:
        pass


@dataclass
class _ResumedEUStub:
    id: str
    type: str
    priority: str
    extra: dict = field(default_factory=dict)


@dataclass
class ScheduledItem:
    execution_unit_id: str
    tenant_id: str
    priority: str
    run_callback: Callable[[], None]
    run_id: Optional[str] = None
    eu_type: str = "flow"
    enqueued_at_seq: int = field(default=0, compare=False)
    #: FR-15 — monotonic timestamp set by ``enqueue()``. 0.0 means "never enqueued
    #: through the normal path" (e.g. a retry item reconstructed by the dispatcher), and
    #: callers must treat it as "unknown", never as "waited zero".
    enqueued_at_monotonic: float = field(default=0.0, compare=False)
    retry_count: int = field(default=0, compare=False)
    max_retries: int = field(default=2, compare=False)

    def __post_init__(self) -> None:
        if self.priority not in PRIORITY_ORDER:
            raise ValueError(
                f"Invalid priority {self.priority!r}; must be one of {PRIORITY_ORDER}"
            )
        if self.max_retries == 2:
            self.max_retries = _int_env("AINDY_SCHEDULER_MAX_DISPATCH_RETRIES", 2)
