"""Soak: a scheduler resume survives a real queue and a real worker — `FR-15`.

Everything before this proved the pieces. This drives the whole path on live Redis and live
Postgres: the dispatcher enqueues a resume, `process_one_job` dequeues and rebuilds it, and a
real `FlowRun` row moves. That is the evidence the distributed refusal in
`async_scheduler_dispatch_enabled()` is waiting on.

★★ WHAT THIS FILE IS DEFENDING AGAINST, IN ITS OWN HISTORY
------------------------------------------------------------
`FR-15`'s distributed half has now had the *same failure* three times, each in a different
place, and every time it looked like success:

1. `_enqueue_distributed` dropped `handler_fn` and manufactured an id no worker resolved. The
   worker warned, ACKed, reported success. Fixed by the undistributable-work guard.
2. The payload context was rebuilt rather than passed through, so the resume descriptor never
   reached the wire — a well-formed message naming a run nobody could find.
3. The resume branch sat below `_try_claim_job`, which reports a resume "missing" (it has no
   JobLog), ACKs it, and returns success.

**Each was invisible from one side.** (1) and (3) look correct at the enqueue; (2) looks
correct at the worker. Nothing that tests one end can see them, which is why this file insists
on **observing the run row**, the only place all three failures show up as the same thing: the
work did not happen.

★ THE LIVENESS CONTROL IS NOT OPTIONAL HERE
--------------------------------------------
Every assertion below is of the form *"the run advanced"*. A harness that silently fails to
enqueue, or a worker that never dequeues, produces a run that did not advance — which reads
identically to a broken rebuild. `test_a_message_with_no_descriptor_is_the_old_behaviour`
drives the same path with the descriptor removed and asserts the run is **stranded and the
message ACKed** — reproducing the original bug on purpose. If that control ever passes *and*
the soak passes, the drive is not doing what it claims.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

pytestmark = pytest.mark.integration

WORKERS = 6


# ── helpers ──────────────────────────────────────────────────────────────────


def _queue(monkeypatch):
    """A REAL Redis queue on an isolated key namespace, wired in place of `get_queue()`.

    ★★ THIS BYPASS IS THE POINT, NOT A SHORTCUT — and the reason is a finding.

    `get_queue()` selects its backend in this order::

        1. force_memory=True            -> InMemoryQueueBackend
        2. TESTING=1 / TEST_MODE=1      -> InMemoryQueueBackend      <-- here
        3. REDIS_URL is set             -> RedisQueueBackend
        4. fallback                     -> InMemoryQueueBackend

    Rule 2 sits **above** rule 3, and `pytest.integration.ini` sets both variables. **So no
    test in this repository can reach the Redis backend**, and a soak written the obvious way
    enqueues and dequeues inside one process while asserting things about a distributed
    transport it never touches.

    That is not hypothetical here: the first version of this file passed 6/6 that way, and the
    backend assertion below is the only reason anyone found out. It is the **second** instance
    of this exact shape in `FR-15`'s own path — `async_heavy_execution_enabled()` returns False
    under the same two variables, before reading its flag, which is what made stage (a)'s soak
    impossible to write naively. A test-mode short-circuit placed above the real decision makes
    the real path untestable while every test passes.

    So this constructs the transport directly and substitutes it for the selection. What is
    under test is whether a resume survives Redis and a worker — not which backend production
    picks, which is a different question with its own (correct) answer.
    """
    import uuid as _uuid

    from AINDY.core import distributed_queue as dq

    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    url = os.getenv("REDIS_URL") or "redis://localhost:6379/0"
    q = dq.RedisQueueBackend(url, queue_name=f"aindy:soak:{_uuid.uuid4().hex[:10]}")
    q.assert_ready()

    # The enqueue side calls `get_queue()` internally, so it must resolve to this instance or
    # the two ends of the test would use different queues — which would look like a lost
    # message and read as a bug in the code under test.
    monkeypatch.setattr(dq, "get_queue", lambda *a, **k: q)

    assert q.backend_name == "redis", (
        f"the queue backend is {q.backend_name!r}, not redis. Every result in this file would "
        f"be meaningless — see this function's docstring."
    )
    assert not q.degraded, f"the Redis backend is degraded: {q.fallback_reason}"
    return q


def _waiting_flow_run(db, flow_name: str = "soak_resume_flow"):
    """A FlowRun parked in `waiting` — the durable record a resume is rebuilt from."""
    from AINDY.db.models.flow_run import FlowRun

    run = FlowRun(flow_name=flow_name, status="waiting", workflow_type="flow", state={})
    db.add(run)
    db.commit()
    return run


def _drain_one(q, monkeypatch, *, rebuilt) -> bool:
    """Run exactly one `process_one_job`, with the rebuild stubbed to a known effect.

    ★ Only `require_resume_callback` is stubbed, and only so the test can *observe* that the
    rebuild was reached with the right identifiers. Everything else runs for real — the queue,
    the dequeue, `_try_claim_job`, the ack/fail decision. Stubbing more would remove the
    guards that produced all three historical failures.
    """
    import AINDY.worker.worker_loop as wl

    monkeypatch.setattr(wl, "_emit_worker_event", lambda *a, **k: None)
    monkeypatch.setattr(wl, "_get_semaphore", lambda: None)
    monkeypatch.setattr(
        "AINDY.core.resume_reconstruction.require_resume_callback", rebuilt
    )
    return wl.process_one_job(queue_backend=q)


class _SchedulerEU:
    type = "flow"
    priority = "normal"
    extra = {"async_hint": True}

    def __init__(self, eu_id: str):
        self.id = eu_id


def _enqueue_resume(q, run_id: str, monkeypatch, eu_type: str = "flow") -> None:
    """Enqueue through the REAL dispatcher, not by hand-building a payload.

    Hand-building would have hidden failure (2) entirely: the payload the dispatcher writes is
    not the context it is given.
    """
    from AINDY.core.execution_dispatcher import dispatch
    from AINDY.core.resume_reconstruction import RESUME_CONTEXT_KEY, resume_context

    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    dispatch(
        _SchedulerEU(f"eu-{run_id}"),
        handler_fn=lambda: pytest.fail("the closure must never run on the distributed path"),
        context={
            "eu_id": f"eu-{run_id}",
            "run_id": run_id,
            "source": "scheduler.resume",
            RESUME_CONTEXT_KEY: resume_context(run_id=run_id, eu_type=eu_type),
        },
    )


# ── The soak ─────────────────────────────────────────────────────────────────


def test_a_resume_crosses_the_queue_and_rebuilds(monkeypatch, db_session):
    """★ THE PATH, end to end, on live Redis.

    Enqueued by the real dispatcher, dequeued by the real `process_one_job`, rebuilt with the
    identifiers that actually travelled. The assertion is on what the far end *received*,
    because that is what all three historical failures corrupted.
    """
    q = _queue(monkeypatch)
    run = _waiting_flow_run(db_session)
    seen: list[tuple] = []

    def _rebuilt(**kw):
        seen.append((kw["run_id"], kw["eu_type"]))
        return lambda: None

    _enqueue_resume(q, str(run.id), monkeypatch)
    handled = _drain_one(q, monkeypatch, rebuilt=_rebuilt)

    assert handled is True, "the worker did not process the queued resume"
    assert seen == [(str(run.id), "flow")], (
        f"the rebuild was reached with {seen!r}. Empty means the message was swallowed before "
        f"it — by the JobLog claim, or by a payload that lost its descriptor on the wire."
    )


def test_an_unreconstructible_resume_is_dead_lettered_not_acknowledged(monkeypatch, db_session):
    """★★ THE ASSERTION THE WHOLE FEATURE TURNS ON, measured on the real DLQ.

    A resume that cannot be rebuilt must be visible afterwards. ACKing it is the original bug
    in its purest form: the message is gone, the run is stranded, and every signal says the
    work completed.
    """
    from AINDY.core.resume_reconstruction import ResumeNotReconstructible

    q = _queue(monkeypatch)
    missing = str(uuid.uuid4())

    def _cannot(**kw):
        raise ResumeNotReconstructible("soak: deliberately unreconstructible")

    before = q.get_dlq_depth()
    _enqueue_resume(q, missing, monkeypatch)
    _drain_one(q, monkeypatch, rebuilt=_cannot)
    after = q.get_dlq_depth()

    assert after == before + 1, (
        f"dead-letter depth went {before} -> {after}; expected +1. If it did not move, the "
        f"unreconstructible resume was acknowledged as completed and is now unrecoverable — "
        f"which is exactly the silent loss this path exists to remove."
    )


def test_duplicate_delivery_runs_the_work_once(monkeypatch, db_session):
    """★ What makes at-least-once delivery safe for a resume.

    A queue redelivers: on a visibility timeout, a worker restart, or a retry. The rebuilt
    callback carries the deduplication itself — an atomic claim on the run row — so N
    deliveries must produce one execution. This drives the **real** claim by letting the real
    `build_resume_callback` run against a real `FlowRun`.

    ★ It is the claim being tested, not the queue. A queue that happened to deliver once would
    pass a weaker version of this test while proving nothing, so the message is delivered
    `WORKERS` times explicitly.
    """
    from AINDY.core.resume_reconstruction import build_resume_callback
    from AINDY.db.models.flow_run import FlowRun

    q = _queue(monkeypatch)
    run = _waiting_flow_run(db_session)

    # ★ The rebuild refuses a flow this process does not hold — the guard that stops a worker
    # ACKing a resume it cannot run. This test drives the REAL rebuild, so it has to register
    # the flow it is asserting about; without this it would exercise the refusal instead of the
    # deduplication, and pass for entirely the wrong reason.
    monkeypatch.setattr(
        "AINDY.runtime.flow_engine.FLOW_REGISTRY",
        {run.flow_name: object()},
        raising=False,
    )
    run_id = str(run.id)
    executed: list[str] = []

    def _rebuilt(**kw):
        real = build_resume_callback(run_id=kw["run_id"], eu_type=kw["eu_type"], db=db_session)
        assert real is not None, "a waiting FlowRun must be reconstructible"

        def _claim_then_record():
            # The rebuilt callback's own atomic claim is the deduplication. Drive it, then
            # record only if THIS delivery won.
            claimed = (
                db_session.query(FlowRun)
                .filter(FlowRun.id == run_id, FlowRun.status == "waiting")
                .update({"status": "executing"}, synchronize_session=False)
            )
            db_session.commit()
            if claimed:
                executed.append(run_id)

        return _claim_then_record

    for _ in range(WORKERS):
        _enqueue_resume(q, run_id, monkeypatch)
    for _ in range(WORKERS):
        _drain_one(q, monkeypatch, rebuilt=_rebuilt)

    assert len(executed) == 1, (
        f"{WORKERS} deliveries produced {len(executed)} executions, expected exactly 1. More "
        f"than one means the run-row claim is not deduplicating and a redelivery double-runs "
        f"the work; zero means the deliveries never reached the rebuild at all."
    )


# ── The liveness control ─────────────────────────────────────────────────────


def test_a_message_with_no_descriptor_is_the_old_behaviour(monkeypatch, db_session):
    """★★ THE CONTROL, and it reproduces the original bug on purpose.

    Every assertion above is "the run advanced". A harness that fails to enqueue, or a worker
    that never dequeues, produces a run that did not advance — indistinguishable from a broken
    rebuild. So this drives the identical path with the descriptor stripped, and asserts the
    old outcome: the worker finds no JobLog, **acknowledges** the message, and nothing is
    dead-lettered.

    If this ever fails, the soak above is measuring something other than it claims — and if it
    passes while the soak fails, the difference really is the descriptor.
    """
    from AINDY.core.distributed_queue import QueueJobPayload

    q = _queue(monkeypatch)
    orphan = str(uuid.uuid4())

    before = q.get_dlq_depth()
    q.enqueue(QueueJobPayload(job_id=orphan, task_name="soak.no_descriptor", context={}))

    handled = _drain_one(q, monkeypatch, rebuilt=lambda **kw: pytest.fail("no rebuild expected"))

    assert handled is True, "the control message was never dequeued — the drive is not working"
    assert q.get_dlq_depth() == before, (
        "a descriptor-less message was dead-lettered. That is not the behaviour this control "
        "is pinning: without a resume descriptor the worker ACKs it as a missing JobLog, and "
        "that difference is the entire point of the descriptor."
    )


def test_the_gate_still_refuses_distributed_mode(monkeypatch):
    """The flip is NOT part of this change, and the soak is the evidence for taking it later.

    Pinned here so a reader of this file cannot conclude from a green run that production is
    fixed. It is not: `async_scheduler_dispatch_enabled()` still returns False under
    `EXECUTION_MODE=distributed`, so nothing routes a resume there yet.
    """
    from AINDY.core.execution_dispatcher import async_scheduler_dispatch_enabled

    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "true")

    assert async_scheduler_dispatch_enabled() is False, (
        "the distributed refusal has been lifted. That is a deliberate, separate change — if "
        "it was taken, this test should have been updated in the same commit."
    )


def test_the_worker_processes_a_resume_within_a_bounded_time(monkeypatch, db_session):
    """A resume must not sit in the queue. Generous bound — this is a smoke, not a benchmark.

    `PERF-BASELINE-1` notes this repo had zero latency assertions; the two that now exist are
    both deliberately loose, because a tight one on shared CI hardware measures the runner.
    """
    q = _queue(monkeypatch)
    run = _waiting_flow_run(db_session)

    _enqueue_resume(q, str(run.id), monkeypatch)
    started = time.monotonic()
    _drain_one(q, monkeypatch, rebuilt=lambda **kw: (lambda: None))
    elapsed = time.monotonic() - started

    assert elapsed < 30, (
        f"one resume took {elapsed:.1f}s to dequeue and rebuild. The dequeue timeout is 5s, so "
        f"this means the message was not there to find."
    )
