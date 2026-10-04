"""Soak: `AT_MOST_ONCE` under contention, on real Postgres and the real advisory lock (DEC-089).

The contrast this exists for is IDEM-11: eight concurrent identical `EXACTLY_ONCE` calls ran the
handler twice, because the gate degrades to at-least-once when a caller loses the insert race to a
live `pending` row (`test_soak_idempotency_contention.py`). `AT_MOST_ONCE` must not. It always takes
the strict lock, with the STRICT flags OFF here on purpose, so the loser waits for the winner and
replays, or is refused (`transient`, nothing dispatched). It never runs alongside it.

★ The assertions are the CONTRACT and no stricter (that soak's own lesson, twice): the handler runs
AT MOST once (and at least once, or the drive dispatched nothing); every other caller replayed or
was refused; and none of the three degrade paths moved. Which callers replayed and which were
refused is timing, so it is not asserted.
"""
from __future__ import annotations

import contextlib
import time
import uuid
from unittest.mock import patch

import pytest

from tests.integration.soak_harness import drive_concurrently, metric_window

pytestmark = pytest.mark.integration

WORKERS = 8
DEGRADE_LABELS = ("degraded", "degraded_gate_error", "degraded_lock_timeout")


class _OkRm:
    def check_quota(self, _x):
        return True, None

    def record_usage(self, _x, _u):
        return None


def _register(guarantee: str):
    from AINDY.kernel import syscall_registry as R

    name = f"sys.v1.test.amo_soak_{uuid.uuid4().hex[:8]}"
    runs: list[int] = []

    def handler(payload, ctx):
        runs.append(1)
        time.sleep(0.3)  # hold the slot long enough that the other callers contend for it
        return {"ran": len(runs)}

    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(handler=handler, capability="test.amo", execution_guarantee=guarantee)
    return name, runs


def _drive(name: str, eu_id: str):
    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.kernel import syscall_registry as R

    def _one(_i: int):
        d = D.SyscallDispatcher()  # one per worker: a shared one would measure its own lock
        d._emit_syscall_event = lambda *a, **k: None
        ctx = R.SyscallContext(execution_unit_id=eu_id, user_id=str(uuid.uuid4()),
                               capabilities=["test.amo"], trace_id="amo-soak")
        with patch.object(D, "_get_rm", lambda: _OkRm()):
            return d.dispatch(name, {"x": 1}, ctx)

    return drive_concurrently(_one, workers=WORKERS)


def test_control_the_drive_is_concurrent(monkeypatch, testing_session_factory):
    """Liveness: with no gate, every caller runs. Fewer means the drive serialised and the soak
    below would prove nothing."""
    name, runs = _register("AT_LEAST_ONCE")
    _drive(name, str(uuid.uuid4())).assert_all_succeeded()
    assert len(runs) == WORKERS, f"expected {WORKERS} runs without a gate, got {len(runs)}"


def test_at_most_once_never_runs_twice_and_never_degrades(monkeypatch, testing_session_factory):
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    monkeypatch.delenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT", raising=False)  # AT_MOST_ONCE forces it
    name, runs = _register("AT_MOST_ONCE")

    with contextlib.ExitStack() as stack:
        windows = [stack.enter_context(metric_window("aindy_effect_gate_outcomes_total", labels={"outcome": o}))
                   for o in DEGRADE_LABELS]
        outcome = _drive(name, str(uuid.uuid4()))

    assert outcome.ok, f"a caller raised: {outcome.failures[:1]}"
    assert len(runs) <= 1, f"AT_MOST_ONCE ran the handler {len(runs)} times under {WORKERS}-way contention"
    assert len(runs) == 1, "liveness: no caller ran the handler; the drive dispatched nothing"
    for env in outcome.results:
        refused = env.get("status") == "error" and env.get("failure_class") == "transient"
        assert env.get("status") == "success" or refused, f"neither replayed nor refused: {env}"
    for label, window in zip(DEGRADE_LABELS, windows):
        moved = window.delta("aindy_effect_gate_outcomes_total")
        assert moved == 0, f"AT_MOST_ONCE degraded via `{label}` {moved} time(s); it must refuse instead"
