### Fixed — an enveloped response now carries ONE trace id, not two (FR-26) (#621)

- `ExecutionContext.from_request` now prefers the trace id `log_requests` already assigned to
  the request (`request.state.trace_id`) over the incoming `X-Trace-ID` / `X-Request-ID`
  headers, minting a fresh uuid only when neither exists. Before this, every request under the
  middleware got a second id here — a browser sends neither header — so the response's
  `X-Trace-ID` and its body `trace_id` disagreed and resolved to **two different event
  graphs**: the pipeline's `execution.started/completed` under the body's id, and everything
  the handler did (flow run, syscalls, memory writes — which read the contextvar) under the
  header's. The id most likely to be copied out of a client showed a route that ran and
  produced nothing. Filed by the app team as `TRACE-ID-DUAL-1` (found 2026-07-22).
- An explicit `execute_with_pipeline(metadata={"trace_id": …})` still wins; only the default
  changed.
- **Behaviour change worth knowing:** a *client-sent* `X-Trace-ID` no longer becomes the
  pipeline's id when the request passes through `log_requests` — the middleware never honoured
  it for the header, and the pipeline quietly did for the body, so a client could choose the id
  half its request was recorded under. Both halves now use the middleware's. The header
  fallbacks keep their old meaning for a Request that did not pass through the middleware (a
  mounted app, a test harness). Honouring a client-chosen id is a trust-boundary decision and
  is deliberately not made here.
