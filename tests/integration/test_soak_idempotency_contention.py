"""Soak: the EXACTLY_ONCE gate under CONTENTION, on real Postgres.

This is the first concurrent test in the repository, and it is the evidence `IDEM-11`'s flag flip
has been waiting on. `test_idempotency_gate_e2e.py` already proves the gate is **correct** — it
turns `AINDY_SYSCALL_IDEMPOTENCY` on and dispatches the same syscall **twice, sequentially**.
Sequential dedup is the easy half: the first call has already committed its `effect_records` row
before the second one looks.

**Contention is the risk the flag actually carries.** N callers racing the same
`(action_type, input, scope)` all reach the gate before any of them has committed, so the
guarantee rests on a database uniqueness constraint doing its job under a real transaction —
which nothing in this repository has ever exercised.

★ The flag-off control is not optional
--------------------------------------
``test_without_the_flag_the_handler_runs_once_per_caller`` runs the identical driver with the
gate off and asserts the handler runs **N times**. It is doing two jobs:

1. **Proving the drive is actually concurrent.** If thread ramp-up serialised the callers, the
   flag-off run would also dedup by accident and the flag-on assertion would be measuring
   nothing. This is `EVENTBUS-COVERAGE-1`'s variant 6 in its most expensive form — a soak result
   that reads as evidence and is not.
2. **Being the regression baseline** the soak is compared against.

★ There is no metric for this
-----------------------------
Searched the 52 registered metrics: **nothing observes the idempotency gate firing.**
``aindy_durable_effects`` and ``aindy_effect_attribution`` are ContextVars, not metrics. So the
invariant here is asserted on handler-run count and on `effect_records` rows, and the metric
window watches the **pool** instead — because the realistic way this flag hurts in production is
not a wrong answer, it is `RT-MEMTXN-LEAK-1` again: the gate opens its own `SessionLocal`, and N
concurrent gated calls means N extra connections.

**Filing note for whoever flips the flag: an operator cannot currently tell whether the gate is
doing anything.** A counter on gate hit/replay is a prerequisite for a real production soak, and
it does not exist.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from tests.integration.soak_harness import (
    drive_concurrently,
    metric_window,
    read_metric,
)

pytestmark = pytest.mark.integration

WORKERS = 8


def _register_probe(exactly_once: bool):
    """Register a throwaway syscall whose handler counts its own invocations."""
    from AINDY.kernel import syscall_registry as R

    name = f"sys.v1.test.soak_{uuid.uuid4().hex[:8]}"
    runs: list[int] = []

    def handler(payload, ctx):
        runs.append(1)
        return {"ran": len(runs), "echo": payload}

    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(
        handler=handler,
        capability="test.soak",
        execution_guarantee="EXACTLY_ONCE" if exactly_once else "AT_LEAST_ONCE",
    )
    return name, runs


class _OkRm:
    """Quota manager that always admits — quota is not what this test is measuring."""

    def check_quota(self, _x):
        return True, None

    def record_usage(self, _x, _u):
        return None


def _drive(name: str, eu_id: str, payload: dict) -> object:
    """Dispatch the same syscall from N threads, each with its own dispatcher instance.

    ★ A fresh ``SyscallDispatcher`` per worker, never the singleton: sharing one would serialise
    on its internal state and the test would measure a lock instead of the gate. The gate opens
    its own session internally, which is what makes this safe to drive from threads at all.
    """
    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.kernel import syscall_registry as R

    caller_id = str(uuid.uuid4())

    def _one(_i: int):
        dispatcher = D.SyscallDispatcher()
        dispatcher._emit_syscall_event = lambda *a, **kw: None
        ctx = R.SyscallContext(
            execution_unit_id=eu_id,
            user_id=caller_id,
            capabilities=["test.soak"],
            trace_id="soak",
        )
        with patch.object(D, "_get_rm", lambda: _OkRm()):
            return dispatcher.dispatch(name, payload, ctx)

    return drive_concurrently(_one, workers=WORKERS)


# ── The control: proves the drive is genuinely concurrent ────────────────────


def test_without_the_flag_the_handler_runs_once_per_caller(monkeypatch, testing_session_factory):
    """★ LIVENESS + BASELINE. If this dedups, the drive is not concurrent and the soak below
    is measuring nothing."""
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "false")
    name, runs = _register_probe(exactly_once=True)

    outcome = _drive(name, str(uuid.uuid4()), {"x": 1})
    outcome.assert_all_succeeded()

    assert len(runs) == WORKERS, (
        f"gate off: expected {WORKERS} handler runs, got {len(runs)}. Fewer means the drive "
        f"serialised — the concurrency is not real and every assertion in this file is vacuous."
    )


# ── The soak ─────────────────────────────────────────────────────────────────


def test_the_gate_degrades_to_at_least_once_under_contention(
    monkeypatch, testing_session_factory, caplog
):
    """★ THE FINDING FROM THIS HARNESS'S FIRST RUN — and it is a documentation gap, not a bug.

    The first version of this test asserted ``len(runs) == 1``. It failed in CI: **the handler
    ran 2 times across 8 concurrent identical calls.** That assertion was wrong, not the code.

    ``effect_ledger.resolve_effect_record`` deliberately degrades when it loses the insert race
    against a *live* pending row::

        # A live concurrent call holds the slot; degrade to AT_LEAST_ONCE for
        # this invocation (strict at-most-once under concurrency needs advisory
        # locking — see IDEMPOTENCY_CONTRACT.md).
        return False, None

    and `IDEMPOTENCY_CONTRACT.md` documents it exactly, in its state table: *"pending (fresh,
    ≤ 15 min old) | concurrent-insert race — live call in flight | gate degrades to
    AT_LEAST_ONCE for this call; warning logged."*

    **So why did anyone write the wrong assertion?** Because `CLAUDE.md`'s `IDEM-11` line — the
    thing an implementer reads before flipping the flag — said *"at-most-once is built"* with no
    concurrency caveat at all. The contract and the index disagreed, and the index is what gets
    read. That line is corrected; this test is the executable version of the correction.

    **What this means for the flip: `EXACTLY_ONCE` is not exactly-once under contention.** It is
    "exactly once unless another caller holds the slot, in which case at-least-once with a
    warning." Anyone flipping `AINDY_SYSCALL_IDEMPOTENCY` for a non-idempotent effect needs to
    know that, and until this test existed nothing said it in a form that could fail.
    """
    import logging

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    name, runs = _register_probe(exactly_once=True)

    with caplog.at_level(logging.WARNING):
        outcome = _drive(name, str(uuid.uuid4()), {"x": 1})

    assert outcome.ok, (
        f"{len(outcome.failures)} of {WORKERS} concurrent callers raised — the gate must "
        f"serialise, replay or degrade, never fail. First: "
        f"{outcome.failures[0] if outcome.failures else None}"
    )
    assert set(r.get("status") for r in outcome.results) == {"success"}

    # The guarantee that DOES hold: the gate dedups the large majority.
    assert 1 <= len(runs) < WORKERS, (
        f"handler ran {len(runs)} times across {WORKERS} concurrent calls. 1 would mean strict "
        f"at-most-once (the gate does not claim it); {WORKERS} would mean the gate does nothing "
        f"under contention, which would make the flag worthless exactly where it matters."
    )

    # ★ The degradation must be COUNTABLE. It is the only signal an operator gets that
    # EXACTLY_ONCE did not hold for a given call.
    if len(runs) > 1:
        assert any(
            "degrading to AT_LEAST_ONCE" in r.message % r.args if r.args else
            "degrading to AT_LEAST_ONCE" in r.message
            for r in caplog.records
        ), (
            "the handler ran more than once but no degradation warning was logged — the "
            "downgrade would be silent, and an operator would have no way to know that "
            "EXACTLY_ONCE did not hold"
        )


def test_the_ledger_holds_exactly_one_row_for_the_raced_key(
    monkeypatch, testing_session_factory
):
    """The DB is the arbiter, so assert on it rather than only on in-process counters."""
    from AINDY.core.execution_gate import compute_action_id
    from AINDY.db.models.effect_record import EffectRecord

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    name, _runs = _register_probe(exactly_once=True)
    eu_id = str(uuid.uuid4())
    payload = {"x": 2}

    _drive(name, eu_id, payload).assert_all_succeeded()

    action_id = compute_action_id(action_type=name, input_payload=payload, scope=eu_id)
    session = testing_session_factory()
    try:
        rows = session.query(EffectRecord).filter(EffectRecord.action_id == action_id).all()
    finally:
        session.close()

    assert len(rows) == 1, (
        f"{len(rows)} effect_records rows for one raced action_id — the uniqueness guarantee is "
        f"the whole mechanism, and duplicates here mean the gate is advisory under contention"
    )
    assert rows[0].status == "success"


def test_the_gate_does_not_exhaust_the_connection_pool(monkeypatch, testing_session_factory):
    """★ The realistic production failure, and the reason to read a metric rather than infer.

    The gate opens its OWN ``SessionLocal``, so N concurrent gated calls means N extra
    connections on top of the callers'. `RT-MEMTXN-LEAK-1` was exactly this class — 60 held
    connections and a 43-second login — and it was found in production, not in a test.
    """
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    name, _runs = _register_probe(exactly_once=True)

    with metric_window("aindy_db_pool_exhaustion_events_total") as m:
        _drive(name, str(uuid.uuid4()), {"x": 3}).assert_all_succeeded()

    m.assert_unchanged("aindy_db_pool_exhaustion_events_total")


def test_the_harness_refuses_an_unregistered_metric():
    """★ Guards the harness itself. `get_sample_value` returns None for an unknown name, and
    None-as-zero makes 'did not move' and 'does not exist' indistinguishable — a soak assertion
    against a renamed metric would pass forever, most convincingly on the run that broke."""
    with pytest.raises(AssertionError, match="not registered"):
        read_metric("aindy_this_metric_does_not_exist")
