"""Crash continuation for nodus_vm agent runs (ECOGAP-1 Phase 2, opt-in).

The flow-level continuation (Phase 1, `core/flow_continuation.py`) covers standard
DAG flows but explicitly skips `agent_execution`. This closes the agent side for
the **nodus_vm** segment-chain path: a crashed agent run left in `executing` is
re-driven from its last *completed segment boundary* instead of stranded, reusing
the WAIT-resume machinery (`_build_agent_resume_callback`) with a claim from
`executing` rather than `waiting`.

Granularity is segment-level: the crashed segment re-runs from its first step
(AgentStep is a post-segment batch write, so mid-segment progress isn't durable —
that's ECOGAP-1 Phase 2a, deferred). The re-run therefore repeats the crashed
segment's tool calls, so continuation only applies to agent types explicitly
declared **continuation-safe** (idempotent tools) — `mark_agent_type_continuation_safe`.

Startup-only: at startup no runner is live, so every `executing` AgentRun is
definitionally orphaned from the dead process (same principle as Phase 1 / the
RTR-2 job recovery); a crash-loop is bounded by an attempt counter in
`result["__continuation_attempts"]` that resets naturally once the run makes
progress. Opt-in behind `AINDY_DURABLE_CONTINUATION` (default off).
"""

from __future__ import annotations

import logging
import threading

from AINDY.kernel.clock import utcnow

logger = logging.getLogger(__name__)

_ATTEMPTS_KEY = "__continuation_attempts"
_MAX_SCAN = 500
_NODUS_VM_WORKFLOW = "nodus_agent_execution"

# Agent types whose tools are idempotent (or EffectRecord-gated), so re-running a
# crashed segment cannot double-fire a side effect. Empty by default.
CONTINUATION_SAFE_AGENT_TYPES: set[str] = set()


def mark_agent_type_continuation_safe(agent_type: str) -> None:
    """Declare an agent type safe to re-drive from its last completed segment."""
    CONTINUATION_SAFE_AGENT_TYPES.add(agent_type)


def is_agent_type_continuation_safe(agent_type: str | None) -> bool:
    return agent_type in CONTINUATION_SAFE_AGENT_TYPES


def _continuation_enabled() -> bool:
    from AINDY.config import settings

    return bool(getattr(settings, "AINDY_DURABLE_CONTINUATION", False))


def _max_attempts() -> int:
    from AINDY.config import settings

    return max(1, int(getattr(settings, "AINDY_DURABLE_CONTINUATION_MAX_ATTEMPTS", 3)))


def _count_completed_segments(segments: list, completed_steps: int) -> int:
    """Number of segments fully covered by `completed_steps` committed tool steps.

    AgentStep is batch-written per segment, so `completed_steps` always lands on a
    segment boundary — this yields the first segment to (re-)run.
    """
    total = 0
    idx = 0
    for i, seg in enumerate(segments):
        n = len(seg.get("tool_steps") or [])
        if total + n <= completed_steps:
            total += n
            idx = i + 1
        else:
            break
    return idx


def _is_nodus_vm_run(run, db) -> bool:
    """True when the run executed via the nodus_vm segment chain (its linked
    FlowRun is a ``nodus_agent_execution`` wrapper) — not the AGENT_FLOW default,
    whose crashed FlowRun is recovered by ``stuck_run_service``."""
    if not getattr(run, "flow_run_id", None):
        return False
    from AINDY.db.models.flow_run import FlowRun

    fr = db.query(FlowRun).filter(FlowRun.id == run.flow_run_id).first()
    return fr is not None and fr.workflow_type == _NODUS_VM_WORKFLOW


def continue_crashed_agent_runs(db) -> int:
    """STARTUP-ONLY: re-drive crashed nodus_vm agent runs from their last completed
    segment. Returns the number continued. Best-effort — never raises."""
    if not _continuation_enabled():
        return 0
    try:
        from AINDY.db.models import AgentRun
        from AINDY.runtime.agent_plan_compiler import split_agent_plan
        from AINDY.runtime.nodus_execution_service import _build_agent_resume_callback

        crashed = (
            db.query(AgentRun)
            .filter(AgentRun.status == "executing")
            .limit(_MAX_SCAN)
            .all()
        )
        continued = 0
        for run in crashed:
            try:
                if not is_agent_type_continuation_safe(run.agent_type):
                    continue
                if not _is_nodus_vm_run(run, db):
                    continue  # AGENT_FLOW / unknown — handled by the flow-side path

                try:
                    segments = split_agent_plan(run.plan or {})
                except ValueError:
                    continue
                accumulated = list((run.result or {}).get("steps") or [])
                next_idx = _count_completed_segments(segments, len(accumulated))
                if next_idx >= len(segments):
                    continue  # nothing left to run — leave to normal completion

                attempts = int((run.result or {}).get(_ATTEMPTS_KEY, 0))
                if attempts >= _max_attempts():
                    _dead_letter(run, db, attempts)
                    continue

                # Bound crash-loops via a counter in result. The segment chain
                # rewrites result={"steps": …} on the next segment terminal, so the
                # counter resets once the run makes progress.
                run.result = {
                    **(run.result or {}),
                    "steps": accumulated,
                    _ATTEMPTS_KEY: attempts + 1,
                }
                db.commit()

                total_tool_steps = sum(len(s.get("tool_steps") or []) for s in segments)
                callback = _build_agent_resume_callback(
                    run_id=str(run.id),
                    segments=segments,
                    next_segment_index=next_idx,
                    accumulated=accumulated,
                    user_id=str(run.user_id),
                    correlation_id=run.correlation_id,
                    scoped_token=run.capability_token,
                    total_tool_steps=total_tool_steps,
                    claim_status="executing",
                )
                threading.Thread(target=callback, daemon=True).start()
                continued += 1
                logger.warning(
                    "[AgentContinuation] re-driving crashed agent run=%s from segment %d (attempt %d/%d)",
                    run.id, next_idx, attempts + 1, _max_attempts(),
                )
            except Exception as exc:
                logger.error(
                    "[AgentContinuation] continue failed for run=%s: %s",
                    getattr(run, "id", "?"), exc,
                )

        if continued:
            logger.info("[AgentContinuation] crash-continued %d agent run(s)", continued)
        return continued
    except Exception as exc:
        logger.error("[AgentContinuation] scan failed: %s", exc)
        return 0


def _dead_letter(run, db, attempts: int) -> None:
    """Crash-loop guard: an agent run that exhausted its attempts is failed."""
    try:
        run.status = "failed"
        run.completed_at = utcnow()
        run.error_message = f"Crash continuation exhausted after {attempts} attempt(s)"
        db.commit()
        logger.warning(
            "[AgentContinuation] run=%s failed after %d continuation attempt(s)", run.id, attempts
        )
    except Exception as exc:
        logger.error("[AgentContinuation] dead-letter failed for run=%s: %s", run.id, exc)
        try:
            db.rollback()
        except Exception:
            pass
