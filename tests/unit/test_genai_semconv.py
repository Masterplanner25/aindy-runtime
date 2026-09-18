"""`OTEL-GENAI-SEMCONV-1` — GenAI semantic-convention spans at the three seams, additively.

Design: ``docs/design/OTEL_GENAI_SEMCONV_DESIGN.md`` §3 (the helper), §5 (the one rename), §9.

★ These drive the REAL entry points against an in-memory span exporter — a real `OpenAILLMClient`
call on a stub transport, a real `execute_tool` on a registered tool — not a patched
`observe_llm_usage` (green-check variant 13: a fixture that stubs the meter cannot see that the
span carried its numbers). A mutation that removes `op.record(response)` from one client turns the
census guard in `test_token_meter.py` red, not just the assertions here.
"""
from __future__ import annotations

import uuid

import pytest

from AINDY.platform_layer import genai_telemetry as gt

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# An in-memory exporter on whatever tracer provider the process has
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def exporter():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exp = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
        provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        pytest.skip("could not install an SDK TracerProvider in this process")
    provider.add_span_processor(SimpleSpanProcessor(exp))
    return exp


@pytest.fixture
def spans(exporter):
    exporter.clear()
    yield exporter
    exporter.clear()


def _by_name(exporter, prefix):
    return [s for s in exporter.get_finished_spans() if s.name.startswith(prefix)]


# ---------------------------------------------------------------------------
# The keys come from the pinned package (design §2) — including the renamed one
# ---------------------------------------------------------------------------

def test_attribute_keys_are_read_from_the_semconv_package_not_typed():
    assert gt.SEMCONV_AVAILABLE, "opentelemetry-semantic-conventions is pinned; the keys must come from it"
    assert gt.ATTR_PROVIDER_NAME == "gen_ai.provider.name"       # the upstream rename, not gen_ai.system
    assert gt.ATTR_OPERATION_NAME == "gen_ai.operation.name"
    assert gt.ATTR_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
    assert gt.ATTR_USAGE_OUTPUT_TOKENS == "gen_ai.usage.output_tokens"
    assert (gt.OP_CHAT, gt.OP_EXECUTE_TOOL, gt.OP_INVOKE_AGENT) == ("chat", "execute_tool", "invoke_agent")


# ---------------------------------------------------------------------------
# The LLM seam — a real client, a stub transport
# ---------------------------------------------------------------------------

class _Usage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _Msg:
    content = "ok"


class _Choice:
    message = _Msg()
    finish_reason = "stop"


class _ChatResponse:
    model = "probe-model-2026"
    choices = [_Choice()]

    def __init__(self, usage):
        self.usage = usage


def _stub_openai_client(response=None, raise_exc=None):
    from AINDY.platform_layer.openai_client import OpenAILLMClient

    client = OpenAILLMClient.__new__(OpenAILLMClient)

    class _Completions:
        def create(self, **kw):
            if raise_exc is not None:
                raise raise_exc
            return response

    class _Chat:
        completions = _Completions()

    class _SDK:
        chat = _Chat()

    object.__setattr__(client, "_client", _SDK())
    object.__setattr__(client, "_chat_timeout", 30.0)
    return client


def test_a_real_chat_call_emits_the_semconv_span_and_meters_exactly_once(spans):
    from AINDY.platform_layer.metrics import REGISTRY
    from AINDY.platform_layer.token_meter import extract_token_usage

    response = _ChatResponse(_Usage(7, 3))
    labels = {"provider": "openai", "model": "semconv-probe", "kind": "prompt"}
    before = REGISTRY.get_sample_value("aindy_llm_tokens_total", labels) or 0.0

    _stub_openai_client(response).chat(model="semconv-probe", messages=[{"role": "user", "content": "hi"}])

    found = _by_name(spans, "chat semconv-probe")
    assert len(found) == 1, [s.name for s in spans.get_finished_spans()]
    span = found[0]
    a = dict(span.attributes)
    assert a[gt.ATTR_OPERATION_NAME] == "chat"
    assert a[gt.ATTR_PROVIDER_NAME] == "openai"
    assert a[gt.ATTR_REQUEST_MODEL] == "semconv-probe"
    assert (a[gt.ATTR_USAGE_INPUT_TOKENS], a[gt.ATTR_USAGE_OUTPUT_TOKENS]) == extract_token_usage(response)
    assert a[gt.ATTR_RESPONSE_MODEL] == "probe-model-2026"
    assert tuple(a[gt.ATTR_RESPONSE_FINISH_REASONS]) == ("stop",)
    # the meter ran once — inside the span, not beside it
    assert REGISTRY.get_sample_value("aindy_llm_tokens_total", labels) == before + 7


