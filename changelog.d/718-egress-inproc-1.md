### Fixed — `EGRESS-INPROC-1`: an isolated tool ran with no egress enforcement; the decision is now made once and enforced where the tool runs (#718; DEC-048..051)

- **The defect:** `execute_tool` computed the capability-policy domain allowlist and entered
  `egress_scope` around the IN-PROCESS call only. The `isolation=` branch returned before it, so
  the one tool the runtime distrusts enough to move out of process was the one tool the socket
  guard never covered — with `AINDY_EGRESS_ENFORCEMENT` on or off. The allowlist was computed for
  it and dropped. (Same shape as `CANCEL-REACH-1` residual 2, on the egress axis.)
- **Now:** `EgressDecision(mode, domains)` is resolved once, BEFORE the branch, from the policy
  domains and the tool's effective `authority.network` — `none` and `scoped`-with-no-list are
  deny-all, fail-closed. The in-process branch scopes it as before; the worker receives it as an
  additive request key (`egress`) and installs the guard **process-globally**, which also closes
  the raw-`threading.Thread` contextvar bypass there (still open in-process, by the guard's own
  docstring). The worker never reads policy.
- **Reported, not assumed:** when enforcement is on, the tool envelope carries
  `egress: {mode, mechanism}` (`socket_guard` | `socket_guard:worker` | `none`) and the
  `execute_tool` span carries `aindy.egress.mode` / `aindy.egress.mechanism`. A provider that
  cannot enforce a declared mode reports rather than refuses.
- **Behaviour change to read before enabling the flag:** a tool whose `env_spec` declares
  `authority.network="none"` is now deny-all on both branches when the flag is on — the tool seam
  previously ignored that axis. `AINDY_EGRESS_ENFORCEMENT` stays default-off; flag off, nothing
  changes and envelopes are byte-identical.
- Worker protocol: request gains optional `egress`; every reply gains `egress_mechanism`.
