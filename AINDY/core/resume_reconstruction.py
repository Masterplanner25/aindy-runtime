"""Rebuild a run's resume from its identifier, instead of carrying it as a closure.

`FR-15`'s remaining half. `dispatch()` takes a `handler_fn` closure, and in thread mode
that closure *is* the work. It cannot be in distributed mode — a closure does not cross a
process boundary — so the scheduler's resume could not be routed there at all: the payload
would key on an id no worker could resolve, and a worker treats an unresolvable job as
**finished** (warn, ack, report success). Every scheduler resume would have been lost
silently. `async_scheduler_dispatch_enabled()` refused distributed mode for exactly that reason
until this module existed; it is opt-in there now, and a deployment that has not opted in still
runs the serialised dispatch `FR-15` describes.

★★ **The durable representation was never missing — it is what restart recovery already
depends on, every boot.** `rehydrate_waiting_flow_runs` and `rehydrate_waiting_agent_runs`
exist precisely because a process restart destroys the live closure, so the resume must be
rebuilt from the row. This module does not invent that capability; it gives it an entry
point for *one* run rather than only a whole sweep, so a resume can be named by identifier
and reconstructed wherever it lands.

★ **The agent half already had this shape.** `_build_agent_resume_callback` is module-level
with three callers — the live registration, the rehydration sweep and crash continuation —
all passing values read off the `AgentRun` row, and its docstring already states the
invariant: *the closure captures only plain values, never a live DB session.* The flow half
had a nested copy inside its sweep and a `lambda: self.resume(id)` on the live path. So this
is a pattern being propagated, not introduced — which is also why it is worth doing rather
than special-casing the distributed transport: two representations of one operation is how
they drifted apart in the first place.

**What this module deliberately does not do.** It does not decide *whether* a run should
resume, does not register waits, and does not touch a transport. It answers one question —
*given this identifier, what is the zero-argument call that resumes it?* — and returns
`None` when it cannot answer, so a caller can never mistake "not resumable" for a callable
that quietly does nothing.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Execution-unit types this module can rebuild a resume for. A type absent here is not a
#: bug — `task` and unknown types have no resume semantics — but it does mean such work
#: must never be routed anywhere the live closure cannot follow it.
RECONSTRUCTIBLE_EU_TYPES: frozenset[str] = frozenset({"flow", "agent"})


#: Reserved ``task_name`` marking a queue message as a resume rather than a registered job.
#: Double-underscored so it cannot collide with a handler key — every real one is a dotted
#: domain name (``agent.create_run``), and a test pins that it is not registered. A collision
#: would route a resume into a handler, or a job into the rebuild.
RESUME_TASK_NAME = "__resume__"

#: Key under which the resume descriptor travels inside ``QueueJobPayload.context``.
RESUME_CONTEXT_KEY = "resume"


def resume_context(*, run_id: str, eu_type: str) -> dict[str, Any]:
    """The descriptor a transport carries in place of a closure it cannot serialise.

    Two identifiers, nothing more — which is the whole point. Everything else the resume
    needs is read back off the row at the far end by :func:`build_resume_callback`, so the
    message stays valid however long it sits in a queue and whatever moves in between.

    ★ Putting any *state* in here would reintroduce the problem in a new form: a payload
    carrying a segment index or a step list is a snapshot, and a snapshot can be stale by the
    time it is read. An identifier cannot be.
    """
    return {"run_id": str(run_id), "eu_type": str(eu_type or "").lower()}


def read_resume_context(context: dict[str, Any] | None) -> tuple[str, str] | None:
    """Extract ``(run_id, eu_type)`` from a carried context, or ``None`` if absent.

    Returns ``None`` rather than raising on a malformed descriptor: a worker reading a message
    it does not understand must fall through to its ordinary handling, not crash the consumer
    loop for every subsequent message. A descriptor that is *present but unusable* is caught at
    the far end by :func:`require_resume_callback`, where there is a run to name in the error.
    """
    if not isinstance(context, dict):
        return None
    raw = context.get(RESUME_CONTEXT_KEY)
    if not isinstance(raw, dict):
        return None
    run_id = str(raw.get("run_id") or "")
    eu_type = str(raw.get("eu_type") or "").lower()
    if not run_id or not eu_type:
        return None
    return run_id, eu_type


class ResumeNotReconstructible(Exception):
    """Raised when a caller *requires* a rebuild and the run cannot supply one.

    Separate from the `None` return of :func:`build_resume_callback` on purpose. Reading a
    row and finding it is not in a resumable state is an ordinary, expected answer — a run
    that already completed, or was claimed by another instance, is not an error. Being
    *asked* to reconstruct something that cannot be reconstructed, by a caller with no
    fallback (a distributed transport, which has already discarded the closure), is.
    """


def _build_flow(run_id: str, db: "Session") -> Optional[Callable[[], None]]:
    """Rebuild a flow resume from the `FlowRun` row. Every argument comes off that row."""
    from AINDY.core.flow_run_rehydration import build_flow_resume_callback
    from AINDY.db.models.execution_unit import ExecutionUnit
    from AINDY.db.models.flow_run import FlowRun

    run = db.query(FlowRun).filter(FlowRun.id == str(run_id)).first()
    if run is None:
        logger.info("[resume_rebuild] flow run %s not found", run_id)
        return None

    # ★★ REFUSE UP FRONT WHEN THE FLOW IS NOT REGISTERED IN *THIS* PROCESS.
    #
    # The built callback checks `FLOW_REGISTRY` itself and, finding nothing, logs a warning
    # and **returns normally** — which is right for the rehydration sweep (a flow that is not
    # ours is legitimately skipped) and catastrophic here. A caller that has already discarded
    # the closure sees a clean return, ACKs the message, and reports the resume completed while
    # the run stays `waiting` forever. That is the fourth distinct way this path has produced a
    # silent loss.
    #
    # It is not hypothetical across a process boundary. `register_flow()` only COLLECTS
    # registration functions; `register_flows()` invokes them, and that is what fills
    # FLOW_REGISTRY. Whether a given process has done the second step is a property of that
    # process, so the API can hold a flow the worker does not.
    #
    # Returning None here turns that into `ResumeNotReconstructible` at `require_resume_callback`,
    # so the message is dead-lettered and visible rather than acknowledged and gone.
    from AINDY.runtime.flow_engine import FLOW_REGISTRY

    if run.flow_name not in FLOW_REGISTRY:
        logger.warning(
            "[resume_rebuild] flow %r is not registered in this process; run %s cannot be "
            "resumed here (registered: %s)",
            run.flow_name, run_id, sorted(FLOW_REGISTRY) or "none",
        )
        return None

    # ★ The EU is reached through ``ExecutionUnit.flow_run_id`` — there is no
    # ``FlowRun.execution_unit_id`` column. The sweep resolves it the same way and treats a
    # miss as fine ("EU context is optional"), so "" is a legitimate value here and not an
    # error. Worth stating because the plausible-looking `getattr(run, "execution_unit_id",
    # "")` compiles, always yields "", and silently produces a resume that skips the EU
    # transition — a rebuilt resume that looks correct and half-executes.
    eu = (
        db.query(ExecutionUnit)
        .filter(ExecutionUnit.flow_run_id == str(run.id))
        .first()
    )

    return build_flow_resume_callback(
        r_id=str(run.id),
        flow_name=run.flow_name,
        user_id=run.user_id,
        workflow_type=run.workflow_type or "flow",
        eid=str(eu.id) if eu else "",
    )


def _build_agent(run_id: str, db: "Session") -> Optional[Callable[[], None]]:
    """Rebuild an agent resume from the `AgentRun` row.

    ★ `next_segment_index` comes from the row's own `wait_state`, not from a counter held
    anywhere in this process. That is the whole reason an agent resume survives a restart
    today, and it is what makes this rebuild identical to the one the sweep performs.
    """
    from AINDY.db.models import AgentRun
    from AINDY.runtime.agent_plan_compiler import split_agent_plan
    from AINDY.runtime.nodus_execution_service import _build_agent_resume_callback

    run = db.query(AgentRun).filter(AgentRun.id == str(run_id)).first()
    if run is None:
        logger.info("[resume_rebuild] agent run %s not found", run_id)
        return None

    wait_state = dict(run.wait_state or {})

    # ★ Segments come from ``split_agent_plan``, not from a raw ``plan["segments"]`` read —
    # the split is what defines a segment boundary, and reading the plan directly would
    # produce a different decomposition than the one the run was suspended against.
    try:
        segments = split_agent_plan(run.plan or {})
    except ValueError:
        logger.info("[resume_rebuild] agent run %s has no splittable plan", run_id)
        return None
    if not segments:
        logger.info("[resume_rebuild] agent run %s split to zero segments", run_id)
        return None

    next_segment_index = int(wait_state.get("resume_segment_index") or 0)
    if not 0 <= next_segment_index < len(segments):
        logger.info(
            "[resume_rebuild] agent run %s resume_segment_index %d out of range (%d segments)",
            run_id, next_segment_index, len(segments),
        )
        return None

    return _build_agent_resume_callback(
        run_id=str(run.id),
        segments=segments,
        next_segment_index=next_segment_index,
        accumulated=list((run.result or {}).get("steps") or []),
        user_id=str(run.user_id),
        correlation_id=run.correlation_id,
        scoped_token=run.capability_token,
        total_tool_steps=sum(len(s["tool_steps"]) for s in segments),
    )


#: One builder per reconstructible type. A registry rather than an `if` chain because the
#: set of types that can cross a process boundary is a fact worth being able to read —
#: `RECONSTRUCTIBLE_EU_TYPES` is what a transport checks *before* it discards the closure.
_BUILDERS: dict[str, Callable[[str, "Session"], Optional[Callable[[], None]]]] = {
    "flow": _build_flow,
    "agent": _build_agent,
}


def build_resume_callback(
    *,
    run_id: str,
    eu_type: str,
    db: "Session",
) -> Optional[Callable[[], None]]:
    """Return the zero-argument call that resumes ``run_id``, or ``None``.

    ``None`` means *this run cannot be resumed from its identifier right now* — it does not
    exist, it is not in a resumable state, or its type has no resume semantics. That is an
    answer, not a failure; the caller decides what to do with it.

    The returned closure performs its own atomic claim when it fires, so calling this twice
    and running both results is safe: exactly one wins. This function does **not** claim,
    which is deliberate — building a callable must not have a side effect on the run, or a
    transport that builds one and then fails to enqueue it would strand the run in
    ``executing`` with nothing driving it.
    """
    key = str(eu_type or "").lower()
    builder = _BUILDERS.get(key)
    if builder is None:
        logger.debug("[resume_rebuild] no builder for eu_type=%r (run=%s)", eu_type, run_id)
        return None

    try:
        return builder(str(run_id), db)
    except Exception as exc:  # noqa: BLE001 — a rebuild failure must not kill the caller
        logger.warning(
            "[resume_rebuild] failed to rebuild %s resume for run=%s: %s", key, run_id, exc
        )
        return None


def require_resume_callback(
    *,
    run_id: str,
    eu_type: str,
    db: "Session",
) -> Callable[[], None]:
    """:func:`build_resume_callback`, but raises rather than returning ``None``.

    For callers that have already given up the closure and have no fallback — a distributed
    transport is the motivating one. There, ``None`` cannot be handled gracefully: the work
    is neither carried nor rebuildable, and enqueueing it anyway produces the silent loss
    this whole module exists to prevent. Fail loudly at the seam instead.
    """
    callback = build_resume_callback(run_id=run_id, eu_type=eu_type, db=db)
    if callback is None:
        raise ResumeNotReconstructible(
            f"cannot rebuild a resume for run_id={run_id!r} eu_type={eu_type!r}. "
            f"Reconstructible types are {sorted(RECONSTRUCTIBLE_EU_TYPES)}; the run must "
            f"exist and be in a resumable state. A caller that has already discarded the "
            f"live callback cannot proceed past this — routing it onward would enqueue "
            f"work no worker can resolve, and an unresolvable job is acknowledged as "
            f"successfully completed."
        )
    return callback
