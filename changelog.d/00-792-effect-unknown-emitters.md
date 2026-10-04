### Changed — the runtime now reports `unknown` where an effect's outcome cannot be observed (EFFECT-OUTCOME-UNKNOWN-1, DEC-088, #792)

- **Read before upgrading if you call `outbound_request` with POST or PATCH.** A request that may
  already have been processed (a read timeout or dropped connection after it was sent, or a
  500/502/503/504 response) is **no longer retried** for a non-idempotent method. It raises
  `EffectOutcomeUnknown`. **Why:** retrying a POST blindly is how one charge becomes two. Pass
  `idempotent=True` for a request that is safe to repeat (for example one carrying an idempotency
  key). Failures that never reached the server (connect errors, pool and write timeouts, 408, 429)
  are still retried for every method, and GET/HEAD/OPTIONS/TRACE/PUT/DELETE retry as before. New
  helper: `outbound_http.classify_transport_exception`.
- An isolated tool that declares `EXACTLY_ONCE` or `AT_MOST_ONCE` and whose worker is lost after
  starting (killed by its time budget or a cancel, crashed, or replied unreadably) is recorded
  `unknown` and held on retry. Before, it was `transient` and retried, so it could act twice. A
  worker that failed to start, or a tool with no declared guarantee, behaves as before.
- `AINDY_MCP_SERVERS` entries accept an optional `"guarantee"`. For a server with an effect
  guarantee, a call that times out after it was sent is recorded `unknown`. A timeout while
  connecting, or on a server without one, stays a retryable timeout.
