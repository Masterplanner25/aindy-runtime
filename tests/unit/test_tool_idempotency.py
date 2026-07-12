"""MEB-0 — tool-path effect boundary (idempotency) in execute_tool.

Boundary logic is isolated here by mocking the capability layer and the effect ledger;
the real dedup against Postgres is verified separately (PG-typed EffectRecord). Pins:
the boundary is doubly-gated (global flag + per-tool EXACTLY_ONCE + run_id), replay
returns the cached result without re-executing, and a ledger failure degrades safely.
"""
from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

from AINDY.agents import tool_registry as tr


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(tr.TOOL_REGISTRY)
    yield
    tr.TOOL_REGISTRY.clear()
    tr.TOOL_REGISTRY.update(saved)


def _register(name, guarantee, runs):
    def _fn(args, user_id, db):
        runs.append(args)
        return {"echoed": args}

    tr.register_tool(
        name=name, risk="low", description="t", capability="c",
        required_capability="c", category="test", egress_scope="none",
        execution_guarantee=guarantee,
    )(_fn)


@contextlib.contextmanager
def _mocks(flag: bool, resolve_return=(False, None)):
    with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch(
        "AINDY.agents.capability_service.check_tool_capability",
        return_value={"ok": True, "allowed_capabilities": [], "granted_tools": []},
    ), patch.object(tr, "queue_system_event", lambda **k: None), patch(
        "AINDY.platform_layer.secret_broker.capability_scope",
        lambda caps: contextlib.nullcontext(),
    ), patch(
        "AINDY.core.execution_gate.compute_action_id", return_value="AID-1"
    ), patch(
        "AINDY.kernel.effect_ledger.resolve_effect_record", return_value=resolve_return
    ) as m_resolve, patch(
        "AINDY.kernel.effect_ledger.complete_effect_record"
    ) as m_complete, patch(
        "AINDY.agents.tool_registry._tool_idempotency_enabled", return_value=flag
    ):
        yield m_resolve, m_complete


def _run(name, run_id="run-1"):
    return tr.execute_tool(
        name, {"x": 1}, "user-1", MagicMock(), run_id=run_id, execution_token={"t": 1}
    )


def test_register_tool_stores_execution_guarantee():
    _register("t_store", "EXACTLY_ONCE", [])
    assert tr.TOOL_REGISTRY["t_store"]["execution_guarantee"] == "EXACTLY_ONCE"
    _register("t_default", None, [])  # explicit call omitting guarantee via helper
    tr.register_tool(name="t_def2", risk="low", description="t", capability="c",
                     required_capability="c", category="test", egress_scope="none")(lambda **k: None)
    assert tr.TOOL_REGISTRY["t_def2"]["execution_guarantee"] == "AT_LEAST_ONCE"


def test_exactly_once_first_call_executes_and_finalizes_success():
    runs = []
    _register("t1", "EXACTLY_ONCE", runs)
    with _mocks(flag=True, resolve_return=(False, None)) as (m_resolve, m_complete):
        result = _run("t1")
    assert result["success"] is True
    assert len(runs) == 1  # executed once
    m_resolve.assert_called_once()
    m_complete.assert_called_once()  # finalized success


def test_exactly_once_replay_returns_cached_without_executing():
    runs = []
    _register("t2", "EXACTLY_ONCE", runs)
    with _mocks(flag=True, resolve_return=(True, {"result": {"echoed": {"x": 1}}})) as (mr, mc):
        result = _run("t2")
    assert result == {
        "success": True,
        "result": {"echoed": {"x": 1}},
        "error": None,
        "idempotent_replay": True,
    }
    assert runs == []  # NOT executed — replayed
    mc.assert_not_called()  # nothing to finalize on replay


def test_flag_off_no_boundary_even_for_exactly_once():
    runs = []
    _register("t3", "EXACTLY_ONCE", runs)
    with _mocks(flag=False) as (m_resolve, m_complete):
        result = _run("t3")
    assert result["success"] is True
    assert len(runs) == 1
    m_resolve.assert_not_called()  # boundary skipped when the global flag is off


def test_at_least_once_tool_never_deduped():
    runs = []
    _register("t4", "AT_LEAST_ONCE", runs)
    with _mocks(flag=True) as (m_resolve, m_complete):
        _run("t4")
    m_resolve.assert_not_called()


def test_no_run_id_no_boundary():
    runs = []
    _register("t5", "EXACTLY_ONCE", runs)
    with _mocks(flag=True) as (m_resolve, m_complete):
        # no run_id / token → boundary cannot key a scope
        result = tr.execute_tool("t5", {"x": 1}, "user-1", MagicMock())
    assert result["success"] is True
    m_resolve.assert_not_called()


def test_ledger_resolve_failure_degrades_to_at_least_once():
    runs = []
    _register("t6", "EXACTLY_ONCE", runs)
    with _mocks(flag=True) as (m_resolve, m_complete):
        m_resolve.side_effect = RuntimeError("db down")
        result = _run("t6")
    assert result["success"] is True
    assert len(runs) == 1  # still executed despite ledger failure
    m_complete.assert_not_called()  # no finalize when we degraded


def test_failed_tool_finalizes_failed():
    def _boom(args, user_id, db):
        raise ValueError("tool blew up")

    tr.register_tool(name="t7", risk="low", description="t", capability="c",
                     required_capability="c", category="test", egress_scope="none",
                     execution_guarantee="EXACTLY_ONCE")(_boom)
    with _mocks(flag=True, resolve_return=(False, None)) as (m_resolve, m_complete):
        result = _run("t7")
    assert result["success"] is False
    m_complete.assert_called_once()
    assert m_complete.call_args[0][2] == "failed"  # finalized as failed
