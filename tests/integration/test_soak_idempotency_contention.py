"""Soak: the EXACTLY_ONCE gate under CONTENTION, on real Postgres.

This is the first concurrent test in the repository, and it is the evidence `IDEM-11`'s flag flip
has been waiting on. `test_idempotency_gate_e2e.py` already proves the gate is **correct** — it
turns `AINDY_SYSCALL_IDEMPOTENCY` on and dispatches the same syscall **twice, sequentially**.
Sequential dedup is the easy half: the first call has already committed its `effect_records` row
before the second one looks.

**Contention is the risk the flag actually carries.** N callers racing the same
`(action_type, input, scope)` all reach the gate before any of them has committed — which
nothing in this repository had ever exercised.

★★ WHAT IT FOUND ON ITS FIRST RUN
---------------------------------
**`EXACTLY_ONCE` is not exactly-once under contention.** Eight concurrent identical calls ran the
handler **twice**. That is by design — the gate degrades to `AT_LEAST_ONCE` when it loses the
insert race to a live pending row, because strict at-most-once needs advisory locking — and
`IDEMPOTENCY_CONTRACT.md` documents it precisely.

**The gap was the index, not the code.** `CLAUDE.md`'s `IDEM-11` line said *"at-most-once is
built"* with no concurrency caveat, and that line is what an implementer reads before flipping
the flag. Corrected there; pinned here.

★ The flag-off control is not optional
--------------------------------------
``test_without_the_flag_the_handler_runs_once_per_caller`` runs the identical driver with the
gate off and asserts the handler runs **N times**. It is doing two jobs:

1. **Proving the drive is actually concurrent.** If thread ramp-up serialised the callers, the
   flag-off run would also dedup by accident and the flag-on assertion would be measuring
   nothing. This is `EVENTBUS-COVERAGE-1`'s variant 6 in its most expensive form — a soak result
   that reads as evidence and is not.
2. **Being the regression baseline** the soak is compared against.

★ The gate is now observable — it was not when this file was written
-------------------------------------------------------------------
When this soak first ran, **nothing observed the gate at all**: ``aindy_durable_effects`` and
``aindy_effect_attribution`` are ContextVars, not metrics, so with the flag on an operator had no
way to tell whether the gate was firing, replaying, or silently degrading. That absence was the
real blocker on a *production* soak — there was nothing to read.

``aindy_effect_gate_outcomes_total{outcome=reserved|replayed|degraded|reclaimed}`` now exists,
and this file asserts on it. **``degraded`` is the label that matters**: a deployment where it is
a meaningful fraction of ``reserved`` is one where the guarantee the operator thinks they enabled
is not the one they have.
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


def test_the_gate_degrades_to_at_least_once_under_contention(monkeypatch, testing_session_factory):
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
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    name, runs = _register_probe(exactly_once=True)

    # ★ Assert on the METRIC, not on a log line. Three instruments were tried here and the
    # first two were unusable:
    #
    #   caplog          — the warning is emitted from a WORKER THREAD by a module logger and
    #                     caplog did not capture it reliably across that boundary. It failed CI
    #                     on a docstring-only commit, which is the signature of an unreliable
    #                     instrument rather than a regression.
    #   a logger spy    — thread-safe and config-independent, but it observes a log line, which
    #                     is not what an operator has.
    #   this counter    — thread-safe, and it is the SAME signal production would read.
    #
    # A soak whose instrument cannot distinguish "the mechanism did not fire" from "I failed to
    # observe it" produces exactly the ambiguous result a soak must not produce.
    # ★ BOTH degradation labels. There are two paths to AT_LEAST_ONCE and asserting only one
    # under-reports — which is exactly what this test caught in CI on 2026-08-20: the handler ran
    # twice while `degraded` stayed flat, because the downgrade came through the DISPATCHER's
    # gate-failure branch rather than the LEDGER's live-pending-row branch. That branch counted
    # nothing at all.
    #
    # The property being asserted is not "the contention path fired" — it is "a downgrade was
    # never silent". Pinning it to one label made a *correct* runtime look broken and, worse,
    # would have let a real silent downgrade through the other path.
    with metric_window(
        "aindy_effect_gate_outcomes_total", labels={"outcome": "degraded"}
    ) as degraded, metric_window(
        "aindy_effect_gate_outcomes_total", labels={"outcome": "degraded_gate_error"}
    ) as degraded_error:
        # Bound rather than inlined: the second wave below must target the SAME action_id,
        # which is derived from (syscall name, payload, scope=eu_id).
        eu_id, payload = str(uuid.uuid4()), {"x": 1}
        outcome = _drive(name, eu_id, payload)

    assert outcome.ok, (
        f"{len(outcome.failures)} of {WORKERS} concurrent callers raised — the gate must "
        f"serialise, replay or degrade, never fail. First: "
        f"{outcome.failures[0] if outcome.failures else None}"
    )
    assert set(r.get("status") for r in outcome.results) == {"success"}

    # ★★ THIS ASSERTION USED TO READ `1 <= len(runs) < WORKERS`, AND IT WAS WRONG IN THE SAME
    # WAY THE ORIGINAL `== 1` WAS — one step less strict, and still stricter than the contract.
    #
    # It failed CI on 2026-09-04 with `8 < 8` and passed on an immediate re-run of the SAME
    # commit. That is the signature of an assertion on a race outcome, not a regression: the
    # contract says a caller that loses the insert race to a live pending row degrades to
    # AT_LEAST_ONCE, and with WORKERS barrier-synchronised callers it is entirely legal for ALL
    # of them to lose it. So `< WORKERS` can fail on a completely correct runtime, which makes
    # it the shape `CLAUDE.md` catalogues as variant 10: an instrument that cannot distinguish
    # "the gate is broken" from "the threads happened to be tightly synchronised".
    #
    # The upper bound was reaching for something real — "the gate must not be a no-op" — so it
    # is replaced rather than deleted. What replaces it is the SECOND WAVE below, which tests
    # that property deterministically instead of depending on scheduler luck.
    assert len(runs) >= 1, "no caller ran the handler at all; the drive did not dispatch"

    # ★ The degradation must be COUNTABLE. It is the only signal an operator gets that
    # EXACTLY_ONCE did not hold for a given call.
    if len(runs) > 1:
        accounted = (
            degraded.delta("aindy_effect_gate_outcomes_total")
            + degraded_error.delta("aindy_effect_gate_outcomes_total")
        )
        assert accounted >= 1, (
            f"the handler ran {len(runs)} times but NEITHER degradation counter moved — the "
            f"downgrade would be SILENT in production, and an operator would have no way to "
            f"know that EXACTLY_ONCE did not hold for those calls. If this fires again, look "
            f"for a THIRD path to AT_LEAST_ONCE that nothing counts."
        )

    # ── ★★ THE SECOND WAVE — what the deleted upper bound was actually reaching for ─────────
    #
    # The first wave races the INSERT, and how many callers lose that race is scheduler luck.
    # This wave races nothing: the record is committed and terminal before it starts, so every
    # caller must resolve it as already-done and REPLAY. That is the guarantee the contract
    # actually makes, and it holds regardless of timing.
    #
    # ★ It is also the liveness control the file was missing. If the gate became a total no-op,
    # the old bound would only catch it when the scheduler happened to cooperate; this catches
    # it every time.
    before = len(runs)
    second = _drive(name, eu_id, payload)
    second.assert_all_succeeded()

    assert len(runs) == before, (
        f"the handler ran {len(runs) - before} more time(s) on a second wave against an "
        f"already-COMPLETED effect record. Nothing was racing: the row was committed and "
        f"terminal before this wave started, so every caller had to replay it. This is the "
        f"gate doing nothing, and unlike the contention case it is unambiguous."
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
