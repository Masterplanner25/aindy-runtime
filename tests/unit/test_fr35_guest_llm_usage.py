"""FR-35 — guest-path LLM usage: metered where it is spent, recorded where it is owned.

Design: ``docs/design/FR35_GUEST_LLM_USAGE_DESIGN.md`` §9. On `nodus_vm` a tool step's LLM call
runs the provider client in the WORKER; its usage rides the worker reply as a fourth deferred
collection and is recorded in the parent under an explicit subject.

★ The standing rule first: ASSERT THE MECHANISM. The worker-side test runs the REAL `run_one`
in-process with a tool whose stub LLM client returns a usage-bearing response, and asserts that
(a) the reply carries the record and (b) the worker-side counters did NOT move — deferral
replaces observation. A test that only read the reply could not tell "deferred" from "counted
twice", and the double-count is the failure the meter's own design rejects.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from AINDY.platform_layer import token_meter as tm

pytestmark = pytest.mark.runtime_only


class _Usage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


class _Resp:
    model = "probe-model"

    def __init__(self, p, c):
        self.usage = _Usage(p, c)


def _read(name, labels):
    from AINDY.platform_layer.metrics import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


# ---------------------------------------------------------------------------
# The ledger and the deferral scope
# ---------------------------------------------------------------------------

def test_inside_a_deferral_scope_observe_appends_and_counts_nothing():
    labels = {"provider": "deferred-probe", "model": "probe-model", "kind": "prompt"}
    before = _read("aindy_llm_tokens_total", labels)
    with tm.llm_usage_deferral_scope() as ledger:
        with tm.llm_usage_tool_scope("arm.analyze"):
            tm.observe_llm_usage(provider="deferred-probe", model="probe-model", response=_Resp(1201, 764),
                                 started_at_ms=1_700_000_000_000, duration_ms=15_200)
    assert len(ledger) == 1
    rec = ledger.records[0]
    assert (rec.provider, rec.model, rec.prompt_tokens, rec.completion_tokens) == ("deferred-probe", "probe-model", 1201, 764)
    assert (rec.tool, rec.started_at_ms, rec.duration_ms, rec.outcome) == ("arm.analyze", 1_700_000_000_000, 15_200, "ok")
    assert _read("aindy_llm_tokens_total", labels) == before, "deferral must REPLACE observation, not add to it"


def test_outside_a_scope_observe_counts_as_before():
    """Liveness control for the test above — the same call outside the scope moves the counter."""
    labels = {"provider": "deferred-probe", "model": "probe-model", "kind": "prompt"}
    before = _read("aindy_llm_tokens_total", labels)
    tm.observe_llm_usage(provider="deferred-probe", model="probe-model", response=_Resp(5, 1))
    assert _read("aindy_llm_tokens_total", labels) == before + 5


def test_the_ledger_is_bounded_and_aggregates_the_tail():
    with tm.llm_usage_deferral_scope(max_records=2) as ledger:
        for _ in range(5):
            tm.observe_llm_usage(provider="p", model="m", response=_Resp(10, 2))
    d = ledger.as_dict()
    assert len(d["records"]) == 2
    assert d["tail"] == [{"provider": "p", "model": "m", "calls": 3, "prompt_tokens": 30, "completion_tokens": 6}]
    assert len(ledger) == 5


def test_an_unreadable_response_is_counted_on_the_ledger_not_dropped_silently():
    with tm.llm_usage_deferral_scope() as ledger:
        tm.observe_llm_usage(provider="p", model="m", response=object())
    assert ledger.as_dict()["unreadable"] == 1 and len(ledger) == 0


def test_ledger_cap_reads_the_env(monkeypatch):
    monkeypatch.setenv(tm.LLM_LEDGER_MAX_ENV, "7")
    assert tm.ledger_max_records() == 7
    monkeypatch.setenv(tm.LLM_LEDGER_MAX_ENV, "nope")
    assert tm.ledger_max_records() == tm.DEFAULT_LLM_LEDGER_MAX


# ---------------------------------------------------------------------------
# ★ The worker, for real: run_one in-process, a tool whose LLM call is metered
# ---------------------------------------------------------------------------

def _register_llm_tool(name, seen: dict):
    from AINDY.agents import tool_registry
    from AINDY.platform_layer.genai_telemetry import llm_operation

    def _impl(args, user_id, db):
        seen["attribution"] = tm.current_llm_attribution()  # what the governor's reserve would resolve
        # what a real provider client does since #706: the span brackets the call, record() meters
        with llm_operation(provider="worker-probe", model="probe-model") as op:
            response = _Resp(1201, 764)
            op.record(response)
        return {"analysed": True}

    tool_registry.TOOL_REGISTRY[name] = {
        "fn": _impl, "risk": "low", "isolation": None, "execution_guarantee": "AT_LEAST_ONCE",
        "egress_scope": None, "capability": None, "args_schema": None,
    }


def test_run_one_defers_a_tool_steps_usage_and_the_worker_counters_do_not_move(monkeypatch):
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.agents import tool_registry
    from AINDY.runtime import nodus_worker

    name = "__fr35_tool__" + uuid.uuid4().hex[:8]
    seen: dict = {}
    _register_llm_tool(name, seen)
    # the seam's authority check is the parent's business (it needs a minted token); in-process
    # here we grant it so the tool actually runs — what is under test is the ledger, not the gate
    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability",
        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []},
        raising=True,
    )
    labels = {"provider": "worker-probe", "model": "probe-model", "kind": "prompt"}
    before = _read("aindy_llm_tokens_total", labels)
    tenant = str(uuid.uuid4())
    run_id = "run-" + uuid.uuid4().hex[:8]
    try:
        result = nodus_worker.run_one({
            "script": f'let r = call_tool("{name}", {{}})\nset_state("r", r)\n',
            "filename": "fr35.nd",
            "state": {}, "memory_context": {}, "input_payload": {},
            "context": {"user_id": tenant, "execution_unit_id": "eu-fr35", "trace_id": "t-fr35",
                        "run_id": run_id, "execution_token": {"sig": "x"}},
        })
    finally:
        tool_registry.TOOL_REGISTRY.pop(name, None)

    ledger = result.get("llm_usage")
    assert isinstance(ledger, dict), result
    assert len(ledger["records"]) == 1, result
    rec = ledger["records"][0]
    assert (rec["provider"], rec["prompt_tokens"], rec["completion_tokens"]) == ("worker-probe", 1201, 764)
    assert rec["tool"] == name
    assert rec["started_at_ms"] > 0
    # (b) the mechanism: NOTHING was observed in this process
    assert _read("aindy_llm_tokens_total", labels) == before, "the worker counted the call itself — that is the double count"
    # (c) the attribution scope was forwarded, so the governor's reserve can name a subject here
    assert seen["attribution"] == (tenant, run_id), seen


def test_a_waiting_reply_still_carries_the_ledger():
    """A segment that parks still spent — the ledger rides every reply shape."""
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker

    result = nodus_worker.run_one({
        "script": 'set_state("nodus_wait_requested", true)\nset_state("nodus_wait_event_type", "x")\n',
        "filename": "wait.nd", "state": {}, "memory_context": {}, "input_payload": {},
        "context": {"user_id": "u", "execution_unit_id": "eu-w", "trace_id": "t"},
    })
    assert result["status"] == "waiting"
    assert result["llm_usage"] == {"records": [], "tail": [], "unreadable": 0}


# ---------------------------------------------------------------------------
# ★ The parent, for real: run_script applies the reply's ledger under an explicit subject
# ---------------------------------------------------------------------------

class _Proc:
    returncode = 0
    stderr = ""

    def __init__(self, ledger):
        self.stdout = json.dumps({
            "status": "success", "output_state": {}, "emitted_events": [], "memory_writes": [],
            "llm_usage": ledger,
        })


def _run_parent(monkeypatch, ledger, *, run_id="", unit="eu-parent", tenant=None):
    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "0")
    from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext, NodusRuntimeAdapter

    tenant = tenant or str(uuid.uuid4())
    ctx = NodusExecutionContext(user_id=tenant, execution_unit_id=unit, run_id=run_id)
    with patch("AINDY.runtime.nodus_runtime_adapter.subprocess.run", lambda *a, **k: _Proc(ledger)):
        NodusRuntimeAdapter(MagicMock()).run_script("let x = 1", ctx)
    return tenant


def test_run_script_records_the_ledger_once_under_the_reply_subject(monkeypatch):
    from AINDY.kernel.resource_manager import get_resource_manager

    run_id = "run-" + uuid.uuid4().hex[:8]
    ledger = {"records": [{"provider": "parent-probe", "model": "probe-model", "prompt_tokens": 1201,
                           "completion_tokens": 764, "started_at_ms": 1_700_000_000_000, "duration_ms": 15_200,
                           "tool": "arm.analyze", "outcome": "ok"}],
              "tail": [{"provider": "parent-probe", "model": "probe-model", "calls": 2, "prompt_tokens": 10, "completion_tokens": 4}],
              "unreadable": 0}
    labels = {"provider": "parent-probe", "model": "probe-model", "kind": "prompt"}
    before = _read("aindy_llm_tokens_total", labels)
    calls_before = _read("aindy_llm_calls_total", {"provider": "parent-probe", "attributed": "run"})

    tenant = _run_parent(monkeypatch, ledger, run_id=run_id)

    assert _read("aindy_llm_tokens_total", labels) == before + 1201 + 10     # records + tail, exactly once
    assert _read("aindy_llm_calls_total", {"provider": "parent-probe", "attributed": "run"}) == calls_before + 2
    rm = get_resource_manager()
    assert int(rm.get_usage(run_id).get("tokens") or 0) == 1201 + 764 + 10 + 4
    assert int(rm.get_tenant_tokens(tenant) or 0) >= 1201 + 764 + 10 + 4


def test_without_a_run_the_usage_accrues_to_the_unit(monkeypatch):
    """A `sys.v1.nodus.execute` script with no agent run still accrues to its unit and tenant."""
    from AINDY.kernel.resource_manager import get_resource_manager

    unit = "eu-" + uuid.uuid4().hex[:8]
    ledger = {"records": [{"provider": "unit-probe", "model": "m", "prompt_tokens": 3, "completion_tokens": 2}], "tail": [], "unreadable": 0}
    calls_before = _read("aindy_llm_calls_total", {"provider": "unit-probe", "attributed": "unit"})
    _run_parent(monkeypatch, ledger, run_id="", unit=unit)
    assert _read("aindy_llm_calls_total", {"provider": "unit-probe", "attributed": "unit"}) == calls_before + 1
    assert int(get_resource_manager().get_usage(unit).get("tokens") or 0) == 5


def test_a_reply_without_a_ledger_records_nothing(monkeypatch):
    labels = {"provider": "absent-probe", "model": "m", "kind": "prompt"}
    before = _read("aindy_llm_tokens_total", labels)
    _run_parent(monkeypatch, None)
    assert _read("aindy_llm_tokens_total", labels) == before


def test_the_same_ledger_applied_twice_counts_twice():
    """Two segments' spend is two segments' spend — usage is not an effect and is never deduped."""
    from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext, _apply_deferred_llm_usage

    ctx = NodusExecutionContext(user_id=str(uuid.uuid4()), execution_unit_id="eu-twice")
    ledger = {"records": [{"provider": "twice-probe", "model": "m", "prompt_tokens": 7, "completion_tokens": 1}], "tail": [], "unreadable": 0}
    labels = {"provider": "twice-probe", "model": "m", "kind": "prompt"}
    before = _read("aindy_llm_tokens_total", labels)
    assert _apply_deferred_llm_usage(ledger, ctx) == 1
    assert _apply_deferred_llm_usage(ledger, ctx) == 1
    assert _read("aindy_llm_tokens_total", labels) == before + 14


# ---------------------------------------------------------------------------
# #706's gap — the replayed span, with the worker's timestamps, under the current span
# ---------------------------------------------------------------------------

@pytest.fixture
def spans():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exp = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        trace.set_tracer_provider(TracerProvider())
        provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        pytest.skip("no SDK tracer provider")
    provider.add_span_processor(SimpleSpanProcessor(exp))
    yield exp
    exp.clear()


def test_the_parent_replays_the_chat_span_with_the_workers_timestamps(spans):
    from AINDY.platform_layer import genai_telemetry as gt
    from AINDY.runtime.nodus_runtime_adapter import NodusExecutionContext, _apply_deferred_llm_usage

    started = 1_700_000_000_000
    rec = {"provider": "span-probe", "model": "replayed-model", "prompt_tokens": 9, "completion_tokens": 4,
           "started_at_ms": started, "duration_ms": 2_500, "tool": "arm.analyze", "outcome": "ok"}
    with gt.agent_operation(agent_name="probe", run_id="run-span"):
        _apply_deferred_llm_usage({"records": [rec], "tail": [], "unreadable": 0},
                                  NodusExecutionContext(user_id="u", execution_unit_id="eu", run_id="run-span"))
    agent = [s for s in spans.get_finished_spans() if s.name.startswith("invoke_agent")][0]
    chat = [s for s in spans.get_finished_spans() if s.name == "chat replayed-model"]
    assert len(chat) == 1
    span = chat[0]
    assert span.parent.span_id == agent.context.span_id
    assert span.start_time == started * 1_000_000
    assert span.end_time == (started + 2_500) * 1_000_000
    a = dict(span.attributes)
    assert a["aindy.deferred"] is True
    assert a[gt.ATTR_TOOL_NAME] == "arm.analyze"
    assert (a[gt.ATTR_USAGE_INPUT_TOKENS], a[gt.ATTR_USAGE_OUTPUT_TOKENS]) == (9, 4)
    assert a[gt.ATTR_PROVIDER_NAME] == "span-probe"


# ---------------------------------------------------------------------------
# The agent_flow backend is untouched
# ---------------------------------------------------------------------------

def test_no_deferral_scope_is_active_in_process():
    assert tm.current_llm_usage_ledger() is None


# ---------------------------------------------------------------------------
# ★ Admission in the worker: the governor's reserve names the FORWARDED subject and can refuse
# ---------------------------------------------------------------------------

def test_the_governor_refuses_inside_the_worker_under_the_forwarded_tenant(monkeypatch):
    """§3 of the design: the reserve must run where the call is made. With the attribution scope
    forwarded, `resolve_llm_subject()` answers in the worker and a refusal reaches the step as a
    typed `transient` failure — before the call, before any ledger entry."""
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.agents import tool_registry
    from AINDY.platform_layer import llm_budget
    from AINDY.platform_layer.llm_client import CircuitBreakerLLMClient, LLMBudgetExceededError
    from AINDY.runtime import nodus_worker

    reserved: dict = {}

    class _RM:
        def effective_limit(self, unit_key, dimension):
            return 0

        def reserve_tenant_tokens(self, tenant_id, count, cap):
            reserved["tenant"] = tenant_id
            reserved["count"] = count
            return False  # over the window

        def get_tenant_tokens(self, tenant_id):
            return 999_999

    monkeypatch.setattr("AINDY.kernel.resource_manager.get_resource_manager", lambda: _RM(), raising=True)
    monkeypatch.setattr(llm_budget, "_tenant_cap", lambda: 8000, raising=True)
    monkeypatch.setattr(llm_budget, "_may_fail_open", lambda: False, raising=True)
    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability",
        lambda **kw: {"ok": True, "error": None, "granted_tools": [], "allowed_capabilities": []},
        raising=True,
    )

    class _Inner:
        def chat(self, messages, model=None, temperature=0.7, max_tokens=None):
            raise AssertionError("the provider was called — the reserve did not refuse")

        def is_available(self):
            return True

    from AINDY.platform_layer.llm_client import LLMClient
    LLMClient.register(_Inner) if hasattr(LLMClient, "register") else None
    client = CircuitBreakerLLMClient(_Inner(), provider="worker-probe")

    def _impl(args, user_id, db):
        client.chat([{"role": "user", "content": "hi"}], model="m", max_tokens=4000)
        return {"unreachable": True}

    name = "__fr35_budget__" + uuid.uuid4().hex[:8]
    tool_registry.TOOL_REGISTRY[name] = {"fn": _impl, "risk": "low", "isolation": None,
                                         "execution_guarantee": "AT_LEAST_ONCE", "egress_scope": None,
                                         "capability": None, "args_schema": None}
    tenant = str(uuid.uuid4())
    try:
        result = nodus_worker.run_one({
            "script": f'let r = call_tool("{name}", {{}})\nset_state("r", r)\n',
            "filename": "budget.nd", "state": {}, "memory_context": {}, "input_payload": {},
            "context": {"user_id": tenant, "execution_unit_id": "eu-budget", "trace_id": "t",
                        "run_id": "run-budget", "execution_token": {"sig": "x"}},
        })
    finally:
        tool_registry.TOOL_REGISTRY.pop(name, None)

    assert reserved.get("tenant") == tenant, f"the reserve did not see the forwarded tenant: {reserved}"
    step = result["output_state"]["r"]
    assert step["success"] is False
    assert step["failure_class"] == LLMBudgetExceededError.failure_class == "transient", step
    assert "llm token budget" in step["error"]
    assert result["llm_usage"]["records"] == []  # refused before the call: nothing to record
