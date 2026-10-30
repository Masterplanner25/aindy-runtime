### Changed — the dispatcher refuses a payload `user_id` that names anyone but the caller (`PAYLOAD-IDENTITY-DISPATCH-1`, DEC-103, #866)

- **Operators: read before upgrading.** An in-process `dispatch()` whose payload carries a
  non-empty `user_id` different from the context's caller now returns `TENANT_VIOLATION` with
  `failure_class: permission`, and the handler does not run. Until now the dispatcher only counted
  it (DEC-100). The three transports with an outside writer (MCP, the Nodus `sys()` seam,
  `POST /platform/syscall`) already refused it (DEC-099). This closes the same hole for every
  other caller, including code nobody has written yet.
- **Why it was wrong:** handlers read `payload["user_id"]` to decide whose data to serve, and
  the dispatcher authenticated the caller without comparing the two. So any in-process code that
  built a payload from untrusted state could act as another user.
- **Evidence for the flip:** `aindy_payload_identity_mismatch_total` read 0 over 2,289 app
  dispatches across two windows, and the app checked all 93 of its in-process dispatch sites. One
  passes `user_id` as a *filter*: `watcher_signals_list_node`, operator-only. It is refused under
  this release and is being fixed on the app side.
- **Escape hatch:** `AINDY_PAYLOAD_IDENTITY_DISPATCH=observe` restores count-only. Only that exact
  value (case-insensitive) disables refusal; any other value refuses. The counter keeps counting
  in both modes, so its `{syscall}` label names the call site either way. If you need the escape
  hatch, read the counter, fix the caller, then remove the setting. A legitimate on-behalf call
  has no supported form yet (`INITIATOR-IDENTITY-1`).
- Absent, `None` or empty `user_id` keys are unaffected. So is a `user_id` equal to the caller's
  in any UUID spelling.
