---
title: "OpenTelemetry GenAI Semantic Conventions — Design"
api_version: "1.0"
last_verified: "2026-09-16"
status: current
owner: "platform-team"
---

# OpenTelemetry GenAI semantic conventions — design

**`OTEL-GENAI-SEMCONV-1`. PHASES 1 + 2 SHIPPED 2026-09-16 (#706; DEC-034 … DEC-038) — entry CLOSED;
phase 3 (drop `user.id`) is owed the release AFTER the one that ships this.** Live record:
`AINDY/platform_layer/genai_telemetry.py`, `docs/runtime/RUNTIME_BEHAVIOR.md` §5.

> **As built, where it differs from the text below:** `user.id` exists only on the `syscall.*`
> span — the `async_job.*` span carries `job.name`/`job.id`/`trace.id` — so §5's rename is one
> key on one span kind. The `MeterProvider` is initialised inside `init_otel` (`_init_metrics`).
> The `execute_tool` span carries `aindy.success` / `aindy.failure_class` from the envelope and
> sets ERROR status on a failed result. Three derived census guards (`test_token_meter.py` ×2,
> `test_llm_budget.py`) learned the new metering shape — `op.record(...)` inside
> `with llm_operation(...)` — and a direct `observe_llm_usage` call in a client is now REFUSED
> by the census. Mutation-tested 8/8, all through real entry points on an in-memory exporter.

**Originally:** DESIGN ONLY — nothing shipped. The entry frames this as a *rename*
of a public surface, gated on release discipline ("additive first, both emitted for a release,
documented removal"). §1 measures the surface and finds the premise mostly wrong in a way that
makes the work **smaller and safer**: there is almost nothing GenAI-shaped to rename, because
the runtime emits no LLM, tool or agent span at all. What "adopt the conventions" means here is
*emit three span kinds at three chokepoints that already exist*, additively. §5 is the one
genuine rename; §7 is what not to build — content capture stays out, as the entry requires.

---

## 1. ★★ The OTel surface at HEAD is two span kinds, and neither is a GenAI operation

Measured 2026-09-16 (`grep -rn "start_as_current_span\|set_attribute" AINDY`):

| Span name | Site | Attributes |
|---|---|---|
| `syscall.{name}` | `kernel/syscall_dispatcher.py:824` | `syscall.name`, `syscall.version`, `syscall.capability`, `user.id`, `trace.id` |
| `async_job.{task}` | `platform_layer/async_job_service.py:1281` | same shape, keyed on the job |

That is the whole list. **`set_attribute` has zero call sites** — attributes exist only at span
creation. There is **no span** around an LLM call, a tool execution, or an agent run. The
richness the entry credits us with is real, but it lives in **Prometheus** (`aindy_llm_tokens_total
{provider, model, kind}`, `aindy_llm_calls_total{provider, attributed}`, 52 metric families)
and in the **`SystemEvent` causal graph** — not in OTel. Standard tooling cannot read our
traces not because our names are wrong but because the operations it looks for are not there.

So the entry's risk model — "attribute names appear in operator dashboards and anything a
consumer built against them" — applies to **five attribute keys on two span kinds**, and to
the Prometheus names, which this design does not touch (§6). Everything GenAI-shaped is new.

---

## 2. The conventions, and which of ours they map onto

The GenAI semantic conventions (`opentelemetry-semantic-conventions`, `gen_ai.*`) define, at
the version the implementing PR pins:

| Convention | Meaning | Our chokepoint |
|---|---|---|
| span `{gen_ai.operation.name} {gen_ai.request.model}` — e.g. `chat claude-opus-5` | one model invocation | the LLM seam: the four provider clients' call sites, each already bracketed by `llm_budget` (reserve, before) and `observe_llm_usage` (meter, after) |
| `gen_ai.operation.name = execute_tool`, span `execute_tool {gen_ai.tool.name}` | one tool call | `tool_registry.execute_tool` — the mediated-effect chokepoint |
| `gen_ai.operation.name = invoke_agent`, span `invoke_agent {gen_ai.agent.name}` | one agent run | `agent_runtime/execution.py::execute_run`, where `llm_attribution_scope` is already entered |
| `gen_ai.provider.name` (★ formerly `gen_ai.system`; renamed upstream — pin the version, emit the current key) | provider | the `provider=` string the meter already receives |
| `gen_ai.request.model`, `gen_ai.response.model` | model asked for / answered with | `model=` and `response.model` where the SDK exposes it |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | usage | **`extract_token_usage(response)` already returns exactly this pair** |
| `gen_ai.response.finish_reasons` | why the model stopped | per-client; `None` where the SDK does not say |
| `gen_ai.tool.name`, `gen_ai.tool.call.id` | tool identity | `tool_name`; the run id + step index as the call id |
| `gen_ai.agent.id`, `gen_ai.agent.name` | agent identity | `AgentRun.agent_id` / registry name |
| `gen_ai.conversation.id` | the thread | our `trace_id` — the same value the dispatcher links spans to |
| metrics `gen_ai.client.token.usage` (histogram, by `gen_ai.token.type`), `gen_ai.client.operation.duration` | usage and latency | emitted from the same seam, **beside** the `aindy_llm_*` counters, never instead of them |

**★ The conventions are still marked *Development* stability upstream, and one key has already
been renamed under us (`gen_ai.system` → `gen_ai.provider.name`).** That is not a reason to
wait — the value is that tooling reads the current names — but it is a reason to (a) pin the
semconv package version in the implementing PR and read the keys from it rather than typing
them, and (b) expect one rename cycle of *our own* later, which §5's additive protocol already
covers.

---

## 3. One helper, three seams — and the meter moves inside it

Today each provider client does, after its call:

```python
observe_llm_usage(provider="anthropic", model=str(model), response=response)
```

and `test_every_provider_client_meters_its_response` derives the client census from the
source and asserts each one does. The design folds the meter into a span helper so **a client
cannot meter without tracing or trace without metering**:

```python
# AINDY/platform_layer/genai_telemetry.py (proposed)
@contextmanager
def llm_operation(*, provider: str, model: str, operation: str = "chat"):
    with tracer.start_as_current_span(f"{operation} {model}", attributes={...}) as span:
        op = _LlmOperation(span, provider, model)
        yield op                      # client calls op.record(response) — that IS observe_llm_usage
        # on exit: gen_ai.usage.* set from what record() saw; duration histogram observed
```

- The four clients change one line each: `with llm_operation(...) as op: response = …;
  op.record(response)`. `observe_llm_usage` stays as the function `record()` calls, so the
  meter's own tests and `COST-GOVERNOR-1`'s accrual path are untouched.
- **The census guard changes what it asserts, not how it derives:** every client that calls
  a `METERED_METHODS` method does so inside `llm_operation`. Same AST walk, same
  non-empty-census assertion, one more `With` node to find.
- `execute_tool` gets `tool_operation(tool_name, run_id, step)` around the dispatch — after
  the capability check and cancel check, so a refused tool has no span (a refusal is an
  error envelope, and the syscall span already records those).
- `execute_run` gets `agent_operation(agent_id, name)` at the point `llm_attribution_scope`
  is entered — the two scopes are the same lifetime.

**Nesting falls out for free.** Spans started as current inside a `syscall.*` or `async_job.*`
span become its children; outside one, the helper links to `trace_id` exactly as the
dispatcher does (`span_context_from_trace_id` → `NonRecordingSpan`), so an LLM call from a
route handler still lands in the request's trace.

**Cost when OTel is not installed:** the same `_NoopTracer` path `otel.py` already takes; the
helper is a context manager over a no-op span, and `record()` still meters.

---

## 4. Guest and worker boundaries — where a span cannot follow

- **Isolated tools (`tool_worker.py`)** run in another process with no tracer provider. The
  parent's `tool_operation` span brackets the worker's whole lifetime, which is the correct
  observation from the runtime's side; the child gets no span, and this design does not
  propagate context into it (it gets no `run_id` either — `CANCEL-REACH-1`'s rule: the check
  runs where it can act).
- **Guest `sys()` calls** already arrive at the dispatcher and get a `syscall.*` span. A guest
  script's `call_tool` reaches `execute_tool` in the host and gets a tool span. Nothing new is
  needed at the guest boundary.

---

## 5. The one genuine rename, and the protocol for it

`user.id` and `trace.id` on the two existing span kinds are our names for what semconv calls
`enduser.id` and — nothing; `trace.id` duplicates the span's own trace id and exists only because
the dispatcher links by `trace_id` string. Proposed:

| Release N | Release N+1 |
|---|---|
| emit `enduser.id` **beside** `user.id`; keep `trace.id`; changelog names both | drop `user.id`; keep `trace.id` (it is not wrong, it is redundant, and a consumer may filter on it) |

That is the entry's "additive first, both emitted for a release, documented removal", applied
to the only keys it actually applies to. **`syscall.*` and `async_job.*` span *names* do not
change** — semconv has no vocabulary for a syscall, and inventing a `gen_ai.*` name for one
would be false alignment.

---

## 6. What this design does not touch

- **Prometheus metric names.** `aindy_llm_tokens_total` and friends are the operator surface
  the soak harness reads and the governor accrues from. `gen_ai.client.token.usage` is emitted
  *in addition*, through the OTel meter provider (which `otel.py` does not yet initialise —
  phase 1 adds a `MeterProvider` beside the `TracerProvider`, exported over the same OTLP
  endpoint). Two names for one number, by design, on two different pipelines.
- **The `SystemEvent` causal graph.** It stays the differentiator; nothing here reads or
  writes it.
- **Content capture.** No `gen_ai.input.messages` / `gen_ai.output.messages`, no prompt or
  completion bodies on any span or event. The entry's scope note is a decision boundary, not
  an omission — MAF ships it opt-in for the data-handling reason, and so would we, under a
  separate proposal.

---

## 7. What not to build

- **Not MAF's MRO-layered instrumentation** — the entry already says so; we have one seam
  per operation and a context manager is the whole mechanism.
- **Not a `gen_ai.*` name for syscalls or async jobs** (§5).
- **Not auto-instrumentation via `opentelemetry-instrumentation-openai` et al.** Those patch
  the SDK clients and would produce a second span per call beside ours, with content capture
  on by default in some versions — exactly the data-handling decision §6 keeps separate. Our
  seam is the four clients we own.
- **Not per-tenant attributes on the metric.** `token_meter.py`'s docstring records why the
  labels stop at provider and model (cardinality); `gen_ai.client.token.usage` keeps the same
  two dimensions plus `gen_ai.token.type`. Tenant attribution stays on the span
  (`enduser.id`), where cardinality is free.

---

## 8. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. "Adopt the conventions" = **emit** three new span kinds additively; the two existing span
   names are unchanged (§1, §5).
2. The meter moves **inside** the span helper so the census guard covers both with one
   assertion (§3).
3. `enduser.id` is added beside `user.id` for one release, then `user.id` is dropped (§5).
4. `gen_ai.client.*` metrics are emitted beside — never instead of — `aindy_llm_*` (§6).
5. Content capture is **out**, and needs its own proposal (§6).

## 9. Phasing and tests

| Phase | Ships |
|---|---|
| 1 | `genai_telemetry.py` (`llm_operation` + folded meter), `MeterProvider` in `otel.py`, the four clients, the census guard's new assertion, semconv package pinned |
| 2 | `tool_operation` in `execute_tool`, `agent_operation` in `execute_run`, `enduser.id` added |
| 3 (next release) | `user.id` dropped; changelog entry names the removal at the top |

Tests: an in-memory span exporter (`InMemorySpanExporter`) asserting, per client, the span
name `chat {model}` and `gen_ai.usage.input_tokens` equal to what `extract_token_usage` read —
a real client call against a stub transport, not a patched `observe_llm_usage` (variant 13: a
fixture that stubs the meter cannot see that the span carried its numbers). A mutation that
removes `op.record(response)` from one client must turn the census guard red, not just the
meter test.
