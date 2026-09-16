### Changed — a failure carries a class; retries are decided by it, not by substring matching (`RETRY-CLASSIFY-1`, #703)

- Every `execute_tool` refusal and every syscall **error** envelope now returns `failure_class`
  beside `error`: one of `transient | cancelled | permission | not_found | invalid | fatal`.
  Only `transient` is retried. The syscall envelope change is additive (error envelopes only;
  `docs/runtime/SYSCALL_SYSTEM.md`).
- **Behaviour change:** a cancelled run's tool, a tool call missing its capability token, and a
  crashed capability-enforcement check were re-attempted up to 3× — their error text matched
  none of the nine substrings, so the classifier read *retryable*. Each is now attempted once.
  Any *other* error text is classified exactly as before (the substring table survives as the
  fallback for un-classed strings; an unmatched string is still `transient`).
- Compiled agent plans now emit `is_retryable_error(__result_N)` — the whole `call_tool`
  result — instead of `is_retryable_error(__result_N["error"])`, so a class `execute_tool`
  declared reaches the guest loop and a model-shaped error message cannot decide its own retry.
  The Nodus host function accepts a dict or a string.
- New metric `aindy_retry_classifications_total{site, failure_class, classified_by, decision}`,
  one increment per retry decision. `classified_by="substring"` is the residue the fallback
  table still owns. `flow.node.*` / `agent.step.*` failure events carry the record under
  `payload.retry`.
- `execute_with_retry` / `_execute_with_retry` removed from `AINDY/core/retry_policy.py` —
  zero callers (DEC-024). `is_retryable_error` now takes the result dict or a string.
- Decisions recorded: DEC-024, DEC-025. Design: `docs/design/RETRY_CLASSIFICATION_AND_CONTEXT_DESIGN.md`.
