### Changed — an effect whose outcome is unknown or partial is held, never re-run (EFFECT-OUTCOME-UNKNOWN-1, DEC-085..087, DEC-090, #790)

- **Read before upgrading if you retry `EXACTLY_ONCE` syscalls or tools, or use a lenient
  fan-out join.** On a replay, an effect recorded `unknown` or `partial` now returns its recorded
  outcome, and the handler does not run again. Before, it was reclaimed and re-run: a retried
  lenient fan-out (FLOW-PARALLEL-1) re-ran every branch, including the ones that landed. The held
  envelope carries `status: "unknown"` / `"partial"`, the recorded `outcome` (units and detail),
  `outcome.held: true`, and for `unknown` also `reconcile_required: true` and
  `failure_class: "unknown"`. **Why:** re-running an effect whose outcome is unknown can perform
  it twice, and that duplicate is what the status exists to prevent.
- New `AINDY.kernel.syscall_outcome.EffectOutcomeUnknown`. Raise it from a syscall handler or a
  tool when the effect was dispatched but its outcome was not observed (a read timeout after a
  full write). It is recorded `unknown` on both seams, including from an isolated tool worker.
  **New outcome emitter:** the dispatcher turns a raised `EffectOutcomeUnknown` into the
  `_outcome: unknown` claim.
- New `failure_class: "unknown"`. No retry loop retries it (agent backends, `decide_retry`, the
  compiled `nodus_vm` loop).
- An `unknown` outcome skips output-schema validation. Before, a stable syscall recorded it
  `failed`, which made it reclaimable.
- The effect-record TTL job never reaps an `unknown` row. Watch the new gauge
  `aindy_effect_unknown_unresolved`; the cleanup scan logs a WARNING while it is nonzero. A route
  to reconcile them comes in a later phase.
