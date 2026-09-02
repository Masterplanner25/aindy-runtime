"""FR-15 (a) — the scheduler's async-dispatch gate, split from the route-facing flag.

`FR-15` diagnosed one defect: `_scheduler_heartbeat_tick` is the only queue drainer and
`schedule()` runs each item **synchronously**, so one slow flow starves every other queued
item and the wait/cleanup jobs with it. The filed fix was *"flip
`AINDY_ASYNC_HEAVY_EXECUTION`, one line"*.

That flag means two unrelated things, and flipping it ships both:

1. *May heavy work run off the scheduler heartbeat?* — the actual defect.
2. *Do two live HTTP routes answer `202 QUEUED` instead of a result?* —
   `agents/runtime_api.py:106` and `routes/memory_router.py:729` branch on the same
   variable.

Only (1) is FR-15. Fixing a P0 scheduler defect should not renegotiate an API contract as a
side effect, so the scheduler now asks `async_scheduler_dispatch_enabled()` and the routes
keep asking the old question. Precedent for splitting rather than flipping:
`EVENTBUS-PUBLISH-LATCH-1` (one field carrying an operator switch and a runtime latch) and
FR-17's `AINDY_ASYNC_JOB_LOOP_CLOSURE` (*"one flag meant two things"*).

★★ THE FILE EXISTS BECAUSE THE DEMONSTRATION DID NOT
-----------------------------------------------------
`TECH_DEBT.md` records the eight type×priority combinations as *"demonstrated"*. They were —
ad hoc, in a session, and never pinned: before this file, `grep -rl _decide_mode tests/`
returned **nothing**. The mode decision that the whole entry turns on had no test at all, so
`test_every_heavy_type_is_inline_by_default` below is the demonstration made repeatable.

★★ THE TWO GUARDS THAT ARE NOT STYLE
-------------------------------------
**Distributed mode must refuse, and it is checked before the explicit opt-in.**
`dispatch()`'s ASYNC branch is not "submit to a thread pool" — under
`EXECUTION_MODE=distributed` it calls `_enqueue_distributed()`, which builds a payload from
`context` and **drops `handler_fn`**. A closure cannot cross a process boundary. That is fine
for `async_job_service` (its context carries `log_id`, so the worker re-reads the JobLog) and
fatal for the scheduler, whose work *is* the closure `item.run_callback`. The worker would
find no JobLog for the eu_id, log a warning, **ack** the message and return **success** — the
resume never runs and the FlowRun stays `waiting` forever, with no DLQ entry and no retry.
`docker-compose.prod.yml` sets `EXECUTION_MODE: distributed`, so this is the production shape.

**An explicit opt-in must beat test mode.** `async_heavy_execution_enabled()` returns False
under `TESTING`/`TEST_MODE` *before* reading its flag, and `pytest.integration.ini` sets both
— so the async path is unreachable from every test in this repository and always has been. A
soak written the obvious way (monkeypatch the flag, drive load) passes while running the
INLINE branch throughout: catalogue variant 3 arriving as variant 6, not merely unexercised
but actively neutralised two lines above the switch it appears to test. The new gate honours
an explicit setting so the evidence for the flip is obtainable at all.
"""
from __future__ import annotations

import threading

import pytest

pytestmark = pytest.mark.runtime_only


# ── helpers ──────────────────────────────────────────────────────────────────


class _EU:
    """Minimal ExecutionUnit stand-in — `_decide_mode` reads only these three."""

    def __init__(self, eu_type: str = "flow", priority: str = "normal", extra=None):
        self.type = eu_type
        self.priority = priority
        self.extra = extra or {}
        self.id = "eu-test"


def _clear_gate_env(monkeypatch):
    """Neutral environment: no opt-in, not distributed, not a test run."""
    monkeypatch.delenv("AINDY_ASYNC_SCHEDULER_DISPATCH", raising=False)
    monkeypatch.setenv("EXECUTION_MODE", "thread")
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setenv("TEST_MODE", "false")


# ── 1. The mode decision itself — the never-pinned demonstration ─────────────


HEAVY_TYPES = ["flow", "agent", "nodus", "job"]


@pytest.mark.parametrize("eu_type", HEAVY_TYPES)
@pytest.mark.parametrize("priority", ["high", "normal"])
def test_every_heavy_type_is_inline_by_default(monkeypatch, eu_type, priority):
    """All 8 combinations return INLINE unset — the state FR-15 calls the defect.

    Rule 2 (`async_heavy_execution_enabled()`) short-circuits Rules 4 and 5, so
    `priority="high"` blocks the thread its own docstring promises it never will.
    """
    from AINDY.core.execution_dispatcher import ExecutionMode, _decide_mode

    monkeypatch.delenv("AINDY_ASYNC_HEAVY_EXECUTION", raising=False)
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setenv("TEST_MODE", "false")

    assert _decide_mode(_EU(eu_type, priority)) is ExecutionMode.INLINE