def test_a_failed_call_marks_the_span_and_re_raises(spans):
    from opentelemetry.trace import StatusCode

    from AINDY.platform_layer.llm_client import LLMCallError

    client = _stub_openai_client(raise_exc=RuntimeError("upstream 503"))
    with pytest.raises(LLMCallError):
        client.chat_completion_response(model="failing-probe", messages=[])
    span = _by_name(spans, "chat failing-probe")[0]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes[gt.ATTR_ERROR_TYPE] == "RuntimeError"
    assert gt.ATTR_USAGE_INPUT_TOKENS not in span.attributes


def test_no_span_carries_prompt_or_completion_content(spans):
    """Content capture is OUT (design §6) — a guard against it creeping in."""
    _stub_openai_client(_ChatResponse(_Usage(1, 1))).chat(model="content-probe", messages=[{"role": "user", "content": "SECRET"}])
    for span in spans.get_finished_spans():
        for key, value in span.attributes.items():
            assert not key.startswith(("gen_ai.input", "gen_ai.output", "gen_ai.prompt", "gen_ai.completion")), key
            assert "SECRET" not in str(value), (key, value)


# ---------------------------------------------------------------------------
# The tool seam — a real execute_tool
# ---------------------------------------------------------------------------

def test_execute_tool_emits_an_execute_tool_span_around_the_real_invocation(spans):
    from AINDY.agents import tool_registry

    name = "__semconv_tool__" + uuid.uuid4().hex[:8]
    tool_registry.TOOL_REGISTRY[name] = {
        "fn": lambda args, user_id, db: {"ok": True}, "risk": "low", "isolation": None,
        "execution_guarantee": "AT_LEAST_ONCE", "egress_scope": None, "capability": None,
    }
    try:
        res = tool_registry.execute_tool(name, {}, str(uuid.uuid4()), None)
    finally:
        tool_registry.TOOL_REGISTRY.pop(name, None)
    assert res["success"] is True, res
    found = _by_name(spans, f"execute_tool {name}")
    assert len(found) == 1
    a = dict(found[0].attributes)
    assert a[gt.ATTR_OPERATION_NAME] == "execute_tool"
    assert a[gt.ATTR_TOOL_NAME] == name
    assert a["aindy.success"] is True


def test_a_refused_tool_gets_no_span(spans):
    """A refusal is an error envelope the caller already sees; the span is for the invocation."""
    from AINDY.agents import tool_registry

    tool_registry.execute_tool("no-such-tool-" + uuid.uuid4().hex, {}, str(uuid.uuid4()), None)
    assert _by_name(spans, "execute_tool ") == []


# ---------------------------------------------------------------------------
# Nesting and the agent seam
# ---------------------------------------------------------------------------

def test_llm_and_tool_spans_nest_under_the_agent_span(spans):
    with gt.agent_operation(agent_name="probe-agent", run_id="run-1", user_id="u-1"):
        with gt.llm_operation(provider="p", model="m") as op:
            op.record(_ChatResponse(_Usage(1, 1)))
        with gt.tool_operation(tool_name="t", run_id="run-1") as ts:
            ts.outcome({"success": True})
    agent = _by_name(spans, "invoke_agent probe-agent")[0]
    llm = _by_name(spans, "chat m")[0]
    tool = _by_name(spans, "execute_tool t")[0]
    assert llm.parent.span_id == agent.context.span_id
    assert tool.parent.span_id == agent.context.span_id
    a = dict(agent.attributes)
    assert a[gt.ATTR_OPERATION_NAME] == "invoke_agent"
    assert a[gt.ATTR_AGENT_NAME] == "probe-agent"
    assert a[gt.ATTR_CONVERSATION_ID] == "run-1"
    assert a[gt.ATTR_ENDUSER_ID] == "u-1"


