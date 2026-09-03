"""`EFFECT-PARTIAL-1` + `EFFECT-OUTCOME-UNKNOWN-1` — the effect record can say two more things.

Both entries are about the same column, and the registry says to settle them together. They are
the two things `EffectRecord.status` could not express:

- **`partial`** — some units of a batched effect applied and some did not. The envelope is
  binary, so a 5-unit effect with 2 failures forced through it is either a **lie** (`success`,
  silently partial) or a **waste** (`error`, discarding the 3 that landed).
- **`unknown`** — dispatched, outcome unobserved. Narrowly: a read timeout after a full request
  write, which the outcome-ambiguity design identifies as the *only* genuinely ambiguous phase.
  Everything either side of it is knowable.

No migration: the column is `String(32)` with no CHECK.

★★ WHAT THESE TESTS ARE FOR, AND THE TRAP THE ENTRY NAMES
-----------------------------------------------------------
`EFFECT-PARTIAL-1` says this is *"trusting-a-green-check variant 6 by construction — the test
needs a liveness control or it passes with the reporting wire broken."* That is exactly right and
it shapes the file: a test asserting *"a partial effect is not recorded as success"* passes just
as happily when nothing records anything at all. So every negative assertion here is paired with
a positive one that would fail if the write path were dead.

The other half is that a vocabulary is only a vocabulary if it can **refuse**. These values were
a docstring listing three strings while `complete_effect_record` accepted any `str` — so the
tests are as much about what the column rejects as about what it now accepts.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.runtime_only


def _action_id() -> str:
    return f"soak-{uuid.uuid4().hex}"


def _reserve(db, action_id: str):
    """Create a pending effect row the way the gate does."""
    from AINDY.db.models.effect_record import EFFECT_STATUS_PENDING, EffectRecord

    record = EffectRecord(
        action_id=action_id,
        action_type="test.effect",
        input_hash=uuid.uuid4().hex,  # NOT NULL; part of the dedup identity, irrelevant here
        status=EFFECT_STATUS_PENDING,
    )
    db.add(record)
    db.flush()
    return record


# ── the vocabulary itself ────────────────────────────────────────────────────


def test_the_two_new_values_exist_and_are_terminal():
    """★ Terminal, not a third kind of pending — which decides how the TTL job treats them.

    That job reaps by `status != "pending"` and hard-excludes pending rows. An `unknown` that
    counted as pending would be warned about hourly as a stuck handler *and* never cleaned up —
    an unbounded table full of rows that are never going to resolve on their own.
    """
    from AINDY.db.models.effect_record import (
        EFFECT_STATUS_PARTIAL,
        EFFECT_STATUS_PENDING,
        EFFECT_STATUS_UNKNOWN,
        EFFECT_STATUSES,
        TERMINAL_EFFECT_STATUSES,
    )

    assert {EFFECT_STATUS_PARTIAL, EFFECT_STATUS_UNKNOWN} <= EFFECT_STATUSES
    assert {EFFECT_STATUS_PARTIAL, EFFECT_STATUS_UNKNOWN} <= TERMINAL_EFFECT_STATUSES
    assert EFFECT_STATUS_PENDING not in TERMINAL_EFFECT_STATUSES


def test_the_declared_set_and_the_terminal_set_agree():
    """A drifting pair here would let a value be legal to store and illegal to complete with."""
    from AINDY.db.models.effect_record import EFFECT_STATUSES, TERMINAL_EFFECT_STATUSES

    assert TERMINAL_EFFECT_STATUSES < EFFECT_STATUSES
    assert EFFECT_STATUSES - TERMINAL_EFFECT_STATUSES == {"pending"}


# ── the write path: it must accept the new values ────────────────────────────


@pytest.mark.parametrize("status", ["success", "failed", "partial", "unknown"])
def test_every_terminal_status_can_be_recorded(db_session, status):
    """★★ THE LIVENESS HALF, and the entry predicted it would be needed.

    Every refusal assertion below would pass just as well if `complete_effect_record` wrote
    nothing at all. This is the test that fails in that world — it reads the row back and
    asserts the value actually landed.
    """
    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.kernel.effect_ledger import complete_effect_record

    action_id = _action_id()
    _reserve(db_session, action_id)

    complete_effect_record(db_session, action_id, status, {"units": [1, 2]})

    db_session.expire_all()
    row = (
        db_session.query(EffectRecord)
        .filter(EffectRecord.action_id == action_id)
        .first()
    )
    assert row is not None, "the effect row vanished"
    assert row.status == status, f"status was not recorded: got {row.status!r}"
    assert row.completed_at is not None, "a completed effect must carry completed_at"


def test_a_partial_effect_carries_which_units_landed(db_session):
    """★ A `partial` without a payload is worse than `failed`.

    It says something went wrong and removes the ability to say what. The value is only worth
    having if the per-unit outcome travels with it, so this pins that the payload survives —
    `result_payload` is where the recoverable half of a partial effect lives.
    """
    from AINDY.db.models.effect_record import EffectRecord
    from AINDY.kernel.effect_ledger import complete_effect_record

    action_id = _action_id()
    _reserve(db_session, action_id)
    payload = {"applied": ["a", "b", "c"], "failed": ["d", "e"]}

    complete_effect_record(db_session, action_id, "partial", payload)

    db_session.expire_all()
    row = db_session.query(EffectRecord).filter(EffectRecord.action_id == action_id).first()
    assert row.result_payload == payload, (
        "the per-unit outcome did not survive. A 'partial' status with no record of which units "
        "applied is not recoverable from — it is 'failed' with extra ambiguity."
    )


# ── the write path: it must refuse everything else ───────────────────────────


def test_an_unknown_status_string_is_refused(db_session):
    """★ A vocabulary that cannot refuse a value is not a vocabulary.

    The column is `String(32)` with no CHECK and this function took any `str`, so a typo wrote a
    status nothing would ever query for — and the TTL job, which reaps by `status != "pending"`,
    would have silently treated it as terminal.
    """
    from AINDY.kernel.effect_ledger import complete_effect_record

    action_id = _action_id()
    _reserve(db_session, action_id)

    with pytest.raises(ValueError) as caught:
        complete_effect_record(db_session, action_id, "succeded", None)  # typo, on purpose

    assert "succeded" in str(caught.value)
    assert "partial" in str(caught.value), "the error should name the legal values"


def test_completing_back_to_pending_is_refused(db_session):
    """★★ The specific one worth its own test, because `pending` is a *plausible* mistake.

    It is the obvious place to reach for when an outcome is unobserved — and it is the wrong
    one twice over: the TTL job hard-excludes pending rows, so an honest ambiguity parked there
    is never cleaned up, and the stale-handler warning fires on it hourly as a malfunction.
    `unknown` exists precisely so nobody has to make that trade.
    """
    from AINDY.kernel.effect_ledger import complete_effect_record

    action_id = _action_id()
    _reserve(db_session, action_id)

    with pytest.raises(ValueError) as caught:
        complete_effect_record(db_session, action_id, "pending", None)

    message = str(caught.value)
    assert "stuck handler" in message or "TTL" in message, (
        "the error must say WHY pending is refused, not merely that it is — the reason is the "
        "whole argument for a separate 'unknown' value"
    )


# ── the TTL job's view of the new values ─────────────────────────────────────


def test_the_ttl_reaper_treats_the_new_values_as_completed(db_session):
    """The cleanup job selects by `status != "pending"`, so this follows — but it is the kind of
    consequence that is obvious until someone adds a fourth pending-ish state.

    Asserted against the same predicate the job uses rather than by calling it, because the job
    opens its own session and its own cutoffs; what matters here is that the new values fall on
    the reapable side of the line.
    """
    from AINDY.db.models.effect_record import (
        EFFECT_STATUS_PARTIAL,
        EFFECT_STATUS_UNKNOWN,
        TERMINAL_EFFECT_STATUSES,
    )

    for status in (EFFECT_STATUS_PARTIAL, EFFECT_STATUS_UNKNOWN):
        assert status != "pending"
        assert status in TERMINAL_EFFECT_STATUSES, (
            f"{status!r} is not terminal, so the TTL job would never reap it and the stale "
            f"handler warning would fire on it every hour"
        )
