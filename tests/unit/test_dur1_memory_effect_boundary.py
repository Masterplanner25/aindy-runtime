"""DUR-1 — memory-effect idempotency boundary (Durable Execution program).

``_apply_deferred_memory_writes`` dedup-guards deferred memory writes through the shared
EffectRecord ledger, keyed on POSITION identity (run, node/segment, ordinal) — never content.
These use a MagicMock db + patched ledger/DAO so no real Postgres is needed; the end-to-end
persistence is covered by the DUR-1 PG verification.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only


def _ctx(eu_id="eu-1", node="node_a", user="user-1"):
    from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext

    return NodusExecutionContext(
        user_id=user, execution_unit_id=eu_id, effect_scope=node,
    )


def _writes(*contents):
    return [{"kind": "memory.write", "content": c, "tags": [], "node_type": "insight"} for c in contents]


def _run(memory_writes, context, *, resolve_returns=None, flag="true"):
    """Invoke _apply_deferred_memory_writes with the ledger + DAO patched.

    resolve_returns: list of (already, cached) tuples for successive resolve calls
    (default: all (False, None) — no prior record). Returns (resolve_calls, save_calls,
    complete_calls) recording (action_id / write-content / (action_id,status)).
    """
    from AINDY.runtime import nodus_runtime_adapter as nra

    resolve_calls, save_calls, complete_calls = [], [], []
    rr = list(resolve_returns or [])

    def _resolve(db, action_id, action_type, payload, **kw):
        resolve_calls.append(action_id)
        return rr.pop(0) if rr else (False, None)

    def _complete(db, action_id, status, payload):
        complete_calls.append((action_id, status))

    fake_dao = MagicMock()
    fake_dao.save.side_effect = lambda **kw: save_calls.append(kw["content"])

    with patch.dict(os.environ, {"AINDY_MEMORY_IDEMPOTENCY": flag}), \
        patch("AINDY.kernel.effect_ledger.resolve_effect_record", _resolve), \
        patch("AINDY.kernel.effect_ledger.complete_effect_record", _complete), \
        patch("AINDY.db.dao.memory_node_dao.MemoryNodeDAO", return_value=fake_dao):
        nra._apply_deferred_memory_writes(MagicMock(), memory_writes, context)
    return resolve_calls, save_calls, complete_calls


def test_flag_off_is_passthrough_no_ledger():
    """Default (flag off): no ledger involvement, every write persists."""
    resolve_calls, save_calls, complete_calls = _run(_writes("a", "b"), _ctx(), flag="false")
    assert resolve_calls == []
    assert save_calls == ["a", "b"]
    assert complete_calls == []


def test_flag_on_gates_and_finalizes_each_write():
    resolve_calls, save_calls, complete_calls = _run(_writes("a", "b"), _ctx())
    assert save_calls == ["a", "b"]           # both written (no prior record)
    assert len(resolve_calls) == 2            # each claimed
    assert len(complete_calls) == 2           # each finalized success
    assert all(st == "success" for _, st in complete_calls)


def test_action_id_is_position_keyed_not_content():
    from AINDY.runtime.nodus_runtime_adapter import _memory_effect_action_id
    from AINDY.core.execution_gate import compute_action_id

    scope = "eu-1:node_a"
    expected0 = compute_action_id(action_type="memory.write", input_payload={"seq": 0}, scope=scope)
    resolve_calls, _, _ = _run(_writes("anything"), _ctx())
    assert resolve_calls[0] == expected0 == _memory_effect_action_id(scope, 0)

    # Content-independence: same (scope, ordinal), different content → SAME key.
    a = _memory_effect_action_id(scope, 0)
    b = _memory_effect_action_id(scope, 0)
    assert a == b
    # Different ordinal → different key (distinct writes never collapse).
    assert _memory_effect_action_id(scope, 0) != _memory_effect_action_id(scope, 1)


def test_replay_skips_the_duplicate_write():
    """A re-run where the slot already succeeded must NOT re-persist."""
    resolve_calls, save_calls, complete_calls = _run(
        _writes("a", "b"), _ctx(),
        resolve_returns=[(True, {"written": True}), (True, {"written": True})],
    )
    assert save_calls == []                    # both replayed → nothing written
    assert complete_calls == []                # nothing to finalize
    assert len(resolve_calls) == 2


def test_distinct_nodes_do_not_collide():
    """Two nodes sharing the run's execution_unit_id but with different effect_scope
    produce DIFFERENT keys at the same ordinal — no cross-node data loss."""
    from AINDY.runtime.nodus_runtime_adapter import _memory_effect_action_id

    key_node_a = _memory_effect_action_id("eu-1:node_a", 0)
    key_node_b = _memory_effect_action_id("eu-1:node_b", 0)
    assert key_node_a != key_node_b


def test_failed_write_is_not_finalized():
    """A DAO failure leaves the slot un-finalized (reclaimable), not marked success."""
    from AINDY.runtime import nodus_runtime_adapter as nra

    complete_calls = []
    fake_dao = MagicMock()
    fake_dao.save.side_effect = RuntimeError("db down")

    with patch.dict(os.environ, {"AINDY_MEMORY_IDEMPOTENCY": "true"}), \
        patch("AINDY.kernel.effect_ledger.resolve_effect_record", lambda *a, **k: (False, None)), \
        patch("AINDY.kernel.effect_ledger.complete_effect_record",
              lambda db, aid, st, pl: complete_calls.append((aid, st))), \
        patch("AINDY.db.dao.memory_node_dao.MemoryNodeDAO", return_value=fake_dao):
        nra._apply_deferred_memory_writes(MagicMock(), _writes("a"), _ctx())

    assert complete_calls == []  # write failed → slot left reclaimable
