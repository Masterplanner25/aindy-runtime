"""DUR-2 — per-run at-most-once signal (Durable Execution program).

A continuation driver marks the re-driven run with ``durable_effects_scope()`` so the
effect-boundary chokepoints dedup its effects WITHOUT any per-tool/per-syscall EXACTLY_ONCE
declaration or master flag. These cover the contextvar + the memory chokepoint honoring it +
the continuation-driver wrap; the syscall/tool chokepoints are covered in their own harnesses
(test_idempotency_gate / test_tool_idempotency).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only


def test_durable_effects_scope_roundtrip():
    from AINDY.kernel.effect_ledger import durable_effects_active, durable_effects_scope

    assert durable_effects_active() is False
    with durable_effects_scope():
        assert durable_effects_active() is True
    assert durable_effects_active() is False


def test_memory_gate_honors_durable_signal_with_flag_off():
    """Flag off: no gate; flag off + inside durable scope: gate fires (declaration-free)."""
    from AINDY.runtime import nodus_runtime_adapter as nra
    from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext
    from AINDY.kernel.effect_ledger import durable_effects_scope

    ctx = NodusExecutionContext(user_id="u", execution_unit_id="eu", effect_scope="node")
    writes = [{"kind": "memory.write", "content": "a", "tags": [], "node_type": "insight"}]

    def _resolve_factory(sink):
        return lambda *a, **k: sink.append(1) or (False, None)

    with patch.dict(os.environ, {"AINDY_MEMORY_IDEMPOTENCY": "false"}), \
        patch("AINDY.kernel.effect_ledger.complete_effect_record", lambda *a, **k: None), \
        patch("AINDY.db.dao.memory_node_dao.MemoryNodeDAO", return_value=MagicMock()):
        off = []
        with patch("AINDY.kernel.effect_ledger.resolve_effect_record", _resolve_factory(off)):
            nra._apply_deferred_memory_writes(MagicMock(), writes, ctx)
        assert off == [], "flag off + no durable scope must not gate"

        on = []
        with patch("AINDY.kernel.effect_ledger.resolve_effect_record", _resolve_factory(on)):
            with durable_effects_scope():
                nra._apply_deferred_memory_writes(MagicMock(), writes, ctx)
        assert on == [1], "durable signal must engage the memory gate despite flag off"


def test_agent_durable_resume_wraps_callback_and_restores():
    from AINDY.core.agent_continuation import _durable_resume
    from AINDY.kernel.effect_ledger import durable_effects_active

    seen = {}

    def _cb():
        seen["active"] = durable_effects_active()

    _durable_resume(_cb)()
    assert seen["active"] is True          # callback ran inside the scope
    assert durable_effects_active() is False  # scope restored afterward


def test_flow_continuation_wraps_resume_in_durable_scope(monkeypatch):
    """_dispatch_resume re-drives on a thread; the resume call must see the durable signal."""
    import threading
    from AINDY.core import flow_continuation as fc
    from AINDY.kernel.effect_ledger import durable_effects_active

    seen = {}
    done = threading.Event()

    class _Runner:
        def __init__(self, **kw):
            pass

        def resume(self, run_id):
            seen["active"] = durable_effects_active()
            done.set()

    monkeypatch.setattr(
        "AINDY.runtime.flow_engine.PersistentFlowRunner", _Runner, raising=False
    )
    monkeypatch.setattr(
        "AINDY.runtime.flow_engine.FLOW_REGISTRY", {"f": object()}, raising=False
    )
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: MagicMock())

    fc._dispatch_resume(run_id="r1", flow_name="f", user_id="u", workflow_type="wf")
    assert done.wait(timeout=5), "resume thread did not run"
    assert seen["active"] is True