def test_llm_span_inherits_attribution_from_the_scope(spans):
    from AINDY.platform_layer.token_meter import llm_attribution_scope

    with llm_attribution_scope(tenant_id="tenant-9", run_id="run-9"):
        with gt.llm_operation(provider="p", model="scoped") as op:
            op.record(_ChatResponse(_Usage(1, 1)))
    a = dict(_by_name(spans, "chat scoped")[0].attributes)
    assert a[gt.ATTR_ENDUSER_ID] == "tenant-9"
    assert a[gt.ATTR_CONVERSATION_ID] == "run-9"


# ---------------------------------------------------------------------------
# The one genuine rename (design §5): enduser.id beside user.id for one release (2.20.0),
# then alone (2.21.0, DEC-036)
# ---------------------------------------------------------------------------

def test_syscall_span_carries_enduser_id_and_no_longer_user_id(spans):
    """Through the real dispatcher on a throwaway syscall — a refusal returns before the span
    opens, so the syscall must actually run for the span to exist."""
    import AINDY.kernel.syscall_dispatcher as syscall_dispatcher
    import AINDY.kernel.syscall_registry as syscall_registry

    name = "sys.v1.test.semconv_probe"
    syscall_registry.SYSCALL_REGISTRY[name] = syscall_registry.SyscallEntry(
        handler=lambda payload, context: {"ok": True}, capability="test.capability",
    )
    dispatcher = syscall_dispatcher.SyscallDispatcher()
    dispatcher._emit_syscall_event = lambda *a, **k: None
    ctx = syscall_registry.SyscallContext(
        execution_unit_id="eu-semconv", user_id="user-semconv", capabilities=["test.capability"],
        trace_id="t-semconv",
    )
    try:
        env = dispatcher.dispatch(name, {}, ctx)
    finally:
        syscall_registry.SYSCALL_REGISTRY.pop(name, None)
    assert env["status"] == "success", env
    found = _by_name(spans, f"syscall.{name}")
    assert len(found) == 1, [s.name for s in spans.get_finished_spans()]
    a = dict(found[0].attributes)
    assert a["enduser.id"] == "user-semconv"
    assert "user.id" not in a, "user.id was deprecated with a date (2.20.0 notes) and removed in 2.21.0"


def test_dispatcher_span_attributes_declare_the_semconv_key_only():
    """Structural companion: the span-attribute dict names `enduser.id` and not `user.id`."""
    import ast
    import pathlib

    src = pathlib.Path("AINDY/kernel/syscall_dispatcher.py").read_text(encoding="utf-8")
    keys = {
        k.value
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Dict)
        for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }
    assert {"enduser.id", "syscall.name"} <= keys
    assert "user.id" not in keys


# ---------------------------------------------------------------------------
# Metrics — the GenAI instruments exist beside the Prometheus counters, never instead
# ---------------------------------------------------------------------------

def test_genai_metrics_are_recorded_beside_the_prometheus_counters(monkeypatch):
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    # the SDK allows one global set; use the provider directly for this test's instruments
    monkeypatch.setattr(metrics, "get_meter", lambda name, *a, **k: provider.get_meter(name))
    monkeypatch.setattr(gt, "_instruments_ready", False)
    monkeypatch.setattr(gt, "_token_usage", None)
    monkeypatch.setattr(gt, "_operation_duration", None)

    with gt.llm_operation(provider="p", model="metric-probe") as op:
        op.record(_ChatResponse(_Usage(11, 5)))

    data = reader.get_metrics_data()
    names = {
        m.name
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    }
    assert "gen_ai.client.token.usage" in names
    assert "gen_ai.client.operation.duration" in names
    # and the Prometheus counter still exists under its own name — two pipelines, by design
    from AINDY.platform_layer.metrics import llm_tokens_total  # noqa: F401


def test_meter_provider_is_initialised_beside_the_tracer(monkeypatch):
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider

    from AINDY.platform_layer import otel

    seen = {}
    monkeypatch.setattr(metrics, "set_meter_provider", lambda p: seen.setdefault("provider", p))
    otel._init_metrics(None)
    assert isinstance(seen.get("provider"), MeterProvider)