@pytest.mark.parametrize("eu_type", HEAVY_TYPES)
@pytest.mark.parametrize("priority", ["high", "normal"])
def test_every_heavy_type_is_async_with_the_old_flag_on(monkeypatch, eu_type, priority):
    """The liveness control for the test above.

    Without this, `test_every_heavy_type_is_inline_by_default` would pass just as happily
    against a `_decide_mode` that had been broken to return INLINE unconditionally.
    """
    from AINDY.core.execution_dispatcher import ExecutionMode, _decide_mode

    monkeypatch.setenv("AINDY_ASYNC_HEAVY_EXECUTION", "true")
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setenv("TEST_MODE", "false")

    assert _decide_mode(_EU(eu_type, priority)) is ExecutionMode.ASYNC


# ── 2. The split is real in both directions ──────────────────────────────────


def test_scheduler_gate_on_does_not_turn_on_the_route_flag(monkeypatch):
    """★ THE POINT OF THE SPLIT.

    The two 202-answering routes read `async_heavy_execution_enabled()`. If turning the
    scheduler gate on also turned that True, the split would be decorative and the flip
    would still change two response shapes.
    """
    from AINDY.core.execution_dispatcher import (
        async_heavy_execution_enabled,
        async_scheduler_dispatch_enabled,
    )

    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "true")
    monkeypatch.delenv("AINDY_ASYNC_HEAVY_EXECUTION", raising=False)

    assert async_scheduler_dispatch_enabled() is True
    assert async_heavy_execution_enabled() is False


def test_route_flag_cannot_influence_the_scheduler_gate(monkeypatch):
    """The other direction, so the two cannot be quietly re-coupled later.

    ★ Asserts INDEPENDENCE, not a fixed value. An earlier version asserted the gate was
    `False` here, which passed only because the default was `False` — it was reading the
    default and calling it a decoupling proof, so flipping the default in a later commit made
    a correct runtime look broken. Two properties survive any default:

    1. The route flag does not move the gate — both its settings give the same answer.
    2. An explicit scheduler `off` wins over a route flag that is `on`.
    """
    from AINDY.core.execution_dispatcher import (
        async_heavy_execution_enabled,
        async_scheduler_dispatch_enabled,
    )

    _clear_gate_env(monkeypatch)
    monkeypatch.delenv("AINDY_ASYNC_SCHEDULER_DISPATCH", raising=False)

    monkeypatch.setenv("AINDY_ASYNC_HEAVY_EXECUTION", "true")
    assert async_heavy_execution_enabled() is True
    with_route_flag = async_scheduler_dispatch_enabled()

    monkeypatch.setenv("AINDY_ASYNC_HEAVY_EXECUTION", "false")
    assert async_heavy_execution_enabled() is False
    without_route_flag = async_scheduler_dispatch_enabled()

    assert with_route_flag == without_route_flag, (
        "the route flag moved the scheduler gate — the two are re-coupled, which is exactly "
        "what FR-15 (a) split apart"
    )

    monkeypatch.setenv("AINDY_ASYNC_HEAVY_EXECUTION", "true")
    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "false")
    assert async_scheduler_dispatch_enabled() is False, (
        "an explicit scheduler 'off' must win over the route flag being on"
    )


def test_the_two_route_call_sites_still_read_the_old_flag():
    """Supplement, not coverage — cheap detection of an accidental rewire.

    `ROUTE-GUARD-1`: reading a handler's source proves the guard was written, never that a
    caller receives its answer. The behavioural halves are the two tests above; this only
    catches someone pointing the routes at the new gate, which would silently reintroduce
    the coupling the split exists to remove.
    """
    from pathlib import Path

    for rel in ("AINDY/agents/runtime_api.py", "AINDY/routes/memory_router.py"):
        src = Path(rel).read_text(encoding="utf-8")
        assert "async_heavy_execution_enabled" in src, f"{rel} no longer reads the route flag"
        assert "async_scheduler_dispatch_enabled" not in src, (
            f"{rel} now reads the SCHEDULER gate. That re-couples the 202-response decision "
            f"to the scheduler defect fix, which is exactly what FR-15 (a) split apart."
        )


# ── 3. Precedence, and the refusal that prevents silent resume loss ──────────


def test_distributed_mode_refuses_even_an_explicit_opt_in(monkeypatch):
    """★★ THE GUARD THAT MATTERS MOST.

    `_enqueue_distributed()` drops `handler_fn`. With the scheduler's context there is no
    JobLog for the worker to re-read, so it warns, **acks**, and returns success while the
    resume never runs — a permanent silent loss, worse than the starvation being fixed.

    Checked BEFORE the explicit opt-in on purpose: an operator setting the variable on a
    prod overlay must not be able to arm this.
    """
    from AINDY.core.execution_dispatcher import async_scheduler_dispatch_enabled

    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "distributed")
    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "true")

    assert async_scheduler_dispatch_enabled() is False


