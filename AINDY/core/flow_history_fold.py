"""DUR-4 — reconstruct a FlowRun's state by folding its FlowHistory event log.

`FlowHistory` is a per-node append-only log where each row carries a full pre-node checkpoint
(`input_state`) and the delta the node produced (`output_patch`). So the state *after* the
last recorded node is that row's `input_state` shallow-merged with its `output_patch` — but
only when the node SUCCEEDED, matching the live engine (`runner_steps.py` does
`state.update(patch)` only on SUCCESS; WAIT/FAILURE/RETRY don't apply their patch).

This is a **recovery/audit** primitive, not the live source of truth: normal resume rehydrates
from the durable `FlowRun.state` snapshot. The fold is the canonical backup when that snapshot
is missing/torn — the last FlowHistory row commits *before* the snapshot advance, so it is at
least as fresh as the snapshot for the last completed node.
"""
from __future__ import annotations

from typing import Any


def fold_flow_history_state(rows: list) -> dict[str, Any]:
    """Reconstruct the post-last-node ``FlowRun.state`` from FlowHistory rows in order.

    ``rows`` must be ordered oldest→newest. Returns ``{}`` for an empty log. Uses the last
    row's full ``input_state`` checkpoint (so it is robust to any missing intermediate rows),
    applying its ``output_patch`` only on SUCCESS (shallow merge, parity with the engine).
    """
    if not rows:
        return {}
    last = rows[-1]
    base = dict(getattr(last, "input_state", None) or {})
    if str(getattr(last, "status", "") or "").upper() == "SUCCESS":
        base.update(dict(getattr(last, "output_patch", None) or {}))
    return base


def ordered_flow_history(db, run_id: str) -> list:
    """Load a run's FlowHistory rows in canonical order: sequence_number (DUR-4), then
    created_at, then id — so pre-DUR-4 rows (null sequence) still order chronologically."""
    from sqlalchemy import asc, nullsfirst

    from AINDY.db.models.flow_run import FlowHistory

    return (
        db.query(FlowHistory)
        .filter(FlowHistory.flow_run_id == str(run_id))
        .order_by(
            nullsfirst(asc(FlowHistory.sequence_number)),
            asc(FlowHistory.created_at),
            asc(FlowHistory.id),
        )
        .all()
    )


def reconstruct_flow_run_state(db, run_id: str) -> dict[str, Any]:
    """Fold a run's FlowHistory into its reconstructed state (``{}`` if no history)."""
    return fold_flow_history_state(ordered_flow_history(db, run_id))
