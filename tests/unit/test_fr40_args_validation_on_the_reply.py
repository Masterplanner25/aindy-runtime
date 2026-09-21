"""FR-40 — on `nodus_vm`, FR-33's `warn` mode gets a witness: the validation tally rides the
worker reply and is recorded in the api process.

`execute_tool` counted `aindy_tool_args_validation_total{tool, outcome, mode}` and logged the
`warn` WARNING in whichever process ran it. On `nodus_vm` that is the pool worker: its registry
never serves `/metrics` (FR-35's shape, one series over) and the pool opens it with
`stderr=DEVNULL`, so the 2.20.0 recipe — leave at `warn`, watch `invalid` read zero, then
`enforce` — could not be followed on the backend the app runs. There was no observable
difference between "every step validated clean" and "validation never ran".

Same mechanism as FR-35 (DEC-040..042): deferral REPLACES observation in the worker; the ledger
is the fifth deferred collection on the reply; the parent counts and re-emits the WARNING.
Each claim has a control: the worker's own counter must NOT move (the double count); a reply
without the key records nothing; the cap drops errors but never counts.
"""
from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

_METRIC = "aindy_tool_args_validation_total"


def _read(name, labels):
    from AINDY.platform_layer.metrics import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


def _register(name, schema):
    from AINDY.agents import tool_registry

    tool_registry.TOOL_REGISTRY[name] = {
        "fn": lambda args, user_id, db: {"ok": True}, "risk": "low", "isolation": None,
        "execution_guarantee": "AT_LEAST_ONCE", "egress_scope": None, "capability": None,
        "args_schema": schema,
    }


# ---------------------------------------------------------------------------
# The ledger and the scope, at the unit that changed
# ---------------------------------------------------------------------------

def test_inside_a_scope_observe_records_and_counts_nothing(monkeypatch, caplog):
    from AINDY.agents import tool_registry as tr

    monkeypatch.setenv(tr.ARGS_VALIDATION_ENV, "warn")
    name = "__fr40__" + uuid.uuid4().hex[:6]
    before = _read(_METRIC, {"tool": name, "outcome": "invalid", "mode": "warn"})
    with caplog.at_level(logging.WARNING, logger="AINDY.agents.tool_registry"):
        with tr.args_validation_deferral_scope() as ledger:
            tr._observe_args_validation(name, "invalid", "warn", ["missing required 'q'"])
            tr._observe_args_validation(name, "valid", "warn")
    assert ledger.as_dict() == {"tools": {name: {
        "valid": 1, "invalid": 1, "mode": "warn", "errors": ["missing required 'q'"], "dropped_errors": 0,
    }}}
    assert _read(_METRIC, {"tool": name, "outcome": "invalid", "mode": "warn"}) == before, "counted in the worker — the double count"
    assert not [r for r in caplog.records if "do not match" in r.getMessage()], "logged in the worker — where nobody reads it"


def test_outside_a_scope_observe_counts_and_warns_as_before(caplog):
    from AINDY.agents import tool_registry as tr

    name = "__fr40__" + uuid.uuid4().hex[:6]
    before = _read(_METRIC, {"tool": name, "outcome": "invalid", "mode": "warn"})
    with caplog.at_level(logging.WARNING, logger="AINDY.agents.tool_registry"):
        tr._observe_args_validation(name, "invalid", "warn", ["bad"])
    assert _read(_METRIC, {"tool": name, "outcome": "invalid", "mode": "warn"}) == before + 1
    assert any("do not match" in r.getMessage() and name in r.getMessage() for r in caplog.records)


def test_the_ledger_caps_errors_but_never_the_count(monkeypatch):
    from AINDY.agents import tool_registry as tr

    monkeypatch.setenv(tr.ARGS_VALIDATION_LEDGER_MAX_ENV, "2")
    with tr.args_validation_deferral_scope() as ledger:
        for i in range(5):
            tr._observe_args_validation("t", "invalid", "warn", [f"e{i}"])
    row = ledger.as_dict()["tools"]["t"]
    assert (row["invalid"], row["errors"], row["dropped_errors"]) == (5, ["e0", "e1"], 3)


# ---------------------------------------------------------------------------
# The worker, for real: run_one defers a tool step's validation onto the reply
# ---------------------------------------------------------------------------

