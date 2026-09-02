"""Soak: FR-15 (a) — does the scheduler actually stop blocking on the work it dispatches?

`FR-15`'s defect is not latency, it is **serialisation**. `_scheduler_heartbeat_tick` is the
only thing that drains the queue, it is registered `max_instances=1`, and `schedule()` runs
each item synchronously. So one slow flow holds the single slot and nothing else — not
another flow, not wait expiry, not stale-wait cleanup — gets a turn. That is how a request
waited 177 seconds and how `/health` stayed down for 13 minutes.

The property this file measures is therefore **"the drainer is free again"**, not "the work
finished faster". Those are different claims and only the first is what the flip buys.

★★ WHY THIS SOAK COULD NOT BE WRITTEN THE OBVIOUS WAY
------------------------------------------------------
`async_heavy_execution_enabled()` returns False under `TESTING`/`TEST_MODE` **before** it
reads its flag, and `pytest.integration.ini` sets both *and* pins the flag false. Every test
in this repository has therefore been unable to reach the ASYNC branch, and always was. A
soak that did what the `IDEM-11` soak does — `monkeypatch.setenv(FLAG, "true")`, drive load,
assert — would have passed while executing the INLINE branch from start to finish.

That is the catalogue's **variant 3 arriving as variant 6**: the path is not merely
unexercised, it is actively neutralised two lines above the switch the test appears to flip.
`async_scheduler_dispatch_enabled()` honours an explicit opt-in that test mode cannot veto,
which is the only reason evidence for the flip is obtainable at all.

★ WHAT THIS SOAK DELIBERATELY DOES NOT COVER
---------------------------------------------
**Distributed mode**, which is the production shape (`docker-compose.prod.yml` sets
`EXECUTION_MODE: distributed`). There, `dispatch()`'s ASYNC branch calls
`_enqueue_distributed()`, which drops `handler_fn` — a closure cannot cross a process
boundary, and the scheduler's work *is* the closure `item.run_callback`. The worker would
find no JobLog for the eu_id, warn, **ack**, and return success while the resume never runs.
`async_scheduler_dispatch_enabled()` refuses distributed mode outright for that reason, and
`test_the_gate_refuses_distributed_mode` below pins the refusal *here* as well as in the unit
suite — because this is the file someone reads before deciding the soak was sufficient.

So: green here is evidence for thread-mode deployments only. It says nothing about the
deployment that filed FR-15.
"""
from __future__ import annotations

import threading
import time

import pytest

from tests.integration.soak_harness import metric_window

pytestmark = pytest.mark.integration

#: Kept under `MAX_PER_SCHEDULE_CYCLE` (10) so one `schedule()` call drains the whole batch,
#: and under `AINDY_ASYNC_JOB_WORKERS` (10) so the pool cannot itself serialise the soak and
#: produce a flattering-looking failure that reads as a scheduler problem.
WORKERS = 8

#: Per-item sleep for the INLINE control. Small, because the assertion it feeds is a *lower*
#: bound (serial execution cannot beat the sum), which is the only timing claim in this file.
INLINE_ITEM_SECONDS = 0.25

#: Fast-fail ceiling for `schedule()` itself under the gate. Handing `WORKERS` closures to a
#: ThreadPoolExecutor is sub-millisecond work; this is three orders of magnitude of headroom,
#: sized to catch an INLINE regression in seconds rather than to measure anything.
SUBMIT_CEILING = 5.0

METRIC = "aindy_execution_dispatch_total"


def _enqueue(engine, callback, *, n: int = WORKERS) -> None:
    from AINDY.kernel.scheduler.common import ScheduledItem

    for i in range(n):
        engine.enqueue(
            ScheduledItem(
                execution_unit_id=f"eu-soak-{i}",
                tenant_id="system",
                priority="normal",
                run_callback=callback,
                run_id=f"run-soak-{i}",
                eu_type="flow",
            )
        )


def _engine(monkeypatch, *, gate: bool):
    """A fresh engine with the scheduler gate deliberately set.

    `EXECUTION_MODE` is pinned to `thread` rather than left to default. It would already
    resolve that way outside prod, but an inherited `distributed` would make the gate refuse
    and every assertion below would go vacuous while still reading like a pass.
    """
    from AINDY.core.execution_dispatcher import async_scheduler_dispatch_enabled
    from AINDY.kernel.scheduler_engine import SchedulerEngine

    monkeypatch.setenv("EXECUTION_MODE", "thread")
    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "true" if gate else "false")

    assert async_scheduler_dispatch_enabled() is gate, (
        f"the gate did not take the value this soak set (wanted {gate}). Every assertion "
        f"below would be measuring the other branch."
    )
    return SchedulerEngine()


