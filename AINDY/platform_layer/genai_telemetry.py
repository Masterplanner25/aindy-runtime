"""OpenTelemetry GenAI semantic-convention spans and metrics (`OTEL-GENAI-SEMCONV-1`).

Design: ``docs/design/OTEL_GENAI_SEMCONV_DESIGN.md``.

★ WHAT THIS IS, AND WHAT THE ENTRY THOUGHT IT WAS
--------------------------------------------------
The entry framed this as a *rename* of a public surface, gated on release discipline. Measured
at HEAD before this module existed, the runtime emitted exactly TWO span kinds — ``syscall.*``
and ``async_job.*`` — and **no span around an LLM call, a tool execution or an agent run**;
``set_attribute`` had zero call sites. The richness the entry credits us with lives in
Prometheus and the ``SystemEvent`` graph, not in OTel. So there was nothing GenAI-shaped to
rename; "adopt the conventions" means EMIT three span kinds at three chokepoints that already
exist, additively, with the names standard tooling looks for. Attribute keys are read from the
pinned ``opentelemetry-semantic-conventions`` package, never typed — one key has already been
renamed upstream (``gen_ai.system`` → ``gen_ai.provider.name``) and the conventions are still
*Development* stability.

★ THE METER MOVES INSIDE THE SPAN
----------------------------------
``llm_operation`` yields an ``LlmOperation`` whose ``record(response)`` IS the call to
``token_meter.observe_llm_usage`` — so a provider client cannot meter without tracing or trace
without metering, and the derived census guard in ``test_token_meter.py`` asserts one shape.
Nothing here decides anything: a failure in span or metric emission is swallowed, and the
meter's own accounting (`COST-GOVERNOR-1`) runs exactly once per call, as before.

★ CONTENT CAPTURE IS OUT
-------------------------
No ``gen_ai.input.messages`` / ``gen_ai.output.messages``, no prompt or completion bodies on any
span. That is a data-handling decision with its own answer (MAF ships it opt-in for exactly that
reason), and it is not made here.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from AINDY.platform_layer.otel import get_tracer, span_context_from_trace_id

logger = logging.getLogger(__name__)

try:  # keys come from the pinned package, never typed by hand
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as _ga

    ATTR_OPERATION_NAME = _ga.GEN_AI_OPERATION_NAME
    ATTR_PROVIDER_NAME = _ga.GEN_AI_PROVIDER_NAME
    ATTR_REQUEST_MODEL = _ga.GEN_AI_REQUEST_MODEL
    ATTR_RESPONSE_MODEL = _ga.GEN_AI_RESPONSE_MODEL
    ATTR_RESPONSE_FINISH_REASONS = _ga.GEN_AI_RESPONSE_FINISH_REASONS
    ATTR_USAGE_INPUT_TOKENS = _ga.GEN_AI_USAGE_INPUT_TOKENS
    ATTR_USAGE_OUTPUT_TOKENS = _ga.GEN_AI_USAGE_OUTPUT_TOKENS
    ATTR_TOKEN_TYPE = _ga.GEN_AI_TOKEN_TYPE
    ATTR_TOOL_NAME = _ga.GEN_AI_TOOL_NAME
    ATTR_TOOL_CALL_ID = _ga.GEN_AI_TOOL_CALL_ID
    ATTR_AGENT_ID = _ga.GEN_AI_AGENT_ID
    ATTR_AGENT_NAME = _ga.GEN_AI_AGENT_NAME
    ATTR_CONVERSATION_ID = _ga.GEN_AI_CONVERSATION_ID
    OP_CHAT = _ga.GenAiOperationNameValues.CHAT.value
    OP_EXECUTE_TOOL = _ga.GenAiOperationNameValues.EXECUTE_TOOL.value
    OP_INVOKE_AGENT = _ga.GenAiOperationNameValues.INVOKE_AGENT.value
    OP_EMBEDDINGS = _ga.GenAiOperationNameValues.EMBEDDINGS.value
    SEMCONV_AVAILABLE = True
except Exception:  # pragma: no cover - the package is pinned; this is the no-OTel path
    ATTR_OPERATION_NAME = "gen_ai.operation.name"
    ATTR_PROVIDER_NAME = "gen_ai.provider.name"
    ATTR_REQUEST_MODEL = "gen_ai.request.model"
    ATTR_RESPONSE_MODEL = "gen_ai.response.model"
    ATTR_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
    ATTR_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    ATTR_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    ATTR_TOKEN_TYPE = "gen_ai.token.type"
    ATTR_TOOL_NAME = "gen_ai.tool.name"
    ATTR_TOOL_CALL_ID = "gen_ai.tool.call.id"
    ATTR_AGENT_ID = "gen_ai.agent.id"
    ATTR_AGENT_NAME = "gen_ai.agent.name"
    ATTR_CONVERSATION_ID = "gen_ai.conversation.id"
    OP_CHAT, OP_EXECUTE_TOOL, OP_INVOKE_AGENT, OP_EMBEDDINGS = "chat", "execute_tool", "invoke_agent", "embeddings"
    SEMCONV_AVAILABLE = False

#: ``enduser.id`` — the stable semconv key for the caller's identity, emitted beside the
#: runtime's own ``user.id`` for one release (design §5), then alone.
ATTR_ENDUSER_ID = "enduser.id"
ATTR_ERROR_TYPE = "error.type"

_TRACER_NAME = "aindy.genai"


# ---------------------------------------------------------------------------
# Metrics — the OTel GenAI instruments, emitted BESIDE the `aindy_llm_*` Prometheus counters
# (never instead of: those are the operator surface the governor and soak harness read).
# ---------------------------------------------------------------------------

_token_usage = None
_operation_duration = None
_instruments_ready = False


def _instruments():
    """Lazily create the two GenAI client instruments on the process meter provider."""
    global _token_usage, _operation_duration, _instruments_ready
    if _instruments_ready:
        return _token_usage, _operation_duration
    _instruments_ready = True
    try:
        from opentelemetry import metrics
        from opentelemetry.semconv._incubating.metrics import gen_ai_metrics as _gm

        meter = metrics.get_meter(_TRACER_NAME)
        _token_usage = _gm.create_gen_ai_client_token_usage(meter)
        _operation_duration = _gm.create_gen_ai_client_operation_duration(meter)
    except Exception as exc:  # noqa: BLE001 — observability never decides
        logger.debug("[genai] instruments unavailable: %s", exc)
    return _token_usage, _operation_duration


def _link_kwargs(trace_id: Optional[str]) -> dict[str, Any]:
    """Nest under the current span if there is one; otherwise link to the runtime's trace id,
    exactly as the dispatcher does for ``syscall.*`` spans."""
    try:
        from opentelemetry import trace
        from opentelemetry.trace import NonRecordingSpan, set_span_in_context

        current = trace.get_current_span().get_span_context()
        if current.is_valid or not trace_id:
            return {}
        linked = span_context_from_trace_id(trace_id)
        if linked is None:
            return {}
        return {"context": set_span_in_context(NonRecordingSpan(linked))}
    except Exception:  # noqa: BLE001
        return {}


def _set(span, key: str, value: Any) -> None:
    if value is None:
        return
    try:
        span.set_attribute(key, value)
    except Exception:  # noqa: BLE001
        pass


def _finish_reasons(response: Any) -> Optional[list[str]]:
    """Best effort across the four SDK response shapes; ``None`` when the SDK does not say."""
    try:
        choices = getattr(response, "choices", None)
        if choices:
            reasons = [getattr(c, "finish_reason", None) for c in choices]
            reasons = [r for r in reasons if r]
            return reasons or None
        stop = getattr(response, "stop_reason", None)  # anthropic
        return [str(stop)] if stop else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# LLM operation — the seam the four provider clients wrap their call in
# ---------------------------------------------------------------------------

class LlmOperation:
    """Handle yielded by :func:`llm_operation`. ``record(response)`` meters AND annotates."""

    def __init__(self, span, *, provider: str, model: str, operation: str) -> None:
        self._span = span
        self.provider = provider
        self.model = model
        self.operation = operation
        self.recorded = False
        # FR-35 — when the call runs in a worker, its usage is DEFERRED to the parent, which
        # replays the span with these timestamps: the span here has no exporter to reach.
        self._started_epoch_ms = int(time.time() * 1000)
        self._started_perf = time.perf_counter()

    def record(self, response: Any) -> None:
        """Observe the response: the meter (exactly once) and the span's usage attributes."""
        from AINDY.platform_layer.token_meter import extract_token_usage, observe_llm_usage

        self.recorded = True
        observe_llm_usage(
            provider=self.provider, model=self.model, response=response,
            started_at_ms=self._started_epoch_ms,
            duration_ms=int((time.perf_counter() - self._started_perf) * 1000),
        )
        try:
            usage = extract_token_usage(response)
            if usage is not None:
                prompt, completion = usage
                _set(self._span, ATTR_USAGE_INPUT_TOKENS, int(prompt))
                _set(self._span, ATTR_USAGE_OUTPUT_TOKENS, int(completion))
                token_usage, _ = _instruments()
                if token_usage is not None:
                    base = {ATTR_OPERATION_NAME: self.operation, ATTR_PROVIDER_NAME: self.provider,
                            ATTR_REQUEST_MODEL: self.model}
                    token_usage.record(int(prompt), {**base, ATTR_TOKEN_TYPE: "input"})
                    token_usage.record(int(completion), {**base, ATTR_TOKEN_TYPE: "output"})
            _set(self._span, ATTR_RESPONSE_MODEL, getattr(response, "model", None))
            _set(self._span, ATTR_RESPONSE_FINISH_REASONS, _finish_reasons(response))
        except Exception as exc:  # noqa: BLE001 — the meter already ran; annotation is best effort
            logger.debug("[genai] response annotation skipped: %s", exc)