def test_run_one_defers_a_tool_steps_validation_and_the_worker_counter_does_not_move(monkeypatch):
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.agents import tool_registry as tr
    from AINDY.runtime import nodus_worker

    monkeypatch.setenv(tr.ARGS_VALIDATION_ENV, "warn")
    name = "__fr40_tool__" + uuid.uuid4().hex[:8]
    _register(name, {"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}})
    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability",
        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []},
        raising=True,
    )
    invalid = {"tool": name, "outcome": "invalid", "mode": "warn"}
    before = _read(_METRIC, invalid)
    script = (
        'let a = call_tool("' + name + '", {})\n'
        'let b = call_tool("' + name + '", {"q": "x"})\n'
        'set_state("a", a)\n'
    )
    try:
        result = nodus_worker.run_one({
            "script": script,
            "filename": "fr40.nd", "state": {}, "memory_context": {}, "input_payload": {},
            "context": {"user_id": str(uuid.uuid4()), "execution_unit_id": "eu-fr40", "trace_id": "t-fr40",
                        "run_id": "run-fr40", "execution_token": {"sig": "x"}},
        })
    finally:
        tr.TOOL_REGISTRY.pop(name, None)

    ledger = result.get("args_validation")
    assert isinstance(ledger, dict), result
    row = ledger["tools"][name]
    assert (row["valid"], row["invalid"], row["mode"]) == (1, 1, "warn"), row
    assert row["errors"] and "q" in row["errors"][0], row
    assert _read(_METRIC, invalid) == before, "the worker counted the call itself — that is the double count"


def test_a_waiting_reply_still_carries_the_tally():
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker

    result = nodus_worker.run_one({
        "script": 'set_state("nodus_wait_requested", true)\nset_state("nodus_wait_event_type", "x")\n',
        "filename": "wait.nd", "state": {}, "memory_context": {}, "input_payload": {},
        "context": {"user_id": "u", "execution_unit_id": "eu-w", "trace_id": "t"},
    })
    assert result["status"] == "waiting"
    assert result["args_validation"] == {"tools": {}}


# ---------------------------------------------------------------------------
# ★ The parent, for real: run_script records the reply's tally in THIS process
# ---------------------------------------------------------------------------

class _Proc:
    returncode = 0
    stderr = ""

    def __init__(self, reply_extra):
        self.stdout = json.dumps({
            "status": "success", "output_state": {}, "emitted_events": [], "memory_writes": [],
            **reply_extra,
        })


def _run_parent(monkeypatch, reply_extra):
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "0")
    from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext, NodusRuntimeAdapter

    ctx = NodusExecutionContext(user_id=str(uuid.uuid4()), execution_unit_id="eu-parent", run_id="")
    with patch("AINDY.runtime.nodus_runtime_adapter.subprocess.run", lambda *a, **k: _Proc(reply_extra)):
        NodusRuntimeAdapter(MagicMock()).run_script("let x = 1", ctx)


def test_run_script_counts_the_tally_and_re_emits_the_warning_here(monkeypatch, caplog):
    name = "__fr40_parent__" + uuid.uuid4().hex[:6]
    invalid = {"tool": name, "outcome": "invalid", "mode": "warn"}
    valid = {"tool": name, "outcome": "valid", "mode": "warn"}
    b_invalid, b_valid = _read(_METRIC, invalid), _read(_METRIC, valid)
    ledger = {"tools": {name: {"valid": 3, "invalid": 2, "mode": "warn",
                               "errors": ["missing required 'q'", "x: expected string"], "dropped_errors": 1}}}
    with caplog.at_level(logging.WARNING, logger="AINDY.agents.tool_registry"):
        _run_parent(monkeypatch, {"args_validation": ledger})
    assert _read(_METRIC, invalid) == b_invalid + 2
    assert _read(_METRIC, valid) == b_valid + 3
    warnings = [r.getMessage() for r in caplog.records if "do not match" in r.getMessage() and name in r.getMessage()]
    assert len(warnings) == 1, warnings
    assert "nodus_vm worker" in warnings[0] and "+1 more" in warnings[0] and "2 call(s)" in warnings[0]


def test_enforce_mode_tallies_without_the_warning(monkeypatch, caplog):
    """`enforce` failed the step in the worker with a readable reason; the tally still counts,
    and there is no `warn` line to re-emit."""
    name = "__fr40_enf__" + uuid.uuid4().hex[:6]
    before = _read(_METRIC, {"tool": name, "outcome": "invalid", "mode": "enforce"})
    with caplog.at_level(logging.WARNING, logger="AINDY.agents.tool_registry"):
        _run_parent(monkeypatch, {"args_validation": {"tools": {name: {"valid": 0, "invalid": 1, "mode": "enforce", "errors": ["e"], "dropped_errors": 0}}}})
    assert _read(_METRIC, {"tool": name, "outcome": "invalid", "mode": "enforce"}) == before + 1
    assert not [r for r in caplog.records if name in r.getMessage()]


def test_a_reply_without_the_key_records_nothing(monkeypatch):
    """A worker older than this key, or a reply shape without it: nothing counted, nothing raised."""
    from AINDY.platform_layer.metrics import REGISTRY

    def _samples():
        return sum(len(m.samples) for m in REGISTRY.collect() if m.name == "aindy_tool_args_validation")

    before = _samples()
    _run_parent(monkeypatch, {})
    _run_parent(monkeypatch, {"args_validation": None})
    _run_parent(monkeypatch, {"args_validation": "garbage"})
    assert _samples() == before