# ── The control: proves the soak below is measuring a change ─────────────────


def test_inline_drain_blocks_the_drainer_for_the_sum_of_its_items(monkeypatch):
    """★ LIVENESS + BASELINE — this is the defect, reproduced.

    Two things are asserted because either alone is weak:

    1. **One thread.** All items ran on the caller's, so the drainer was occupied throughout.
    2. **Serial wall-clock.** `schedule()` took at least the sum of the item durations.

    Without (1) a thread-pool run that happened to be slow would satisfy (2). Without (2) an
    implementation that ran everything on one thread *instantly* would satisfy (1). Together
    they say the drainer was blocked, which is the thing FR-15 is about.
    """
    engine = _engine(monkeypatch, gate=False)
    threads: list[int] = []

    def _callback() -> None:
        threads.append(threading.get_ident())
        time.sleep(INLINE_ITEM_SECONDS)

    _enqueue(engine, _callback)

    with metric_window(METRIC, labels={"mode": "inline", "eu_type": "flow"}) as inline:
        started = time.monotonic()
        engine.schedule(tick_waits=False)
        elapsed = time.monotonic() - started

    assert len(threads) == WORKERS, f"expected {WORKERS} items drained, got {len(threads)}"
    assert set(threads) == {threading.get_ident()}, (
        "inline drain used a thread other than the caller's — the control is not measuring "
        "the INLINE branch and the soak has nothing to be compared against"
    )

    serial_floor = WORKERS * INLINE_ITEM_SECONDS * 0.9
    assert elapsed >= serial_floor, (
        f"inline drain finished in {elapsed:.2f}s, under the {serial_floor:.2f}s floor for "
        f"{WORKERS} serial items. Either the items did not run or this is not serial — both "
        f"make the soak's comparison meaningless."
    )

    inline.assert_increased(METRIC, by_at_least=WORKERS)


# ── The soak ─────────────────────────────────────────────────────────────────


def test_async_drain_returns_while_the_work_is_still_running(monkeypatch):
    """★ THE PROPERTY THE FLIP BUYS, and it is asserted without a timing race.

    Every item blocks on an Event the test holds shut. If `schedule()` returns while all
    `WORKERS` items are still parked in their callbacks, the drainer provably did not wait on
    them — there is no threshold to tune and no way for a slow machine to produce a false
    pass. A still-INLINE implementation cannot reach the assertion at all: it would block
    inside `schedule()` on the first item and the test would fail on the timeout, loudly.

    ★ Both metric labels are read. Asserting only that `async` moved would let a partial
    rewire through — half the batch going to the pool and half still blocking the drainer
    reads as success on the async counter alone. `inline` must stay flat.

    ★ `schedule()` is driven on its OWN thread purely so a regression reports fast. Bounding
    its wall-clock *after* calling it cannot fast-fail — the call is the thing that blocks,
    and an INLINE regression took **277 seconds** to surface that way (measured, by mutating
    the fix out). Watching it from another thread reports in `SUBMIT_CEILING` seconds without
    shortening the callback timeouts, which is what keeps the logical assertion threshold-free.
    """
    engine = _engine(monkeypatch, gate=True)

    release = threading.Event()
    entered = threading.Semaphore(0)
    completed: list[int] = []
    lock = threading.Lock()

    def _callback() -> None:
        entered.release()
        release.wait(timeout=30)
        with lock:
            completed.append(threading.get_ident())

    _enqueue(engine, _callback)

    with metric_window(METRIC, labels={"mode": "async", "eu_type": "flow"}) as async_m, \
            metric_window(METRIC, labels={"mode": "inline", "eu_type": "flow"}) as inline_m:
        drained = threading.Event()

        def _drain() -> None:
            try:
                engine.schedule(tick_waits=False)
            finally:
                drained.set()

        drainer = threading.Thread(target=_drain, name="soak-drainer", daemon=True)
        drainer.start()

        if not drained.wait(timeout=SUBMIT_CEILING):
            release.set()  # unblock the callbacks so the run does not hang for minutes
            drainer.join(timeout=60)
            pytest.fail(
                f"schedule() had not returned after {SUBMIT_CEILING}s while all {WORKERS} "
                f"items were parked in their callbacks. Handing closures to a thread pool is "
                f"sub-millisecond work, so this means it is waiting on them: the batch is "
                f"running INLINE and the drainer is still blocked — the FR-15 defect."
            )

        # Every item has entered its callback and none can finish — so if schedule() has
        # returned, it returned without waiting. This is the whole assertion.
        for i in range(WORKERS):
            assert entered.acquire(timeout=15), (
                f"only {i} of {WORKERS} items reached their callback. The scheduler is not "
                f"dispatching the whole batch to the pool."
            )
        assert completed == [], (
            "an item completed before the test released it — the callbacks are not actually "
            "blocked, so 'the drainer returned early' proves nothing"
        )

        release.set()
        drainer.join(timeout=30)
        deadline = time.monotonic() + 30
        while len(completed) < WORKERS and time.monotonic() < deadline:
            time.sleep(0.05)

    assert len(completed) == WORKERS, (
        f"{len(completed)} of {WORKERS} items completed. Work dispatched to the pool was "
        f"lost — the flip would drop resumes."
    )
    assert len(set(completed)) > 1, (
        "all items ran on ONE pool thread. The work left the drainer but did not actually "
        "parallelise, so a single slow item still blocks the rest of the batch."
    )

    async_m.assert_increased(METRIC, by_at_least=WORKERS)
    inline_m.assert_unchanged(METRIC)