@contextmanager
def llm_operation(
    *,
    provider: str,
    model: str,
    operation: str = OP_CHAT,
    trace_id: Optional[str] = None,
) -> Iterator[LlmOperation]:
    """Span ``{operation} {model}`` around one model call; the client calls ``op.record(response)``.

    Span attributes: ``gen_ai.operation.name``, ``gen_ai.provider.name``, ``gen_ai.request.model``
    at start; usage, response model and finish reasons after ``record``. On exit the
    ``gen_ai.client.operation.duration`` histogram is observed with the same three dimensions,
    plus ``error.type`` when the call raised.
    """
    from AINDY.platform_layer.token_meter import current_llm_attribution

    tracer = get_tracer(_TRACER_NAME)
    attrs: dict[str, Any] = {
        ATTR_OPERATION_NAME: operation,
        ATTR_PROVIDER_NAME: provider,
        ATTR_REQUEST_MODEL: str(model),
    }
    try:
        tenant_id, run_id = current_llm_attribution()
        if tenant_id:
            attrs[ATTR_ENDUSER_ID] = str(tenant_id)
        if run_id:
            attrs[ATTR_CONVERSATION_ID] = str(run_id)
    except Exception:  # noqa: BLE001
        pass
    kwargs: dict[str, Any] = {"attributes": attrs, **_link_kwargs(trace_id)}
    try:
        from opentelemetry.trace import SpanKind

        kwargs["kind"] = SpanKind.CLIENT
    except Exception:  # noqa: BLE001
        pass

    started = time.perf_counter()
    error_type: Optional[str] = None
    try:
        span_cm = tracer.start_as_current_span(f"{operation} {model}", **kwargs)
    except Exception:  # noqa: BLE001
        span_cm = None
    if span_cm is None:
        yield LlmOperation(_NoSpan(), provider=provider, model=str(model), operation=operation)
        return
    with span_cm as span:
        op = LlmOperation(span, provider=provider, model=str(model), operation=operation)
        try:
            yield op
        except Exception as exc:
            error_type = type(exc).__name__
            _set(span, ATTR_ERROR_TYPE, error_type)
            try:
                from opentelemetry.trace import Status, StatusCode

                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            _, duration = _instruments()
            if duration is not None:
                dims = {ATTR_OPERATION_NAME: operation, ATTR_PROVIDER_NAME: provider,
                        ATTR_REQUEST_MODEL: str(model)}
                if error_type:
                    dims[ATTR_ERROR_TYPE] = error_type
                try:
                    duration.record(time.perf_counter() - started, dims)
                except Exception:  # noqa: BLE001
                    pass


