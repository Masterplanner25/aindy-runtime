"""FR-27 — strict at-most-once under contention (the advisory-lock half of IDEM-11).

The base gate degrades every concurrent duplicate: a *pending* row protects nothing until it is
*success*, so N barrier-released callers of one `EXACTLY_ONCE` action run the handler up to N
times (measured N−1 of N, pinned at N with a slow handler — see `FR27_ADVISORY_LOCK_DESIGN.md`
§2). Strict mode (`AINDY_SYSCALL_IDEMPOTENCY_STRICT`, default off) makes a loser BLOCK on an
advisory lock keyed on the action_id until the winner finishes, then replay.

★ These assert the MECHANISM is the one running before the outcome (the standing rule about
test-mode short-circuits): the backend must be PostgreSQL (advisory locks don't exist on SQLite)
and strict mode must be on, or the fix is not even engaged and a green assertion proves nothing.

★ Unlike the non-strict soak (`test_soak_idempotency_contention.py`), whose contract permits
degrade-to-N and so asserts only `>= 1`, strict mode's contract IS handler_ran == 1 on PG — so
that exact assertion is legitimate here. The two soaks test different contracts and stay separate.
"""
from __future__ import annotations

import time
import uuid
from unittest.mock import patch

import pytest

from tests.integration.soak_harness import metric_window
from tests.integration import test_soak_idempotency_contention as base

pytestmark = pytest.mark.integration

WORKERS = 8


def _require_postgres(testing_session_factory):
    bind = testing_session_factory.kw.get("bind")
    name = getattr(getattr(bind, "engine", bind), "dialect", None)
    if name is None or name.name != "postgresql":
        pytest.skip("strict idempotency needs PostgreSQL advisory locks; backend is not PG")


def _slow_probe(sleep_s: float = 0.2, fail_first: bool = False):
    """An EXACTLY_ONCE probe whose handler is slow enough to outlive the insert race — the case
    the base gate degrades on. Returns (name, runs)."""
    from AINDY.kernel import syscall_registry as R

    name = f"sys.v1.test.strict_{uuid.uuid4().hex[:8]}"
    runs: list[int] = []

    def handler(payload, ctx):
        runs.append(1)
        time.sleep(sleep_s)
        if fail_first and len(runs) == 1:
            raise RuntimeError("first attempt fails")
        return {"ran": len(runs)}

    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(
        handler=handler, capability="test.soak", execution_guarantee="EXACTLY_ONCE"
    )
    return name, runs


@pytest.fixture
def strict_on(monkeypatch):
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "1")
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT", "1")
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT_WAIT_SECONDS", "30")


def test_the_mechanism_is_actually_engaged(testing_session_factory, strict_on):
    """Liveness: assert strict mode is on AND the backend is PG before any outcome claim."""
    from AINDY.kernel import syscall_dispatcher as D

    _require_postgres(testing_session_factory)
    assert D._syscall_idempotency_strict_enabled() is True


def test_strict_runs_the_handler_exactly_once_under_contention(
    monkeypatch, testing_session_factory, strict_on
):
    _require_postgres(testing_session_factory)
    monkeypatch.setattr(base, "WORKERS", WORKERS)
    name, runs = _slow_probe()

    with metric_window("aindy_effect_gate_outcomes_total", labels={"outcome": "degraded"}) as deg, \
         metric_window("aindy_effect_gate_outcomes_total", labels={"outcome": "replayed"}) as rep:
        out = base._drive(name, str(uuid.uuid4()), {"k": "v"})

    out.assert_all_succeeded()
    assert {r.get("status") for r in out.results} == {"success"}
    assert len(runs) == 1, (
        f"strict mode ran the handler {len(runs)} times under {WORKERS}-way contention; the "
        f"advisory lock must serialise the losers into a replay. This is the guarantee strict "
        f"mode exists to make."
    )
    assert deg.delta("aindy_effect_gate_outcomes_total") == 0, (
        "no caller should have degraded — they should have blocked on the lock and replayed"
    )
    assert rep.delta("aindy_effect_gate_outcomes_total") == WORKERS - 1


@pytest.mark.parametrize("n", [2, 4, 16])
def test_strict_holds_across_widths(monkeypatch, testing_session_factory, strict_on, n):
    _require_postgres(testing_session_factory)
    monkeypatch.setattr(base, "WORKERS", n)
    name, runs = _slow_probe()
    out = base._drive(name, str(uuid.uuid4()), {"k": "v"})
    out.assert_all_succeeded()
    assert len(runs) == 1, f"{n} callers → handler ran {len(runs)} times, expected 1"


def test_a_winner_failure_is_one_reclaim_not_n(monkeypatch, testing_session_factory, strict_on):
    """The winner's handler raising leaves a `failed` row and releases the lock; the next caller
    reclaims and retries ONCE, the rest replay that success. Not N retries."""
    _require_postgres(testing_session_factory)
    monkeypatch.setattr(base, "WORKERS", WORKERS)
    name, runs = _slow_probe(fail_first=True)

    with metric_window("aindy_effect_gate_outcomes_total", labels={"outcome": "reclaimed"}) as rec:
        out = base._drive(name, str(uuid.uuid4()), {"k": "v"})

    statuses = sorted(r.get("status") for r in out.results)
    assert statuses.count("error") == 1, f"expected exactly one error (the failed winner), got {statuses}"
    assert statuses.count("success") == WORKERS - 1
    assert len(runs) == 2, f"expected one retry after the winner failed, handler ran {len(runs)} times"
    assert rec.delta("aindy_effect_gate_outcomes_total") >= 1


def test_without_strict_the_base_gate_still_degrades(monkeypatch, testing_session_factory):
    """Control: with strict OFF, the slow-handler contention still degrades (the defect strict
    mode fixes). Proves strict mode is what changes the outcome, not the harness."""
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "1")
    monkeypatch.delenv("AINDY_SYSCALL_IDEMPOTENCY_STRICT", raising=False)
    _require_postgres(testing_session_factory)
    monkeypatch.setattr(base, "WORKERS", WORKERS)
    name, runs = _slow_probe()
    out = base._drive(name, str(uuid.uuid4()), {"k": "v"})
    out.assert_all_succeeded()
    assert len(runs) > 1, (
        "with strict OFF a slow handler under contention must still degrade — if this ran once, "
        "the base gate changed and strict mode is not what is being measured"
    )
