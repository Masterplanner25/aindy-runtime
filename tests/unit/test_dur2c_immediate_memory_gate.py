"""DUR-2c — gate immediate in-subprocess bridge memory writes (remember/record_outcome).

These run immediately via a direct DAO (not the deferred list DUR-1 gates), so a continuation
re-run would double-write them. AINDYMemoryBridge._gate dedups them through the shared
EffectRecord ledger, keyed on (run_scope, per-action ordinal) — content-independent, with
cached-result replay. Active only under the per-run at-most-once signal (or the memory flag).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only


def _bridge(run_scope=""):
    from AINDY.nodus.runtime.memory_bridge import AINDYMemoryBridge

    return AINDYMemoryBridge(user_id="u", run_scope=run_scope)


def test_gate_passthrough_without_run_scope():
    """No run scope → gating inactive → do_write runs (current behavior)."""
    b = _bridge()  # no scope
    calls = []
    assert b._gate("memory.remember", lambda: calls.append(1) or "id1") == "id1"
    assert calls == [1]


def test_gate_passthrough_when_signal_inactive():
    """Run scope set but no durable signal / flag → still passthrough."""
    b = _bridge("run:seg0")
    calls = []
    # not inside a durable_effects_scope and AINDY_MEMORY_IDEMPOTENCY unset
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("AINDY_MEMORY_IDEMPOTENCY", None)
        assert b._gate("memory.remember", lambda: calls.append(1) or "x") == "x"
    assert calls == [1]


def test_gate_replays_cached_result_on_rerun():
    """The original run writes + finalizes; a re-run (fresh bridge, same scope, same ordinal)
    replays the cached result WITHOUT re-executing do_write."""
    from AINDY.kernel.effect_ledger import durable_effects_scope

    calls = []

    # Original run.
    b1 = _bridge("run:seg0")
    with patch("AINDY.kernel.effect_ledger.resolve_effect_record", lambda *a, **k: (False, None)), \
        patch("AINDY.kernel.effect_ledger.complete_effect_record", lambda *a, **k: None), \
        patch.object(b1, "_session", lambda: MagicMock()):
        with durable_effects_scope():
            r1 = b1._gate("memory.remember", lambda: calls.append("w") or "id1")
    assert r1 == "id1" and calls == ["w"]

    # Re-run: a fresh bridge (ordinals reset) with the SAME scope; the ledger now reports the
    # slot already succeeded → cached id replayed, do_write NOT called again.
    b2 = _bridge("run:seg0")
    with patch("AINDY.kernel.effect_ledger.resolve_effect_record",
               lambda *a, **k: (True, {"result": "id1"})), \
        patch("AINDY.kernel.effect_ledger.complete_effect_record", lambda *a, **k: None), \
        patch.object(b2, "_session", lambda: MagicMock()):
        with durable_effects_scope():
            r2 = b2._gate("memory.remember", lambda: calls.append("w") or "id2")
    assert r2 == "id1", "re-run must replay the original node id"
    assert calls == ["w"], "do_write must NOT run on the re-run"


def test_gate_distinct_ordinals_do_not_collide():
    """Two remember() calls in one run get distinct action_ids (both write)."""
    from AINDY.kernel.effect_ledger import durable_effects_scope

    b = _bridge("run:seg0")
    seen = []

    def _resolve(db, action_id, action_type, payload, **k):
        seen.append(action_id)
        return (False, None)

    with patch("AINDY.kernel.effect_ledger.resolve_effect_record", _resolve), \
        patch("AINDY.kernel.effect_ledger.complete_effect_record", lambda *a, **k: None), \
        patch.object(b, "_session", lambda: MagicMock()):
        with durable_effects_scope():
            b._gate("memory.remember", lambda: "a")
            b._gate("memory.remember", lambda: "b")
    assert len(seen) == 2 and seen[0] != seen[1], "distinct calls must not share a key"


def test_gate_ledger_failure_degrades_to_write():
    """A ledger error must never block the write — degrade to at-least-once."""
    from AINDY.kernel.effect_ledger import durable_effects_scope

    b = _bridge("run:seg0")
    calls = []
    with patch("AINDY.kernel.effect_ledger.resolve_effect_record",
               side_effect=RuntimeError("db down")), \
        patch.object(b, "_session", lambda: MagicMock()):
        with durable_effects_scope():
            assert b._gate("memory.remember", lambda: calls.append(1) or "x") == "x"
    assert calls == [1]
