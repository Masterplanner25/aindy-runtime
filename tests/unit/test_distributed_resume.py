"""`FR-15` stage 2 — a resume survives the distributed transport.

Stage 1 made a resume rebuildable from its run id. This wires it: the scheduler carries a
two-identifier descriptor, the dispatcher enqueues it in place of a closure it cannot
serialise, and the worker rebuilds it at the far end with the same call `rehydrate_waiting_*`
makes on every boot.

★★ THE FAILURE THIS REPLACES, AND WHY IT WAS INVISIBLE
-------------------------------------------------------
Before stage 1, routing a scheduler resume through distributed mode produced a payload keyed
on an id no worker could resolve. `worker_loop` treats an unresolvable job as **finished**::

    job_data = _fetch_job_data(job.job_id)
    if job_data is None:
        logger.warning("[Worker] JobLog not found job_id=%s", job.job_id)
        q.ack(job.job_id)      # ACKed, not dead-lettered
        return True            # reported as SUCCESS

A warning, an ack, and a success — while the run sits `waiting` forever with no dead-letter
entry and nothing to retry. **That is why the resume branch is checked BEFORE
`_fetch_job_data`**: a resume legitimately has no JobLog, so reaching that lookup at all
means falling into the silent-loss path.

★ THE DISTINCTION THE WORKER HAS TO GET RIGHT
----------------------------------------------
Two outcomes look similar and must be handled oppositely:

- **Cannot be rebuilt** → dead-letter. This is the failure above; acking it is the bug.
- **Rebuilt, but the claim was lost to another instance** → *success*. The rebuilt closure
  performs its own atomic claim, so a duplicate delivery is a no-op **by design**. Failing
  these would fill the DLQ with correctly-deduplicated messages and bury the real losses.

The tests below pin both directions, because a worker that dead-letters everything passes
any test that only checks the first.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only


class _ResumeEU:
    """A scheduler-shaped execution unit: heavy, and carrying no JobLog."""

    type = "flow"
    priority = "normal"
    id = "eu-resume-1"
    extra = {"async_hint": True}


def _ctx(run_id: str = "run-1", eu_type: str = "flow") -> dict:
    from AINDY.core.resume_reconstruction import RESUME_CONTEXT_KEY, resume_context

    return {
        "eu_id": "eu-resume-1",
        "run_id": run_id,
        "source": "scheduler.resume",
        RESUME_CONTEXT_KEY: resume_context(run_id=run_id, eu_type=eu_type),
    }


# ── The enqueue side ─────────────────────────────────────────────────────────


def test_a_resume_is_accepted_by_the_guard_without_a_log_id(monkeypatch):
    """★ The guard's question is "can the far end rebuild this?", not "is there a log_id?".

    Stage 1's guard refused anything without a `log_id`, which was correct when a JobLog was
    the only durable record a worker could resolve. A resume has a different one — its own
    run row — so it answers the same question a different way.
    """
    from AINDY.core.execution_dispatcher import ExecutionMode, dispatch

    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    queue = MagicMock()

    with patch("AINDY.core.distributed_queue.get_queue", return_value=queue):
        result = dispatch(_ResumeEU(), handler_fn=lambda: None, context=_ctx())

    assert result.mode is ExecutionMode.ASYNC
    assert queue.enqueue.call_count == 1, "a reconstructible resume must be enqueued"


def test_the_enqueued_resume_carries_the_run_not_the_closure(monkeypatch):
    """What actually goes on the wire.

    `handler_fn` is deliberately something that would be obvious if it somehow travelled —
    it cannot, and the assertion is that the payload identifies the *run* instead.
    """
    from AINDY.core.execution_dispatcher import dispatch
    from AINDY.core.resume_reconstruction import RESUME_TASK_NAME, read_resume_context

    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    queue = MagicMock()

    with patch("AINDY.core.distributed_queue.get_queue", return_value=queue):
        dispatch(_ResumeEU(), handler_fn=lambda: 1 / 0, context=_ctx("run-42", "flow"))

    payload = queue.enqueue.call_args[0][0]
    assert payload.task_name == RESUME_TASK_NAME
    assert payload.job_id == "run-42", (
        "a resume must key on its run id — that is what makes the message identifiable and "
        "gives idempotency_key a meaningful default"
    )
    assert read_resume_context(payload.context) == ("run-42", "flow")


def test_a_malformed_resume_descriptor_is_still_refused(monkeypatch):
    """★ The guard must not be satisfied by the *presence* of a resume key.

    A descriptor missing its run id is not a durable record of anything. Accepting it because
    the key exists would reopen the silent loss through a new door — the message would be
    enqueued, and the far end would have nothing to rebuild from.
    """
    from AINDY.core.execution_dispatcher import UndistributableWorkError, dispatch
    from AINDY.core.resume_reconstruction import RESUME_CONTEXT_KEY

    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    queue = MagicMock()

    for broken in ({}, {"eu_type": "flow"}, {"run_id": "", "eu_type": "flow"}, "nonsense"):
        with patch("AINDY.core.distributed_queue.get_queue", return_value=queue):
            with pytest.raises(UndistributableWorkError):
                dispatch(
                    _ResumeEU(),
                    handler_fn=lambda: None,
                    context={"eu_id": "e", RESUME_CONTEXT_KEY: broken},
                )
    assert queue.enqueue.call_count == 0


def test_thread_mode_still_runs_the_closure_and_ignores_the_descriptor(monkeypatch):
    """★ The descriptor is carried unconditionally; it must change nothing in thread mode.

    The scheduler cannot know which transport applies — the dispatcher decides — so it always
    supplies the descriptor. If that changed thread-mode behaviour, `FR-15 (a)`'s shipped fix
    would regress under a flag, which is the hardest kind of regression to attribute.
    """
    from AINDY.core.execution_dispatcher import dispatch

    monkeypatch.setenv("EXECUTION_MODE", "thread")
    ran: list[int] = []

    result = dispatch(_ResumeEU(), handler_fn=lambda: ran.append(1), context=_ctx())
    assert result.future is not None
    result.future.result(timeout=30)
    assert ran == [1], "thread mode must still execute the carried closure"


def test_the_scheduler_itself_supplies_the_descriptor(monkeypatch):
    """★ ADDED AFTER A SURVIVING MUTATION, and the gap was real.

    Every other test in this file builds the context by hand, so deleting the scheduler's
    descriptor construction outright changed nothing they could see — a mutation that removed
    it passed the whole file. The tests were exercising the transport and the worker while
    quietly assuming the thing that feeds them.

    This drives `schedule()` and reads the context the scheduler actually built.
    """
    from AINDY.core.resume_reconstruction import read_resume_context
    from AINDY.kernel.scheduler.common import ScheduledItem
    from AINDY.kernel.scheduler_engine import SchedulerEngine

    monkeypatch.setenv("EXECUTION_MODE", "thread")
    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "false")

    engine = SchedulerEngine()
    engine.enqueue(
        ScheduledItem(
            execution_unit_id="eu-s",
            tenant_id="system",
            priority="normal",
            run_callback=lambda: None,
            run_id="run-sched-1",
            eu_type="flow",
        )
    )

    seen: list[dict] = []

    def _spy(execution_unit, handler_fn, context=None):
        seen.append(dict(context or {}))
        handler_fn()
        from AINDY.core.execution_dispatcher import DispatchResult, ExecutionMode

        return DispatchResult(mode=ExecutionMode.INLINE, envelope=None, meta=context or {})

    with patch("AINDY.core.execution_dispatcher.dispatch", _spy):
        engine.schedule(tick_waits=False)

    assert seen, "the scheduler did not dispatch the queued item"
    assert read_resume_context(seen[0]) == ("run-sched-1", "flow"), (
        f"the scheduler built a context with no usable resume descriptor: {seen[0]}. In "
        f"distributed mode the closure is dropped, so this descriptor is the entire message "
        f"— without it the transport has nothing to enqueue and the run is stranded."
    )


# ── The worker side ──────────────────────────────────────────────────────────


class _Job:
    job_id = "run-42"
    task_name = "__resume__"
    idempotency_key = "run-42"

    def __init__(self, context):
        self.context = context


def test_worker_rebuilds_and_acks_a_resume():
    """The happy path — and note it never reaches `_fetch_job_data`."""
    import AINDY.worker.worker_loop as wl

    ran: list[int] = []
    q = MagicMock()

    with patch.object(wl, "_emit_worker_event", lambda *a, **k: None), \
            patch("AINDY.db.database.SessionLocal", return_value=MagicMock()), \
            patch(
                "AINDY.core.resume_reconstruction.require_resume_callback",
                return_value=lambda: ran.append(1),
            ):
        wl._run_resume(_Job(_ctx()), ("run-42", "flow"), q, trace_id="t", eu_id="e")

    assert ran == [1], "the rebuilt resume must actually run"
    assert q.ack.call_count == 1
    assert q.fail.call_count == 0


def test_worker_dead_letters_a_resume_it_cannot_rebuild():
    """★★ THE ASSERTION THIS WHOLE STAGE EXISTS FOR.

    An unreconstructible resume must go to the dead-letter queue. Acking it is the exact
    failure `FR-15`'s distributed half was blocked on: warned about, acknowledged, reported
    complete, run stranded `waiting` forever with nothing to retry.
    """
    import AINDY.worker.worker_loop as wl
    from AINDY.core.resume_reconstruction import ResumeNotReconstructible

    q = MagicMock()

    with patch.object(wl, "_emit_worker_event", lambda *a, **k: None), \
            patch("AINDY.db.database.SessionLocal", return_value=MagicMock()), \
            patch(
                "AINDY.core.resume_reconstruction.require_resume_callback",
                side_effect=ResumeNotReconstructible("nope"),
            ):
        wl._run_resume(_Job(_ctx()), ("run-42", "flow"), q, trace_id="t", eu_id="e")

    assert q.fail.call_count == 1, (
        "an unreconstructible resume was not dead-lettered. If it is ACKed instead, the run "
        "is stranded silently — the precise failure this stage removes."
    )
    assert q.ack.call_count == 0, "it must NOT be acknowledged as completed"


def test_a_resume_never_reaches_the_joblog_lookup():
    """★ Placement, asserted rather than assumed.

    A resume has no JobLog by construction. If the branch sat *after* `_fetch_job_data`, the
    lookup would miss and the miss path acks as success — so the ordering is the guarantee,
    not an optimisation.
    """
    from pathlib import Path

    src = Path("AINDY/worker/worker_loop.py").read_text(encoding="utf-8")
    resume_at = src.index("resume = read_resume_context(job.context)")
    fetch_at = src.index("job_data = _fetch_job_data(job.job_id)")
    assert resume_at < fetch_at, (
        "the resume branch moved after the JobLog lookup. A resume has no JobLog, and a "
        "missing JobLog is ACKed as success — the silent loss is back."
    )


def test_the_resume_task_name_is_not_a_registered_handler():
    """A collision would route a resume into a job handler, or a job into the rebuild."""
    from AINDY.core.resume_reconstruction import RESUME_TASK_NAME
    from AINDY.platform_layer import async_job_service

    registry = getattr(async_job_service, "_JOB_REGISTRY", {})
    assert RESUME_TASK_NAME not in registry, (
        f"{RESUME_TASK_NAME!r} is a registered job handler; the reserved resume name must "
        f"not be dispatchable as ordinary work"
    )
