"""DUR-2b — subprocess propagation of the per-run at-most-once signal + stable per-segment
memory-effect scope (Durable Execution program).

A contextvar can't cross the nodus worker subprocess, so DUR-2b threads the durable signal
through the subprocess payload; and because every agent segment runs through the one
``nodus.execute`` node sharing the run's execution_unit_id, it adds a per-segment
discriminator so segment memory-dedup scopes stay distinct + re-run-stable.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only


def test_durable_effects_active_safe_reflects_scope():
    from AINDY.runtime.nodus_runtime_adapter import _durable_effects_active_safe
    from AINDY.kernel.effect_ledger import durable_effects_scope

    assert _durable_effects_active_safe() is False
    with durable_effects_scope():
        assert _durable_effects_active_safe() is True
    assert _durable_effects_active_safe() is False


def test_dur_effect_scope_appends_segment_discriminator():
    from AINDY.runtime.nodus_adapter import _dur_effect_scope

    # Flow path: node name only (no per-segment discriminator).
    assert _dur_effect_scope({"node_name": "nodus.execute"}, {}) == "nodus.execute"

    # Agent path: all segments share node "nodus.execute" + the run's execution_unit_id, so
    # the per-segment __effect_scope must make them distinct.
    s0 = _dur_effect_scope({"node_name": "nodus.execute"}, {"__effect_scope": "agent_plan_seg0"})
    s1 = _dur_effect_scope({"node_name": "nodus.execute"}, {"__effect_scope": "agent_plan_seg1"})
    assert s0 == "nodus.execute:agent_plan_seg0"
    assert s0 != s1, "distinct segments must produce distinct memory-effect scopes"


class _Proc:
    returncode = 0
    stdout = json.dumps(
        {"status": "success", "output_state": {}, "emitted_events": [], "memory_writes": []}
    )
    stderr = ""


def _capture_payload():
    captured = {}

    def _fake_run(cmd, input=None, **kw):
        captured["payload"] = json.loads(input)
        return _Proc()

    return captured, _fake_run


def test_subprocess_payload_carries_durable_flag(monkeypatch):
    """The per-run signal is written into the subprocess payload so the worker can
    re-establish it (the parent contextvar cannot cross the process boundary).

    ★ Pins the FRESH-SUBPROCESS path explicitly. `AINDY_NODUS_WARM_POOL` defaults ON since
    2026-08-19, so without this the adapter takes the warm path, `subprocess.run` is never
    called, and the assertion below has nothing to read. The warm path's equivalent is
    `test_warm_pool_payload_also_carries_durable_flag`.
    """
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "0")
    from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext, NodusRuntimeAdapter
    from AINDY.kernel.effect_ledger import durable_effects_scope

    adapter = NodusRuntimeAdapter(MagicMock())
    ctx = NodusExecutionContext(user_id="u", execution_unit_id="eu")
    captured, fake_run = _capture_payload()

    with patch("AINDY.runtime.nodus_runtime_adapter.subprocess.run", fake_run):
        with durable_effects_scope():
            adapter.run_script("let x = 1", ctx)
        assert captured["payload"]["context"]["durable_effects"] is True

        adapter.run_script("let x = 1", ctx)  # outside the scope
        assert captured["payload"]["context"]["durable_effects"] is False


def test_warm_pool_payload_also_carries_durable_flag(monkeypatch):
    """★ The path this now takes by DEFAULT, and nothing asserted it before the flip.

    DUR-2b's guarantee is that the per-run at-most-once signal survives the process boundary,
    because a ContextVar cannot cross it. That guarantee is only as good as the path actually
    taken — and since 2026-08-19 the default path is the WARM POOL, not a fresh subprocess.
    A warm path that dropped `durable_effects` would silently disable at-most-once for every
    continued run while every existing DUR-2b test stayed green.
    """
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "1")
    from AINDY.runtime import nodus_worker_pool as pool_mod
    from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext, NodusRuntimeAdapter
    from AINDY.kernel.effect_ledger import durable_effects_scope

    captured = {}

    class _FakePool:
        def execute(self, payload, *, timeout_s):
            captured["payload"] = payload
            return {
                "status": "success", "output_state": {}, "emitted_events": [],
                "memory_writes": [], "simulated_effects": [], "stdout_log": "",
            }

    monkeypatch.setattr(pool_mod, "get_pool", lambda: _FakePool())

    adapter = NodusRuntimeAdapter(MagicMock())
    ctx = NodusExecutionContext(user_id="u", execution_unit_id="eu")

    with durable_effects_scope():
        adapter.run_script("let x = 1", ctx)
    assert captured["payload"]["context"]["durable_effects"] is True, (
        "the warm path dropped the durable-effects signal — at-most-once would be silently "
        "disabled for continued runs on the default execution path"
    )

    adapter.run_script("let x = 1", ctx)
    assert captured["payload"]["context"]["durable_effects"] is False


def test_worker_wraps_run_source_in_durable_scope_when_flagged():
    """The worker re-establishes the durable scope so in-subprocess sys()/call_tool() gates
    see it. Drive nodus_worker.main() with a crafted payload and assert the signal is active
    during run_source."""
    import io
    from AINDY.kernel.effect_ledger import durable_effects_active

    seen = {}
    rt = MagicMock()

    def _run_source(*a, **k):
        seen["active"] = durable_effects_active()
        return {"ok": True}

    rt.run_source.side_effect = _run_source

    payload = {
        "script": "let x = 1",
        "state": {},
        "context": {"user_id": "u", "execution_unit_id": "eu", "durable_effects": True},
    }

    import AINDY.runtime.nodus_worker as w

    with patch.object(w.sys, "stdin", io.StringIO(json.dumps(payload))), \
        patch("nodus.runtime.embedding.NodusRuntime", lambda *a, **k: rt), \
        patch("AINDY.nodus.runtime.memory_bridge.AINDYMemoryBridge", lambda **k: MagicMock()):
        w.main()

    assert seen.get("active") is True, "worker must run the VM inside the durable scope"
    assert durable_effects_active() is False, "scope restored after run"
