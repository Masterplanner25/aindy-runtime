"""Transparent crash continuation for non-waiting flows (ECOGAP-1 Phase 1).

On restart, a FlowRun stranded mid-run in ``running``/``executing`` is failed by
the stuck-run scanners — there is no "continue from the last committed node".
Suspended (``waiting``) flows already rehydrate; this closes the non-waiting gap.

The substrate already exists: the flow engine writes ``FlowRun.state`` as a full
snapshot after every node and advances ``current_node`` to the *next*, not-yet-run
node in the same commit, and ``PersistentFlowRunner.resume(run_id)`` drives the
node loop from ``current_node``/``state`` whenever status is not ``waiting``. So
continuation is: atomically re-claim the stranded run and re-drive ``resume()`` —
exactly what the WAIT-rehydration path does, minus the wait.

Correctness: on continuation the single node whose commit didn't land re-runs (all
prior nodes are committed, their patches already in the snapshot). That node's
side effects must be idempotent, so continuation only applies to flows explicitly
declared **continuation-safe** (``mark_flow_continuation_safe``). A durable
per-run attempt counter (in ``state["__continuation_attempts"]``) dead-letters a
crash-looping run instead of retrying forever.

Opt-in and default-off (``AINDY_DURABLE_CONTINUATION``); a no-op otherwise.
"""

from __future__ import annotations

import logging
import threading

from AINDY.kernel.clock import utcnow

logger = logging.getLogger(__name__)

_ATTEMPTS_KEY = "__continuation_attempts"
_ACTIVE_STATUSES = ("running", "executing")


def _continuation_enabled() -> bool:
    from AINDY.config import settings

    return bool(getattr(settings, "AINDY_DURABLE_CONTINUATION", False))


def _max_attempts() -> int:
    from AINDY.config import settings

    return max(1, int(getattr(settings, "AINDY_DURABLE_CONTINUATION_MAX_ATTEMPTS", 3)))


def _default_safe_enabled() -> bool:
    """DUR-3: continuation applies to all flows (except deny-listed) rather than only
    declaration-safe ones. Gated by AINDY_DURABLE_CONTINUATION_ALL (default off)."""
    from AINDY.config import settings

    return bool(getattr(settings, "AINDY_DURABLE_CONTINUATION_ALL", False))


def _flow_continuation_permitted(flow_name: str) -> bool:
    """DUR-3 permission: default-safe → all flows except deny-listed
    (mark_flow_continuation_unsafe, for raw un-mediated side effects); else the per-flow
    continuation-safe DECLARATION is still required (current behavior)."""
    from AINDY.runtime.flow_engine import (
        is_flow_continuation_safe,
        is_flow_continuation_unsafe,
    )

    if _default_safe_enabled():
        return not is_flow_continuation_unsafe(flow_name)
    return is_flow_continuation_safe(flow_name)


def try_continue_flow_run(flow_run, db) -> bool:
    """Attempt to continue one stranded non-waiting FlowRun.

    Returns ``True`` when the run was *handled* (continuation dispatched, or the
    run dead-lettered after exhausting attempts) — the caller must then NOT fail
    it. Returns ``False`` when the run is ineligible (feature off, agent-exec
    flow, not continuation-safe, unknown flow, or lost the claim race) — the
    caller falls through to its normal failure path. Never raises.
    """
    try:
        if not _continuation_enabled():
            return False
        # Agent-execution flows use the nodus_vm segment chain, not the standard
        # node loop — their crash continuation is ECOGAP-1 Phase 2.
        if getattr(flow_run, "workflow_type", None) == "agent_execution":
            return False

        flow_name = flow_run.flow_name
        from AINDY.runtime.flow_engine import FLOW_REGISTRY

        if flow_name not in FLOW_REGISTRY:
            return False
        if not _flow_continuation_permitted(flow_name):
            return False

        from AINDY.db.models.flow_run import FlowRun

        state = flow_run.state or {}
        attempts = int(state.get(_ATTEMPTS_KEY, 0)) if isinstance(state, dict) else 0
        if attempts >= _max_attempts():
            return _dead_letter(flow_run, db, attempts)

        # Atomic claim: only one instance re-drives a stranded run. Flipping to
        # "executing" also bypasses resume()'s waiting-only entry guard.
        claimed = (
            db.query(FlowRun)
            .filter(FlowRun.id == flow_run.id, FlowRun.status.in_(_ACTIVE_STATUSES))
            .update({"status": "executing"}, synchronize_session=False)
        )
        db.commit()
        if not claimed:
            return False  # another instance won the claim

        # Durably record the attempt (winner-only; the status CAS serialized us).
        run = db.query(FlowRun).filter(FlowRun.id == flow_run.id).first()
        if run is None:
            return False
        new_state = dict(run.state or {})
        new_state[_ATTEMPTS_KEY] = attempts + 1
        run.state = new_state
        db.commit()

        _dispatch_resume(
            run_id=str(run.id),
            flow_name=flow_name,
            user_id=str(run.user_id) if run.user_id else None,
            workflow_type=run.workflow_type,
        )
        logger.warning(
            "[FlowContinuation] re-driving stranded flow run=%s flow=%s (attempt %d/%d)",
            run.id, flow_name, attempts + 1, _max_attempts(),
        )
        return True
    except Exception as exc:  # continuation must never break the recovery scan
        logger.error("[FlowContinuation] try_continue failed for run=%s: %s",
                     getattr(flow_run, "id", "?"), exc)
        return False


def _dead_letter(flow_run, db, attempts: int) -> bool:
    """Crash-loop guard: a run that exhausted its attempts is dead-lettered."""
    try:
        flow_run.status = "failed"
        flow_run.completed_at = utcnow()
        flow_run.dead_letter_reason = f"continuation_exhausted after {attempts} attempt(s)"
        flow_run.dead_lettered_at = utcnow()
        flow_run.error_message = "Crash continuation exhausted — dead-lettered"
        db.commit()
        logger.warning(
            "[FlowContinuation] run=%s dead-lettered after %d continuation attempt(s)",
            flow_run.id, attempts,
        )
        return True
    except Exception as exc:
        logger.error("[FlowContinuation] dead-letter failed for run=%s: %s", flow_run.id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _dispatch_resume(*, run_id: str, flow_name: str, user_id, workflow_type) -> None:
    """Re-drive the flow on a daemon thread with a fresh session (mirrors the
    WAIT-rehydration resume callback)."""

    def _bg():
        try:
            from AINDY.db.database import SessionLocal
            from AINDY.kernel.effect_ledger import durable_effects_scope
            from AINDY.runtime.flow_engine import FLOW_REGISTRY, PersistentFlowRunner

            bg_db = SessionLocal()
            try:
                runner = PersistentFlowRunner(
                    flow=FLOW_REGISTRY[flow_name],
                    db=bg_db,
                    user_id=user_id,
                    workflow_type=workflow_type,
                )
                # DUR-2 — the single node that re-runs on continuation must produce
                # at-most-once effects. This per-run signal engages the effect-boundary
                # chokepoints for this re-drive without any per-tool/per-syscall
                # EXACTLY_ONCE declaration. Set inside the bg thread so the contextvar
                # covers the resume call tree (contextvars don't cross thread spawn).
                with durable_effects_scope():
                    runner.resume(run_id)
            finally:
                bg_db.close()
        except Exception as exc:
            logger.warning("[FlowContinuation] resume failed for run=%s: %s", run_id, exc)

    threading.Thread(target=_bg, daemon=True).start()
