### Added — OpenTelemetry GenAI semantic-convention spans and metrics (`OTEL-GENAI-SEMCONV-1`, #706)

- Three new span kinds, emitted additively at seams that already existed: **`chat {model}`**
  around every provider call (`gen_ai.provider.name`, `gen_ai.request.model`,
  `gen_ai.usage.input_tokens` / `output_tokens`, `gen_ai.response.model`, finish reasons;
  `enduser.id` and `gen_ai.conversation.id` when an attribution scope is active),
  **`execute_tool {tool}`** around the actual invocation in `execute_tool` (a refusal gets no
  span — it is an error envelope the caller already sees), and **`invoke_agent {agent_type}`**
  with the attribution scope's lifetime in `execute_run`, under which the other two nest.
  Attribute keys are read from the pinned `opentelemetry-semantic-conventions` package, so the
  emitted key is the current `gen_ai.provider.name`, not the renamed-away `gen_ai.system`.
- **The token meter now lives inside the `chat` span.** Provider clients call
  `op.record(response)` inside `with llm_operation(...)`; that is the one call to
  `observe_llm_usage`, so the `aindy_llm_*` counters are unchanged and counted exactly once as
  before. A client that meters directly, or traces without recording, is refused by the
  derived census in `test_token_meter.py`.
- New OTel metrics `gen_ai.client.token.usage` and `gen_ai.client.operation.duration`, through
  a `MeterProvider` initialised beside the `TracerProvider` and exported over the same
  `OTEL_EXPORTER_OTLP_ENDPOINT`. They sit **beside** the Prometheus `aindy_llm_*` counters,
  never instead of them.
- **`syscall.*` spans now carry `enduser.id` beside `user.id`.** `user.id` is deprecated and
  will be **removed in the release after this one** — repoint any dashboard filter now.
- No prompt or completion content is placed on any span; a test refuses any `gen_ai.input*` /
  `gen_ai.output*` attribute. Auto-instrumentation packages are not used.
- Decisions recorded: DEC-034 … DEC-038. Design: `docs/design/OTEL_GENAI_SEMCONV_DESIGN.md`.
