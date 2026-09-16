"""IDEM-12 — a second ``undo`` on the same run must not re-invoke compensators.

``undo_run_effects`` selected every ``status == "success"`` effect and compensated it, never
consulting ``effect_reversals`` — so a deliberate (or retried, gate-off) second undo ran every
compensator again: a double refund, a second reversing transfer, duplicate audit rows. Latent
only because zero compensators are registered; it goes live with the first one.

The fix is at reversal's own layer, independent of the idempotency gate: an effect with a
``reversed`` audit row is skipped and reported as ``already_reversed``. ``irreversible`` and
``failed`` rows do NOT suppress a retry — a transient compensator failure must stay retryable.
"""
from __future__ import annotations

import uuid

import pytest

from AINDY.core.effect_compensation import undo_run_effects
from tests.unit.test_effect_compensation import (  # noqa: F401 — fixtures
    _BOOM,
    _IRREVERSIBLE,
    _REVERSIBLE,
    _reversals,
    _seed_effect,
    _seed_eu,
    compensators,
    session,
)

pytestmark = pytest.mark.runtime_only

USER = uuid.UUID("00000000-0000-0000-0000-00000000aa01")


def test_second_undo_does_not_recompensate(session, compensators):
    eu = _seed_eu(session, user_id=USER, run_id="run-1")
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, result={"id": "a"})
    _seed_effect(session, eu=eu, action_type=_REVERSIBLE, result={"id": "b"})

    first = undo_run_effects("run-1", db=session)
    assert first["reversed"] == [_REVERSIBLE, _REVERSIBLE]
    assert len(compensators) == 2

    second = undo_run_effects("run-1", db=session)

    # The compensators ran exactly once per effect, across both calls.
    assert len(compensators) == 2, "second undo re-invoked compensators"
    assert second["reversed"] == []
    assert second["already_reversed"] == [_REVERSIBLE, _REVERSIBLE]
    # And the audit log did not grow a duplicate `reversed` row.
    rows = _reversals(session)
    assert sorted(r.status for r in rows) == ["reversed", "reversed"]


def test_failed_compensation_stays_retryable(session, compensators):
    """A `failed` audit row must NOT suppress the retry — only `reversed` does."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    eu = _seed_eu(session, user_id=USER, run_id="run-2")
    _seed_effect(session, eu=eu, action_type=_BOOM, result={"id": "c"})

    first = undo_run_effects("run-2", db=session)
    assert [f["action_type"] for f in first["failed"]] == [_BOOM]

    # The transient cause clears: swap in a compensator that succeeds.
    def _now_works(effect, _context):
        return {"undone": (effect.get("result_payload") or {}).get("id")}

    SYSCALL_REGISTRY[_BOOM].compensate = _now_works
    second = undo_run_effects("run-2", db=session)

    assert second["reversed"] == [_BOOM]
    assert second["already_reversed"] == []
    assert sorted(r.status for r in _reversals(session)) == ["failed", "reversed"]


def test_irreversible_is_resurfaced_not_suppressed(session, compensators):
    """An effect with no compensator is `irreversible` on every undo — surfaced, never hidden
    behind a prior `irreversible` row, and never mistaken for reversed."""
    eu = _seed_eu(session, user_id=USER, run_id="run-3")
    _seed_effect(session, eu=eu, action_type=_IRREVERSIBLE, result={"id": "d"})

    undo_run_effects("run-3", db=session)
    second = undo_run_effects("run-3", db=session)

    assert second["irreversible"] == [_IRREVERSIBLE]
    assert second["already_reversed"] == []


def test_reversed_row_from_another_run_does_not_suppress(session, compensators):
    """Suppression keys on the EFFECT, not the action type: run-4's reversal of a
    `_REVERSIBLE` effect says nothing about run-5's."""
    eu4 = _seed_eu(session, user_id=USER, run_id="run-4")
    _seed_effect(session, eu=eu4, action_type=_REVERSIBLE, result={"id": "e"})
    undo_run_effects("run-4", db=session)
    assert len(compensators) == 1

    eu5 = _seed_eu(session, user_id=USER, run_id="run-5")
    _seed_effect(session, eu=eu5, action_type=_REVERSIBLE, result={"id": "f"})
    result = undo_run_effects("run-5", db=session)

    assert result["reversed"] == [_REVERSIBLE]
    assert len(compensators) == 2
