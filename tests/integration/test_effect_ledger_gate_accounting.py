"""Every path that runs the handler must move a gate counter — on real Postgres.

★ WHY THIS FILE EXISTS, AND WHY IT IS NOT A SOAK
------------------------------------------------
``test_soak_idempotency_contention.py`` ends with an assertion carrying this message:

    the handler ran N times but NEITHER degradation counter moved ... If this fires again,
    look for a THIRD path to AT_LEAST_ONCE that nothing counts.

It fired, on a docs-only PR. There was a third path, and the reason it stayed hidden for two
rounds of fixes is that **the soak could only see it by luck**: its degradation assertion is
guarded by ``if len(runs) > 1``, so a run where the threads happen not to collide skips the
assertion entirely and reports green. That is `CLAUDE.md`'s catalogue variant 9 — *green because
there was nothing to catch* — and it is why the regression guard for this belongs in a
**deterministic** test rather than in the soak that found it.

★ THE PATH
----------
``resolve_effect_record`` opens with a ``SELECT``. A caller that finds an existing row reaches
it by one of two routes, decided purely by whether its ``SELECT`` lands before or after the
winner's ``COMMIT``:

* **before** -> row absent -> ``INSERT`` -> ``IntegrityError`` -> re-query -> counted
* **after**  -> row present on the very first ``SELECT`` -> *(was: fell through, uncounted)*

Both are the same situation and produce the same duplicate handler run. Only the first was
counted, so under contention most losers were invisible: they read the committed ``pending`` row
rather than racing the insert.

**No concurrency is required to demonstrate it**, which is the whole point of this file — the
tests below are sequential and deterministic.
"""

from __future__ import annotations

import uuid

import pytest

from AINDY.kernel import effect_ledger as EL

pytestmark = pytest.mark.integration


def _spy(monkeypatch):
    """Capture gate outcomes without touching Prometheus.

    Asserting on the label rather than on a Prometheus delta keeps this test independent of
    counter cardinality and of whether a label has been observed before — ``read_metric``'s
    "family exists but this label is unobserved" case is a soak-harness concern, not this one.
    """
    seen: list[str] = []
    monkeypatch.setattr(EL, "_count_gate", seen.append)
    return seen


def _reserve(db, action_id):
    """Claim a fresh slot and leave it ``pending`` — i.e. an in-flight effect."""
    return EL.resolve_effect_record(db, action_id, "sys.v1.test.acct", {"x": 1})


def test_a_fresh_slot_reserves_and_is_counted(monkeypatch, testing_session_factory):
    """★ LIVENESS CONTROL. If this stops counting, the spy is broken and every assertion
    below passes vacuously — the failure mode this repo files as variant 6."""
    seen = _spy(monkeypatch)
    db = testing_session_factory()
    try:
        already, _ = _reserve(db, uuid.uuid4().hex)
    finally:
        db.close()

    assert already is False, "a fresh action_id must not report a prior success"
    assert seen == ["reserved"], f"expected a single 'reserved' outcome, got {seen}"


def test_reading_a_live_pending_row_counts_degraded(monkeypatch, testing_session_factory):
    """The third path: caller 2's SELECT lands after caller 1's COMMIT.

    Caller 1 holds the slot ``pending`` and has not completed. Caller 2 finds that row on its
    opening SELECT, never attempts the insert, and therefore never reaches the
    ``IntegrityError`` branch where the degradation used to be counted.
    """
    action_id = uuid.uuid4().hex
    db1, db2 = testing_session_factory(), testing_session_factory()
    try:
        _reserve(db1, action_id)          # caller 1 reserves, does NOT complete
        seen = _spy(monkeypatch)          # spy only on caller 2
        already, cached = EL.resolve_effect_record(
            db2, action_id, "sys.v1.test.acct", {"x": 1}
        )
    finally:
        db1.close()
        db2.close()

    assert already is False, (
        "a live pending row must not be reported as a prior success — the caller would skip "
        "an effect that never actually landed"
    )
    assert cached is None
    assert seen == ["degraded"], (
        f"the handler is about to run a second time and the gate recorded {seen or 'NOTHING'}. "
        f"An uncounted duplicate is invisible in aindy_effect_gate_outcomes_total, which is the "
        f"only signal an operator gets that EXACTLY_ONCE did not hold."
    )


def test_reading_a_failed_row_counts_reclaimed(monkeypatch, testing_session_factory):
    """The same hole, reached without any concurrency at all.

    A prior attempt failed. The next caller reads that ``failed`` row directly and re-executes.
    Before the fix this was uncounted *and* skipped the reclaim — so the row kept the old
    attribution and the old ``created_at``, meaning its staleness clock ran from the first
    attempt rather than this one.
    """
    action_id = uuid.uuid4().hex
    db1, db2 = testing_session_factory(), testing_session_factory()
    try:
        _reserve(db1, action_id)
        EL.complete_effect_record(db1, action_id, "failed", None)
        seen = _spy(monkeypatch)
        already, _ = EL.resolve_effect_record(
            db2, action_id, "sys.v1.test.acct", {"x": 1}
        )
    finally:
        db1.close()
        db2.close()

    assert already is False, "a failed row must not be replayed as a success"
    assert seen == ["reclaimed"], (
        f"re-running a failed effect recorded {seen or 'NOTHING'}; the reclaim path is where "
        f"the row is re-attributed and its staleness clock reset, and skipping it silently "
        f"re-executes an effect against a stale record."
    )


def test_a_completed_row_replays_and_does_not_run_again(monkeypatch, testing_session_factory):
    """The one existing-row outcome that must NOT run the handler — pinned so a future
    refactor of the shared decision cannot turn a replay into a re-execution."""
    action_id = uuid.uuid4().hex
    db1, db2 = testing_session_factory(), testing_session_factory()
    try:
        _reserve(db1, action_id)
        EL.complete_effect_record(db1, action_id, "success", {"ok": True})
        seen = _spy(monkeypatch)
        already, cached = EL.resolve_effect_record(
            db2, action_id, "sys.v1.test.acct", {"x": 1}
        )
    finally:
        db1.close()
        db2.close()

    assert already is True, "a completed effect must be replayed, never re-executed"
    assert cached == {"ok": True}
    assert seen == ["replayed"], f"expected 'replayed', got {seen}"