def replay_deferred_llm_span(record: dict[str, Any], *, trace_id: Optional[str] = None) -> None:
    """FR-35 — emit the `chat {model}` span a WORKER-side call could never export, in the parent,
    with the record's own timestamps, under the current span. Late, not wrong: it says when the
    call happened, in the trace an operator is looking at. Never raises."""
    try:
        from opentelemetry.trace import SpanKind, Status, StatusCode

        provider = str(record.get("provider") or "unknown")
        model = str(record.get("model") or "unknown")
        started_ms = int(record.get("started_at_ms") or 0)
        duration_ms = int(record.get("duration_ms") or 0)
        if started_ms <= 0:
            started_ms = int(time.time() * 1000) - duration_ms
        start_ns = started_ms * 1_000_000
        end_ns = (started_ms + duration_ms) * 1_000_000
        attrs: dict[str, Any] = {
            ATTR_OPERATION_NAME: OP_CHAT,
            ATTR_PROVIDER_NAME: provider,
            ATTR_REQUEST_MODEL: model,
            ATTR_USAGE_INPUT_TOKENS: int(record.get("prompt_tokens") or 0),
            ATTR_USAGE_OUTPUT_TOKENS: int(record.get("completion_tokens") or 0),
            "aindy.deferred": True,
        }
        tool = record.get("tool")
        if tool:
            attrs[ATTR_TOOL_NAME] = str(tool)
        try:
            tenant_id, run_id = __import__("AINDY.platform_layer.token_meter", fromlist=["current_llm_attribution"]).current_llm_attribution()
            if tenant_id:
                attrs[ATTR_ENDUSER_ID] = str(tenant_id)
            if run_id:
                attrs[ATTR_CONVERSATION_ID] = str(run_id)
        except Exception:  # noqa: BLE001
            pass
        tracer = get_tracer(_TRACER_NAME)
        kwargs: dict[str, Any] = {"attributes": attrs, "kind": SpanKind.CLIENT, "start_time": start_ns, **_link_kwargs(trace_id)}
        span = tracer.start_span(f"{OP_CHAT} {model}", **kwargs)
        outcome = str(record.get("outcome") or "ok")
        if outcome != "ok":
            _set(span, ATTR_ERROR_TYPE, outcome.split(":", 1)[-1])
            span.set_status(Status(StatusCode.ERROR, outcome))
        span.end(end_time=max(end_ns, start_ns))
    except Exception as exc:  # noqa: BLE001 — observability never decides
        logger.debug("[genai] deferred span not replayed: %s", exc)