def test_each_item_runs_exactly_once_under_the_gate(monkeypatch):
    """No duplication when the batch goes to the pool.

    `dispatch()` submits under `copy_context().run`, and a context copied per item is the
    kind of thing that silently shares state. This counts distinct execution-unit ids rather
    than a total, so a double-dispatch of one item cannot be masked by another item's loss.
    """
    engine = _engine(monkeypatch, gate=True)

    seen: list[str] = []
    lock = threading.Lock()

    def _make(eu_id: str):
        def _callback() -> None:
            with lock:
                seen.append(eu_id)
        return _callback

    from AINDY.kernel.scheduler.common import ScheduledItem

    for i in range(WORKERS):
        eu_id = f"eu-once-{i}"
        engine.enqueue(
            ScheduledItem(
                execution_unit_id=eu_id,
                tenant_id="system",
                priority="normal",
                run_callback=_make(eu_id),
                run_id=f"run-once-{i}",
                eu_type="flow",
            )
        )

    engine.schedule(tick_waits=False)

    deadline = time.monotonic() + 30
    while len(seen) < WORKERS and time.monotonic() < deadline:
        time.sleep(0.05)

    assert sorted(seen) == sorted(f"eu-once-{i}" for i in range(WORKERS)), (
        f"expected each execution unit exactly once, got {sorted(seen)}"
    )


def test_concurrent_resumes_each_get_their_own_database_session(monkeypatch):
    """★ The `AGENT_WORKING_RULES` §5 risk, on real Postgres.

    Today a resume callback closes over `self` — hence `self.db`, a session created on
    another thread — and `FlowRunner.resume()` uses it without opening its own. Serialised on
    one `max_instances=1` tick that survives; `WORKERS` at once on a shared pool is the shape
    the rule forbids.

    This drives the shape the runtime will actually be in after the flip: N concurrent
    callbacks each opening and using their own session. It proves they do not interfere and
    that none fails to obtain a connection.

    ★ What it does NOT prove: exhaustion safety at scale. `WORKERS` is 8 against a budget of
    `DB_POOL_SIZE` 10 + `DB_MAX_OVERFLOW` 20, so this cannot saturate the pool and is not
    evidence that it never will — `SYSMAX-5` is the standing reminder that scheduler threads
    and request handling share that budget.
    """
    from sqlalchemy import text

    engine = _engine(monkeypatch, gate=True)

    results: list[object] = []
    errors: list[str] = []
    lock = threading.Lock()

    def _callback() -> None:
        from AINDY.db.database import SessionLocal

        try:
            db = SessionLocal()
            try:
                value = db.execute(text("SELECT 1")).scalar()
                with lock:
                    results.append(value)
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001 — the failure IS the finding
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    _enqueue(engine, _callback)
    engine.schedule(tick_waits=False)

    deadline = time.monotonic() + 60
    while len(results) + len(errors) < WORKERS and time.monotonic() < deadline:
        time.sleep(0.05)

    assert not errors, f"concurrent resumes failed against the pool: {errors}"
    assert results == [1] * WORKERS, (
        f"expected {WORKERS} successful queries, got {results}"
    )


# ── The refusal, pinned where a reader of this file will see it ──────────────


def test_the_gate_refuses_distributed_mode(monkeypatch):
    """Duplicated from the unit suite on purpose.

    This is the file someone opens to decide whether the soak was sufficient evidence for the
    flip. The single most important thing to know at that moment is that the soak covers
    thread mode only, and that the production overlay is not thread mode.
    """
    from AINDY.core.execution_dispatcher import async_scheduler_dispatch_enabled

    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "true")

    assert async_scheduler_dispatch_enabled() is False, (
        "the scheduler would enqueue a closure it cannot serialise; the worker acks the "
        "unresolvable job as SUCCESS and the resume is lost permanently and silently"
    )
