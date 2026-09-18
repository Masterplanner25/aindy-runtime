### Changed — `AUTHORITY-LIFETIME-1`: a capability token is valid while its run is live, not while the clock says so (#720; DEC-056..059)

- A token presented for a run in a **terminal** status (`completed | failed | verify_failed |
  cancelled | refused`) is now refused at the two effect chokepoints — `check_tool_capability`
  (before the HMAC check) and the syscall dispatcher's agent gate. Before, `TOKEN_TTL_HOURS = 24`
  was the only bound: a run that finished in 90 seconds could present its token all day.
- The read is `CANCEL-REACH-1`'s existing own-session, per-run-cached status read, widened to
  return the status (`cancellation.run_terminal_status`). **No new read on the hot path**; a
  terminal answer is sticky for the process lifetime, so a finished run costs zero queries after
  the first. Fails OPEN (an unreadable status reads as live); the token's expiry stays the outer
  bound. `is_run_cancelled` is unchanged in behaviour.
- Refusal shape: `failure_class="permission"`, error `run <id> is <status>; authority ended with
  the run`. A **cancelled** run keeps its existing envelope (`failure_class="cancelled"`,
  `cancelled=true`) — it is now observed one step earlier on the tool path, before the effect
  ledger reserves anything.
- **A `waiting` run keeps its authority** — a parked run resumes with the same token. The token
  itself stays stateless: callers without a run (the planner path) see no change.
- New counter `aindy_authority_lifetime_refusals_total{status, surface}`; a cancel still moves
  `aindy_run_cancel_observed_total` as before.