# ---------------------------------------------------------------------------
# Tool and agent operations — the other two chokepoints
# ---------------------------------------------------------------------------

class _NoSpan:
    def set_attribute(self, *a, **k):
        return None

    def record_exception(self, *a, **k):
        return None

    def set_status(self, *a, **k):
        return None


class OperationSpan:
    """Handle yielded by :func:`tool_operation` / :func:`agent_operation`."""

    def __init__(self, span) -> None:
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:
        """One runtime-owned attribute (``aindy.*``); ``None`` is skipped, never an error."""
        _set(self._span, key, value)

    def outcome(self, result: Any) -> None:
        """Annotate the runtime's result envelope: success, and the failure class if any."""
        if not isinstance(result, dict):
            return
        _set(self._span, "aindy.success", bool(result.get("success")))
        _set(self._span, "aindy.failure_class", result.get("failure_class"))
        if not result.get("success"):
            try:
                from opentelemetry.trace import Status, StatusCode

                self._span.set_status(Status(StatusCode.ERROR, str(result.get("error") or "")[:256]))
            except Exception:  # noqa: BLE001
                pass


@contextmanager
def _operation(name: str, attrs: dict[str, Any], trace_id: Optional[str], kind_name: str) -> Iterator[OperationSpan]:
    tracer = get_tracer(_TRACER_NAME)
    kwargs: dict[str, Any] = {"attributes": {k: v for k, v in attrs.items() if v is not None}, **_link_kwargs(trace_id)}
    try:
        from opentelemetry.trace import SpanKind

        kwargs["kind"] = getattr(SpanKind, kind_name)
    except Exception:  # noqa: BLE001
        pass
    try:
        span_cm = tracer.start_as_current_span(name, **kwargs)
    except Exception:  # noqa: BLE001
        span_cm = None
    if span_cm is None:
        yield OperationSpan(_NoSpan())
        return
    with span_cm as span:
        handle = OperationSpan(span)
        try:
            yield handle
        except Exception as exc:
            _set(span, ATTR_ERROR_TYPE, type(exc).__name__)
            try:
                from opentelemetry.trace import Status, StatusCode

                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            except Exception:  # noqa: BLE001
                pass
            raise


def tool_operation(*, tool_name: str, run_id: Optional[str] = None, trace_id: Optional[str] = None):
    """Span ``execute_tool {tool_name}`` around one mediated tool call (`execute_tool`).

    Entered AFTER the capability and cancellation checks: a refusal is an error envelope the
    caller already sees, and the ``syscall.*`` span records refusals at the dispatcher.
    """
    return _operation(
        f"{OP_EXECUTE_TOOL} {tool_name}",
        {ATTR_OPERATION_NAME: OP_EXECUTE_TOOL, ATTR_TOOL_NAME: tool_name,
         ATTR_TOOL_CALL_ID: str(run_id) if run_id else None, ATTR_CONVERSATION_ID: str(run_id) if run_id else None},
        trace_id, "INTERNAL",
    )


def agent_operation(*, agent_name: str, run_id: str, agent_id: Optional[str] = None,
                    user_id: Optional[str] = None, trace_id: Optional[str] = None):
    """Span ``invoke_agent {agent_name}`` around one agent run (`execute_run`)."""
    return _operation(
        f"{OP_INVOKE_AGENT} {agent_name}",
        {ATTR_OPERATION_NAME: OP_INVOKE_AGENT, ATTR_AGENT_NAME: agent_name,
         ATTR_AGENT_ID: str(agent_id) if agent_id else None, ATTR_CONVERSATION_ID: str(run_id),
         ATTR_ENDUSER_ID: str(user_id) if user_id else None},
        trace_id, "INTERNAL",
    )
