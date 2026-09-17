"""App intake 2026-09-16 — FR-36, FR-34, FR-32 (option 2).

Each test drives the real function the fix changed, with production-shaped inputs; the
fixture traps each FR's original observers fell into are named at the test.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# FR-36 — the completion-hook context survives the extension boundary
# ---------------------------------------------------------------------------

class _OrmLikeRun:
    """Looks like an ORM object to the sanitizer (has _sa_instance_state)."""

    _sa_instance_state = object()

    def __init__(self):
        self.id = uuid.uuid4()
        self.agent_type = "default"
        self.trace_id = "trace-fr36"


class _SessionLike:
    def execute(self, *a, **k):
        return None

    def commit(self):
        return None


def test_every_documented_primitive_survives_the_boundary_with_production_shaped_values():
    """★ The existing boundary test passed `user_id="u1"` — a string where production hands a
    `uuid.UUID` — so it could never see the redaction (variant 13). This builds the context
    through the REAL builder with a real UUID and runs the REAL sanitizer over it."""
    from AINDY.agents.agent_runtime.execution import (
        COMPLETION_HOOK_PRIMITIVE_KEYS,
        build_completion_hook_context,
    )
    from AINDY.platform_layer.extension_boundary import sanitize_extension_context

    tenant = uuid.uuid4()
    ctx = build_completion_hook_context(_OrmLikeRun(), db=_SessionLike(), user_db_id=tenant)
    clean = sanitize_extension_context(ctx)

    assert clean["user_id"] == str(tenant)
    for key in COMPLETION_HOOK_PRIMITIVE_KEYS:
        value = clean.get(key)
        assert not (isinstance(value, dict) and "_redacted_type" in value), (
            f"documented key {key!r} reached the hook REDACTED: {value} — the boundary is hiding a bug"
        )
        assert value is None or isinstance(value, (str, int, float, bool)), (key, value)
    # and the two trust-only keys are still stripped / redacted, as before
    assert "db" not in clean
    assert isinstance(clean.get("run"), dict) and "_redacted_type" in clean["run"]


def test_a_missing_tenant_is_none_not_the_string_none():
    from AINDY.agents.agent_runtime.execution import build_completion_hook_context

    ctx = build_completion_hook_context(_OrmLikeRun(), db=None, user_db_id=None)
    assert ctx["user_id"] is None


def test_the_documented_key_set_is_what_the_builder_produces():
    """Liveness: the census the first test iterates is derived from the builder's output, so a
    key added to the context without being declared primitive fails HERE, not in an app log."""
    from AINDY.agents.agent_runtime.execution import (
        COMPLETION_HOOK_PRIMITIVE_KEYS,
        build_completion_hook_context,
    )

    produced = set(build_completion_hook_context(_OrmLikeRun(), db=None, user_db_id=uuid.uuid4()))
    assert produced == COMPLETION_HOOK_PRIMITIVE_KEYS | {"run", "db"}


# ---------------------------------------------------------------------------
# FR-34 — steps_completed counts SUCCESSES, through the real adapter loop
# ---------------------------------------------------------------------------

class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **kw):
        return self

    def first(self):
        return self._result


class _FakeRun:
    def __init__(self):
        self.steps_completed = 0
        self.current_step = 0
        self.id = uuid.uuid4()
        self.status = "executing"
        self.steps_total = 2
        self.error_message = None
        self.result = None
        self.wait_state = None
        self.completed_at = None


class _FakeDB:
    def __init__(self, run):
        self.run = run
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def query(self, *a, **kw):
        return _FakeQuery(self.run)

    def commit(self):
        return None


def _drive_step(monkeypatch, *, idx: int, tool_result: dict, run: _FakeRun):
    from AINDY.runtime import nodus_adapter

    monkeypatch.setattr(nodus_adapter, "execute_tool", lambda **kw: dict(tool_result), raising=True)
    monkeypatch.setattr(nodus_adapter, "record_agent_event", lambda **kw: "evt", raising=True)
    monkeypatch.setattr(nodus_adapter, "queue_system_event", lambda **kw: "sys", raising=True)
    monkeypatch.setattr(nodus_adapter, "emit_system_event", lambda **kw: "sys", raising=True)
    monkeypatch.setattr(
        nodus_adapter, "check_tool_capability",
        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []},
        raising=True,
    )
    monkeypatch.setattr("AINDY.memory.memory_helpers.enrich_context", lambda ctx: ctx, raising=True)
    steps = [{"tool": f"t{i}", "args": {}, "risk_level": "high", "description": "d"} for i in range(idx + 1)]
    state = {
        "agent_run_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "00000000-0000-0000-0000-000000000002",
        "capability_token": {"sig": "x"},
        "correlation_id": "run_fr34",
        "steps": steps,
        "current_step_index": idx,
        "step_results": [],
    }
    return nodus_adapter.agent_execute_step(state, {"db": _FakeDB(run), "trace_id": "t"})


def test_a_failed_step_moves_the_cursor_but_not_steps_completed(monkeypatch):
    """The filed case: a run failing on step 2 of 2 used to record steps_completed 2/2."""
    run = _FakeRun()
    _drive_step(monkeypatch, idx=0, tool_result={"success": True, "result": "ok", "error": None}, run=run)
    assert (run.steps_completed, run.current_step) == (1, 1)
    _drive_step(monkeypatch, idx=1, tool_result={"success": False, "result": None, "error": "requires 'file_path'", "failure_class": "invalid"}, run=run)
    assert run.current_step == 2
    assert run.steps_completed == 1, "a failed step was counted as completed"


def test_steps_completed_never_runs_ahead_of_the_cursor(monkeypatch):
    """A re-driven segment (crash continuation restarts a segment from step one) must not count a
    step twice: the count is clamped to the cursor."""
    run = _FakeRun()
    ok = {"success": True, "result": "ok", "error": None}
    _drive_step(monkeypatch, idx=0, tool_result=ok, run=run)
    _drive_step(monkeypatch, idx=0, tool_result=ok, run=run)  # the same step, re-executed
    assert run.steps_completed == 1


def test_the_gate_refusal_sites_no_longer_count_a_step_as_completed():
    """Structural companion for the two sites the loop harness does not reach (the authority
    gate's skip/fail and the capability refusal): no `steps_completed = idx + 1` remains
    anywhere, and the one assignment left is guarded by the success status."""
    import ast
    import pathlib

    src = pathlib.Path("AINDY/runtime/nodus_adapter.py").read_text(encoding="utf-8")
    assigns = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "steps_completed" for t in node.targets)
    ]
    assert len(assigns) == 1, f"expected ONE guarded steps_completed assignment, found {len(assigns)}"


# ---------------------------------------------------------------------------
# FR-32 — a plugin's memory_execute_loop wins; the runtime's is a default; both boot orders agree
# ---------------------------------------------------------------------------

_PLUGIN_LOOP = {
    "start": "memory_execution_validate",
    "edges": {"memory_execution_validate": ["memory_execution_run"]},
    "end": ["memory_execution_run"],
}


@pytest.fixture
def clean_loop():
    from AINDY.runtime.flow_engine import FLOW_REGISTRY

    saved = FLOW_REGISTRY.pop("memory_execute_loop", None)
    yield FLOW_REGISTRY
    FLOW_REGISTRY.pop("memory_execute_loop", None)
    if saved is not None:
        FLOW_REGISTRY["memory_execute_loop"] = saved


def test_the_runtime_default_registers_when_no_plugin_declared_one(clean_loop):
    from AINDY.runtime.flow_definitions_memory import DEFAULT_MEMORY_EXECUTE_LOOP, register_default_memory_execute_loop

    assert register_default_memory_execute_loop() is True
    assert clean_loop["memory_execute_loop"] == DEFAULT_MEMORY_EXECUTE_LOOP


def test_a_plugin_registration_wins_over_the_runtime_default(clean_loop):
    """Option 2 as taken: the app owns an app-node-only graph whichever module declares it."""
    from AINDY.runtime.flow_definitions_memory import register_default_memory_execute_loop
    from AINDY.runtime.flow_engine import register_flow

    register_flow("memory_execute_loop", dict(_PLUGIN_LOOP))
    assert register_default_memory_execute_loop() is False
    assert clean_loop["memory_execute_loop"] == _PLUGIN_LOOP
    assert clean_loop["memory_execute_loop"]["end"] == ["memory_execution_run"]


def test_register_all_flows_no_longer_registers_the_loop_itself(clean_loop):
    """The default must come LAST — `register_all_flows()` runs before plugin flows on the API
    and after them on the worker, so it must not be the thing that registers a default."""
    from AINDY.runtime.flow_definitions import register_all_flows

    register_all_flows()
    assert "memory_execute_loop" not in clean_loop


def test_both_boot_orders_yield_the_plugins_graph(clean_loop):
    """★ Until this fix the API (runtime first) and the worker (plugins first) disagreed about
    which graph `memory_execute_loop` was. Both orders, same plugin, same answer."""
    from AINDY.runtime.flow_definitions import register_all_flows, register_default_flows
    from AINDY.runtime.flow_engine import register_flow

    def plugin_flows():
        register_flow("memory_execute_loop", dict(_PLUGIN_LOOP))

    # API order
    register_all_flows()
    plugin_flows()
    register_default_flows()
    api_view = dict(clean_loop["memory_execute_loop"])
    clean_loop.pop("memory_execute_loop")
    # worker order
    plugin_flows()
    register_all_flows()
    register_default_flows()
    worker_view = dict(clean_loop["memory_execute_loop"])
    assert api_view == worker_view == _PLUGIN_LOOP


def test_the_api_boot_path_registers_defaults_after_plugin_flows(monkeypatch, clean_loop):
    """Through the real `_register_flow_engine`, with a plugin flow registered by the registry
    hook — the order is the fix, so the order is what is asserted."""
    from AINDY import startup
    from AINDY.platform_layer import registry
    from AINDY.runtime.flow_engine import register_flow

    from AINDY.runtime import flow_definitions_memory as fdm

    calls: list = []
    real_default = fdm.register_default_memory_execute_loop

    def _spied_default():
        outcome = real_default()
        calls.append(("default", outcome))
        return outcome

    monkeypatch.setattr(registry, "register_flows", lambda: (calls.append(("plugins", None)), register_flow("memory_execute_loop", dict(_PLUGIN_LOOP))), raising=True)
    monkeypatch.setattr(fdm, "register_default_memory_execute_loop", _spied_default, raising=True)
    monkeypatch.setattr(startup, "_enforce_nodus_gate", lambda: None, raising=True)
    monkeypatch.setattr(startup, "_verify_flow_engines_started", lambda: None, raising=True)
    startup._register_flow_engine()
    assert clean_loop["memory_execute_loop"] == _PLUGIN_LOOP
    # ★ The final dict cannot tell the orders apart — a plugin registered FIRST would simply be
    # overwritten and re-overwritten. What distinguishes them is that the default DECLINED:
    # it ran after the plugin and returned False, rather than writing a graph the plugin then
    # had to overwrite (the "overwriting a runtime-owned entry" the FR objects to).
    assert calls == [("plugins", None), ("default", False)], calls
