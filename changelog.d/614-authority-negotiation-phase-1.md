### Added — one bounded downgrade attempt on a capability denial, default-off (#614)

`AUTHORITY-NEGOTIATION-1` phase 1. **Off by default**; set `AINDY_AUTHORITY_NEGOTIATION=1` to
enable. Phase 0 (#600) shipped `register_tool(..., degraded_variant=)` and deliberately consulted
it from nothing; this makes it consulted.

A denied capability terminates the step, and because approval is whole-plan, the only recovery was
a human approving an entirely new run — which discards the durable state the original accumulated.
A run that did nine steps of real work and was refused on the tenth started again from zero. With
the flag on, such a denial is offered **exactly one** downgrade to a fallback the *tool* declared
at registration, and only if the capability token already authorises it.

- **It cannot grant authority.** Negotiation decides only *which tool to attempt*; the fallback is
  then executed through `execute_tool`, which runs its own capability check. A negotiated tool
  passes exactly the gate an ordinary one passes — there is no new token, no amendment, and no
  widening path.
- **Bounded and downgrade-only.** One attempt per denial. A fallback may not declare its own
  fallback, so the bound is a property of the registry rather than a counter.
- **Recorded.** A new `AUTHORITY_NEGOTIATED` agent event carries the denied tool, the fallback and
  the outcome; the step result records `negotiated_from` so the history shows what was refused as
  well as what ran. New counter `aindy_authority_negotiation_total{outcome}` —
  `succeeded | no_variant | variant_denied | refused_not_granted | disabled | chain_refused`.
- **Arguments carry over unchanged.** Declaring `degraded_variant=X` is the tool author's promise
  that `X` accepts the original's arguments. There is no argument-mapping vocabulary, so a
  mismatched variant fails at execution rather than at declaration.

Only the tool-level denial site negotiates. The other `CAPABILITY_DENIED` emissions refuse a
missing token or the run-level `execute_flow` capability, where there is no tool and therefore no
`degraded_variant` to declare.

**Nothing changes with the flag off**, which is every existing deployment: the denial path is
byte-for-byte what it was, and the counter reports `disabled`.
