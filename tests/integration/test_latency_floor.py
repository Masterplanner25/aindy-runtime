"""PERF-BASELINE-1 — an order-of-magnitude LATENCY floor on the path the flags flip.

The counted half (`test_work_budget.py`) says how much WORK an effect and an agent step do. This
says how long they take, at order-of-magnitude resolution only: each bound sits about 10x above what
was measured, so it trips on a regression of that size (a sleep, a lost index, a network round trip
per row), never on a slow runner.

★ On demand, not on every PR (the entry's rule: never a tight wall-clock bound on shared CI). Skipped
unless ``AINDY_LATENCY_FLOOR=1``; the `Latency Floor` workflow runs it by hand
(`workflow_dispatch`). Run locally with the integration env plus ``AINDY_LATENCY_FLOOR=1``.

★ Every bound has a CONTROL that must trip it: the same measurement with a delay injected where the
regression would land. A floor that the control cannot break certifies nothing (variant 9).

Measured on the path being flipped: `EXACTLY_ONCE` with `AINDY_SYSCALL_IDEMPOTENCY` on and the real
effect ledger; an agent run on both backends.
"""
from __future__ import annotations

import os
import statistics
import time
import uuid
from unittest.mock import patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("AINDY_LATENCY_FLOOR", "") != "1",
                       reason="on demand: set AINDY_LATENCY_FLOOR=1 (PERF-BASELINE-1)"),
]

#: Median ms for one gated EXACTLY_ONCE effect (4 ledger queries). Measured locally: see the entry.
EFFECT_MEDIAN_FLOOR_MS = 250.0
#: Median ms for one agent step doing no work of its own (`runtime.selftest`), per backend.
STEP_MEDIAN_FLOOR_MS = {"agent_flow": 1500.0, "nodus_vm": 3000.0}

_EFFECTS = 30
_STEPS = 5


@pytest.fixture
def engine(test_engine):
    if test_engine.dialect.name != "postgresql":
        pytest.skip("the latency floor is measured on Postgres")
    return test_engine


@pytest.fixture
def _restore_request_context():
    from AINDY.main import _request_id_ctx

    before = _request_id_ctx.get()
    try:
        yield
    finally:
        _request_id_ctx.set(before)


# ── per effect ───────────────────────────────────────────────────────────────


def _effect_median_ms(monkeypatch) -> float:
    from tests.integration.test_work_budget import _OkRm, _register_probe

    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.kernel import syscall_registry as R

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    name, runs = _register_probe()
    eu = str(uuid.uuid4())
    dispatcher = D.SyscallDispatcher()
    ctx = R.SyscallContext(execution_unit_id=eu, user_id=str(uuid.uuid4()), capabilities=["test.budget"],
                           trace_id="latency")
    samples = []
    try:
        with patch.object(D, "_get_rm", lambda: _OkRm()):
            for i in range(_EFFECTS + 3):
                t0 = time.perf_counter()
                envelope = dispatcher.dispatch(name, {"n": i}, ctx)
                elapsed = (time.perf_counter() - t0) * 1000
                assert envelope.get("status") == "success", envelope
                if i >= 3:  # the first few pay connection and import warm-up
                    samples.append(elapsed)
    finally:
        R.SYSCALL_REGISTRY.pop(name, None)
    assert len(runs) == _EFFECTS + 3, "control: every distinct payload is a distinct effect"
    return statistics.median(samples)


def test_a_gated_effect_is_within_its_floor(engine, monkeypatch):
    median = _effect_median_ms(monkeypatch)
    print(f"\n[latency-floor] EXACTLY_ONCE effect, gate on: median {median:.1f} ms over {_EFFECTS} "
          f"(floor {EFFECT_MEDIAN_FLOOR_MS} ms)")
    assert median < EFFECT_MEDIAN_FLOOR_MS, (median, EFFECT_MEDIAN_FLOOR_MS)


def test_control_a_slow_ledger_trips_the_effect_floor(engine, monkeypatch):
    """The regression the floor exists for: a delay in the effect ledger, per effect."""
    from AINDY.kernel import effect_ledger

    real = effect_ledger.resolve_effect_record

    def _slow(*a, **k):
        time.sleep(EFFECT_MEDIAN_FLOOR_MS / 1000.0)
        return real(*a, **k)

    from AINDY.kernel import syscall_dispatcher as D

    monkeypatch.setattr(D, "_resolve_effect_record", _slow)  # bound by name at import
    median = _effect_median_ms(monkeypatch)
    assert median >= EFFECT_MEDIAN_FLOOR_MS, (
        f"a {EFFECT_MEDIAN_FLOOR_MS} ms delay per effect read as {median:.1f} ms: the measurement "
        f"is not on the ledger path, so the floor above certifies nothing"
    )


# ── per agent step ───────────────────────────────────────────────────────────


def _step_median_ms(backend: str, monkeypatch) -> float:
    from tests.integration.test_agent_vm_parity import _committed_user, _create_executing_run, _execute, _read_run
    from tests.integration.test_work_budget import _selftest_plan

    def _one(n):
        user = _committed_user()
        plan = _selftest_plan(n)
        run_id, token = _create_executing_run(user, plan)
        t0 = time.perf_counter()
        _execute(backend, run_id=run_id, plan=plan, token=token, user_id=user, monkeypatch=monkeypatch)
        elapsed = (time.perf_counter() - t0) * 1000
        run = _read_run(run_id)
        assert run["status"] == "completed" and run["steps_completed"] == n, (backend, run)
        return elapsed

    _one(1)  # warm-up: worker pool, plugin load, first-run surcharge
    per_step = []
    for _ in range(3):
        one, many = _one(1), _one(_STEPS)
        per_step.append((many - one) / (_STEPS - 1))
    return statistics.median(per_step)


@pytest.mark.parametrize("backend", ["agent_flow", "nodus_vm"])
def test_an_agent_step_is_within_its_floor(engine, monkeypatch, _restore_request_context, backend):
    median = _step_median_ms(backend, monkeypatch)
    floor = STEP_MEDIAN_FLOOR_MS[backend]
    print(f"\n[latency-floor] {backend}: median {median:.1f} ms per agent step (floor {floor} ms)")
    assert median < floor, (backend, median, floor)


def test_control_a_slow_step_trips_the_step_floor(engine, monkeypatch, _restore_request_context):
    """A delay per tool call, on the backend that runs tools in-process."""
    from AINDY.runtime import nodus_adapter

    real = nodus_adapter.execute_tool  # bound by name at import; the agent_flow step calls this
    floor = STEP_MEDIAN_FLOOR_MS["agent_flow"]

    def _slow(*a, **k):
        time.sleep(floor / 1000.0)
        return real(*a, **k)

    monkeypatch.setattr(nodus_adapter, "execute_tool", _slow)
    median = _step_median_ms("agent_flow", monkeypatch)
    assert median >= floor, (
        f"a {floor} ms delay per step read as {median:.1f} ms: the measurement is not on the step path"
    )
