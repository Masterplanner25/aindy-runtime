"""IDEM-13 — strict at-most-once on the TOOL seam, proven on live PostgreSQL.

The unit suite (`tests/unit/test_tool_idempotency_strict_idem13.py`) covers the wiring with the
lock patched; SQLite has no advisory locks, so the OUTCOME can only be measured here.

What this pins, and why the two assertions differ:

* **without** the flag the contract PERMITS every concurrent caller to execute — that is
  `_resolve_existing_row` degrading past a live `pending` row, and it is what was measured on a
  real Telegram channel (5 concurrent sends of one key → 5 delivered messages, 1 row). So the
  non-strict assertion is `>= 1`, never `== N`: a soak assertion must not be stricter than the
  contract, and it must not be LOOSER than what was observed either — `degraded` must move.
* **with** the flag the contract IS `handler_ran == 1`, so that exact assertion is legitimate.

★ Liveness first: PostgreSQL AND the flag, asserted before any outcome claim. On SQLite, or with
the flag off, the mechanism is not engaged and a green result here would prove nothing.
"""
from __future__ import annotations

import threading
import uuid

import pytest

from tests.integration.soak_harness import metric_window

pytestmark = pytest.mark.integration

WORKERS = 8
TOOL = "idem13.soak_probe"


def _require_postgres(db) -> None:
    bind = db.get_bind()
    if getattr(getattr(bind, "engine", bind), "dialect").name != "postgresql":
        pytest.skip("strict tool idempotency needs PostgreSQL advisory locks; backend is not PG")


@pytest.fixture
def probe():
    """A slow EXACTLY_ONCE tool — slow enough to outlive the insert race, which is the case the
    base gate degrades on (a fast handler can finish before the losers even look)."""
    import time

    from AINDY.agents import tool_registry as tr

    runs: list[int] = []
    name = f"{TOOL}_{uuid.uuid4().hex[:8]}"

    def _fn(args, user_id, db):
        runs.append(1)
        time.sleep(0.25)
        return {"ran": len(runs)}

    tr.register_tool(
        name=name, risk="low", description="IDEM-13 soak probe", capability="c",
        required_capability="c", category="test", egress_scope="none",
        execution_guarantee="EXACTLY_ONCE",
    )(_fn)
    yield name, runs
    tr.TOOL_REGISTRY.pop(name, None)


def _drive(name: str, session_factory, run_id: str, workers: int = WORKERS):
    """Release `workers` threads on one barrier, each calling the SAME tool with the SAME args
    (so the same action_id), each on its OWN session — the shape of real contention."""
    import contextlib
    from unittest.mock import patch

    from AINDY.agents import tool_registry as tr

    barrier = threading.Barrier(workers)
    results: list = []
    lock = threading.Lock()

    def _one():
        barrier.wait()
        db = session_factory()
        try:
            with patch("AINDY.agents.capability_service.check_tool_capability",
                       return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []}), \
                 patch.object(tr, "queue_system_event", lambda **k: None), \
                 patch("AINDY.platform_layer.secret_broker.capability_scope",
                       lambda caps: contextlib.nullcontext()):
                out = tr.execute_tool(name, {"k": "v"}, "user-1", db,
                                      run_id=run_id, execution_token={"t": 1})
            with lock:
                results.append(out)
        finally:
            db.close()

    threads = [threading.Thread(target=_one, name=f"idem13-{i}") for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return results


def test_the_mechanism_is_engaged_before_any_outcome_claim(monkeypatch, db_session):
    from AINDY.agents import tool_registry as tr

    _require_postgres(db_session)
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY", "1")
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "1")
    assert tr._tool_idempotency_strict_enabled() is True
    assert tr._tool_idempotency_enabled() is True


def test_without_the_flag_concurrent_callers_degrade(monkeypatch, db_session,
                                                     testing_session_factory, probe):
    """The documented pre-IDEM-13 behaviour, and what the live channel measured. Not a failure —
    the assertion is `>= 1` because the contract permits N."""
    _require_postgres(db_session)
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY", "1")
    monkeypatch.delenv("AINDY_TOOL_IDEMPOTENCY_STRICT", raising=False)
    name, runs = probe

    with metric_window("aindy_effect_gate_outcomes_total", labels={"outcome": "degraded"}) as deg:
        results = _drive(name, testing_session_factory, str(uuid.uuid4()))

    assert all(r.get("success") for r in results), [r for r in results if not r.get("success")]
    assert len(runs) >= 1
    assert deg.delta("aindy_effect_gate_outcomes_total") >= 1, (
        "the losers must have degraded past the live pending row — if nothing degraded, the "
        "handler was too fast to contend and this soak proved nothing"
    )


def test_strict_runs_the_tool_exactly_once_under_contention(monkeypatch, db_session,
                                                            testing_session_factory, probe):
    _require_postgres(db_session)
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY", "1")
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT", "1")
    monkeypatch.setenv("AINDY_TOOL_IDEMPOTENCY_STRICT_WAIT_SECONDS", "30")
    name, runs = probe

    with metric_window("aindy_effect_gate_outcomes_total", labels={"outcome": "degraded"}) as deg, \
         metric_window("aindy_effect_gate_outcomes_total", labels={"outcome": "replayed"}) as rep:
        results = _drive(name, testing_session_factory, str(uuid.uuid4()))

    assert all(r.get("success") for r in results), [r for r in results if not r.get("success")]
    assert len(runs) == 1, (
        f"strict mode ran the tool {len(runs)} times under {WORKERS}-way contention. On a real "
        f"channel each of those is a message a person receives — that is the whole point of the "
        f"lock."
    )
    assert deg.delta("aindy_effect_gate_outcomes_total") == 0, (
        "nobody should degrade: the losers block on the lock and then replay"
    )
    assert rep.delta("aindy_effect_gate_outcomes_total") == WORKERS - 1
    assert sum(1 for r in results if r.get("idempotent_replay")) == WORKERS - 1
