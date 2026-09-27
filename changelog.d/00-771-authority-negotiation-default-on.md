### Changed — authority negotiation is ON by default; a tool that declares `on_denial="wait"` now parks its run on a denial instead of failing it (AUTHORITY-NEGOTIATION-1, DEC-081, #771)

- **Read before upgrading if any of your tools declares `degraded_variant=` or
  `on_denial="wait"`.** With `AINDY_AUTHORITY_NEGOTIATION` unset, a capability denial on such a
  tool is now negotiated. The runtime makes one attempt at the declared fallback if the token
  already grants it. Otherwise the run PARKS on `agent.authority.decision` for an operator's
  `skip` or `abort`. Before, the step failed. Set `AINDY_AUTHORITY_NEGOTIATION=0` (or
  `false` / `no` / `off`) to keep the old behaviour. Any other value, blank included, is now on.
- Why now: the evidence the flip waited for exists on both backends. A real declared tool was
  denied, parked, resumed with `skip` from another process, and completed on `agent_flow` and on
  `nodus_vm`. Negotiation cannot grant authority: `execute_tool` re-checks whatever tool is
  attempted.
- A tool that declares neither a fallback nor a gate behaves exactly as before. On `nodus_vm` it is
  no longer pre-checked by the worker at all. Without that change, the flip would have run the
  capability check twice on every tool step. As a result, on `nodus_vm` an undeclared tool's
  denial is no longer counted as `aindy_authority_negotiation_total{outcome="no_variant"}`.