def test_explicit_opt_in_beats_test_mode(monkeypatch):
    """Without this the soak cannot exist — see the module docstring."""
    from AINDY.core.execution_dispatcher import async_scheduler_dispatch_enabled

    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "true")

    assert async_scheduler_dispatch_enabled() is True


def test_unset_under_test_mode_is_off(monkeypatch):
    """Ordinary test runs stay inline — nothing sets the variable by default."""
    from AINDY.core.execution_dispatcher import async_scheduler_dispatch_enabled

    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.delenv("AINDY_ASYNC_SCHEDULER_DISPATCH", raising=False)

    assert async_scheduler_dispatch_enabled() is False


def test_unset_outside_test_mode_follows_the_default_constant(monkeypatch):
    """Pins the flip to ONE greppable line.

    When FR-15 (a) is finally flipped, `_SCHEDULER_ASYNC_DISPATCH_DEFAULT` is the only
    thing that changes, and this test is what proves the constant is actually consulted
    rather than shadowed by a second default somewhere in the precedence chain.
    """
    import AINDY.core.execution_dispatcher as d

    _clear_gate_env(monkeypatch)

    monkeypatch.setattr(d, "_SCHEDULER_ASYNC_DISPATCH_DEFAULT", True)
    assert d.async_scheduler_dispatch_enabled() is True

    monkeypatch.setattr(d, "_SCHEDULER_ASYNC_DISPATCH_DEFAULT", False)
    assert d.async_scheduler_dispatch_enabled() is False


def test_the_shipped_default_dispatches_asynchronously(monkeypatch):
    """★ The FR-15 (a) flip itself, pinned as behaviour.

    Every other test here is value-independent on purpose — they prove the constant is
    *consulted*, so they pass whichever way it is set. That is right for the mechanism and
    useless for the decision: reverting the flip leaves them all green.

    This one asserts the shipped answer, so an accidental revert fails rather than quietly
    restoring the serialised dispatch. Editing it should feel deliberate; a default change is
    exactly the kind of thing that should not slip through as a side effect.
    """
    from AINDY.core.execution_dispatcher import async_scheduler_dispatch_enabled

    _clear_gate_env(monkeypatch)
    monkeypatch.delenv("AINDY_ASYNC_SCHEDULER_DISPATCH", raising=False)

    assert async_scheduler_dispatch_enabled() is True, (
        "a thread-mode deployment with nothing configured is dispatching INLINE again — the "
        "scheduler drains its queue synchronously and one slow item starves the rest"
    )


# ── 4. The scheduler actually asks — behaviour, not wiring ───────────────────


def _drain_one(monkeypatch, *, gate: bool):
    """Enqueue one item, drain it with `schedule()`, report the thread it ran on."""
    from AINDY.kernel.scheduler.common import ScheduledItem
    from AINDY.kernel.scheduler_engine import SchedulerEngine

    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("AINDY_ASYNC_SCHEDULER_DISPATCH", "true" if gate else "false")

    engine = SchedulerEngine()
    ran_on: list[int] = []
    done = threading.Event()

    def _callback() -> None:
        ran_on.append(threading.get_ident())
        done.set()

    engine.enqueue(
        ScheduledItem(
            execution_unit_id="eu-1",
            tenant_id="system",
            priority="normal",
            run_callback=_callback,
            run_id="run-1",
            eu_type="flow",
        )
    )
    engine.schedule(tick_waits=False)
    done.wait(timeout=10)
    return ran_on


def test_with_the_gate_off_the_callback_runs_on_the_drainer_thread(monkeypatch):
    """The baseline, and the regression guard: gate off must be byte-identical to today.

    The whole FR-15 defect is that this thread is the 1-second heartbeat's, so while the
    callback runs nothing else can be dispatched.
    """
    ran_on = _drain_one(monkeypatch, gate=False)

    assert ran_on == [threading.get_ident()], (
        "gate off: the callback must still run inline on the caller's thread"
    )


def test_with_the_gate_on_the_callback_leaves_the_drainer_thread(monkeypatch):
    """★ THE DEFECT FIX, stated as behaviour rather than as a flag read.

    Asserting the thread *differs* is the property FR-15 actually wants — the drainer is
    free again the moment it hands the item off. Asserting `stub.extra["async_hint"]` would
    only prove a dict was populated.
    """
    ran_on = _drain_one(monkeypatch, gate=True)

    assert len(ran_on) == 1, f"the callback did not run: {ran_on}"
    assert ran_on[0] != threading.get_ident(), (
        "gate on: the callback still ran on the drainer's thread — the scheduler is not "
        "reaching the ASYNC branch, so the starvation FR-15 describes is unchanged"
    )
