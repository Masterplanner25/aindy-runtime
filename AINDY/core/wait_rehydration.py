"""The `waiting_flow_runs` durability seed — shared by the WAIT branch's backup and flow rehydration.

★ This module used to hold `rehydrate_waiting_eus()`, the boot-time re-registration of every
`execution_units` row in ``status="waiting"`` with a scheduler callback that moved the unit
``waiting → resumed → executing``. **Removed 2026-09-16 (`EU-WAIT-SIGNAL-DEAD-1`'s follow-up).**
Two facts made it dead weight:

1. **Its callback never committed.** It opened its own ``SessionLocal``, flushed the two
   transitions, and ``close()``d — rolled back on every fire, since it was written. The unit's
   status was never moved by it; the log line saying so was the only evidence it ran.
2. **Every unit it could re-register is a FLOW run's, and the flow path already resumes that
   unit — with a commit.** After #679 removed the request-level wait, a unit enters ``waiting``
   only via `_park_execution_unit` (a flow node WAIT). `flow_run_rehydration` re-registers the
   RUN, keyed by the run id, and its callback's step 2 resumes the run's unit on the session
   the runner then commits. So each restart registered TWO scheduler entries per parked run —
   one real (run id), one rollback-only (unit id) — and `flow_run_rehydration`'s docstring
   called the pair "complementary … removing either would leave a broken half-state", which
   was false in exactly the direction that hid the rollback.

**Do NOT restore it by adding the commit.** A second, independently-committing callback for
the same unit races the flow callback for the same transition (`resume_execution_unit`'s
idempotency guard hides the race until it does not). One writer per unit: the run's.

What remains is the seed. `waiting_flow_runs.run_id` FKs to `flow_runs`, so a non-run id is
skipped at DEBUG (FR-29 addendum): a non-flow wait lives in memory + Redis, the rule
`_persist_wait_backup` applies.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from AINDY.db.database import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def ensure_waiting_flow_run_row(
    db: "Session",
    *,
    run_id: str,
    event_type: str,
    correlation_id: str | None,
    timeout_at,
    eu_id: str | None,
    priority: str,
) -> None:
    """Best-effort seed of waiting_flow_runs for startup durability."""
    waited_since = utcnow()
    max_wait_seconds = None
    if timeout_at is not None:
        try:
            max_wait_seconds = max(
                0,
                int((timeout_at - waited_since).total_seconds()),
            )
        except Exception:
            max_wait_seconds = None
    try:
        import os

        from AINDY.db.models.flow_run import FlowRun
        from AINDY.db.models.waiting_flow_run import WaitingFlowRun

        # ★ FR-29 addendum (2026-09-15): `waiting_flow_runs.run_id` FKs to `flow_runs`. The
        # since-removed EU-side caller (`rehydrate_waiting_eus`) passed `run_id=eu_id`, and an
        # execution-unit id is never a flow-run id — so on Postgres that seed raised
        # ForeignKeyViolation for EVERY waiting unit on EVERY boot, logged "non-fatal" (ten per
        # boot on the app's stack from the units FR-29 leaked). SQLite does not enforce the FK,
        # which is why no test saw it. The guard stays: the WAIT branch's backup can still be
        # handed a non-run id. Say so at DEBUG and do nothing.
        if db.query(FlowRun.id).filter(FlowRun.id == str(run_id)).first() is None:
            logger.debug(
                "[rehydrate] waiting_flow_runs seed skipped for run=%s: not a flow run "
                "(eu_id=%s) — wait held in memory/Redis only",
                run_id, eu_id,
            )
            return

        existing = (
            db.query(WaitingFlowRun)
            .filter(WaitingFlowRun.run_id == str(run_id))
            .first()
        )
        if existing is None:
            db.add(
                WaitingFlowRun(
                    run_id=str(run_id),
                    event_type=event_type,
                    correlation_id=correlation_id,
                    waited_since=waited_since,
                    max_wait_seconds=max_wait_seconds,
                    timeout_at=timeout_at,
                    eu_id=eu_id,
                    priority=priority or "normal",
                    instance_id=os.getenv("HOSTNAME", "local"),
                )
            )
        else:
            existing.event_type = event_type
            existing.correlation_id = correlation_id
            existing.timeout_at = timeout_at
            existing.eu_id = eu_id
            existing.priority = priority or "normal"
            if existing.waited_since is None:
                existing.waited_since = waited_since
            existing.max_wait_seconds = max_wait_seconds
        db.flush()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning(
            "[rehydrate] waiting_flow_runs seed failed for run=%s (non-fatal): %s",
            run_id,
            exc,
        )

