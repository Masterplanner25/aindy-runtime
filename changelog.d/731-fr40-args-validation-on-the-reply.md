### Fixed — FR-40: on `nodus_vm`, the declared-args validation tally now reaches the api (#731; DEC-067)

- **Why it was wrong:** `execute_tool` counted `aindy_tool_args_validation_total{tool, outcome, mode}`
  and logged FR-33's `warn` line in whichever process ran it. On the `nodus_vm` backend that is the
  pool worker — its registry never serves `/metrics` and the pool opens it with `stderr=DEVNULL` —
  so "every step validated clean" and "validation never ran" were indistinguishable from the api,
  and the 2.20.0 recipe (leave at `warn`, watch `invalid` read zero, then `enforce`) could not be
  followed on the backend the app runs.
- Same mechanism as FR-35: the tally rides the worker reply as `args_validation` (the fifth deferred
  collection) and is recorded in the api — counter samples under the api's registry, and one `warn`
  WARNING per tool naming the call count, the errors and the origin. Deferral replaces observation
  in the worker; nothing is counted twice. Errors carried per tool are capped by
  `AINDY_TOOL_ARGS_VALIDATION_LEDGER_MAX` (default 32); counts are never dropped.
- **App side:** the recipe now holds on `nodus_vm` — read `outcome="invalid"` on the api's
  `/metrics` before flipping `AINDY_TOOL_ARGS_VALIDATION=enforce`.
