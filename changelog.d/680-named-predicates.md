### Added — named flow predicates; a named decision is part of the graph signature (`FLOW-PARALLEL-1` phase 3a, #680)

- A conditional edge may declare `{"target": …, "when": "<name>"}` naming a predicate registered
  with `@register_predicate("<name>")` (a pure function of state), beside the existing
  `{"target": …, "condition": <callable>}` form. `"default"` is built in and always matches — the
  named form of `lambda s: True`. An ordered list of `when` edges ending in `default` is a
  switch-case: first match wins, explicit default, and a non-terminal node with no match fails
  the run (as before).
- **A `when` naming an unregistered predicate fails the run** with the name in the reason — it
  never silently falls through. Rebinding a registered name to a different callable is refused.
- **The graph signature now includes the NAME of a named predicate.** Rename or reroute the
  decision gating a named edge and a run suspended under the old decision is quarantined on
  resume (`FLOW-GRAPH-SIGNATURE-1`'s documented blind spot, closed for named edges; callable
  predicates are still not hashed).
- **Migration note:** converting an existing edge from `condition` to `when` changes that flow's
  signature once, so runs suspended on it at upgrade time are quarantined. Migrate flows with
  parked runs behind a drain. The runtime's own flows (`AGENT_FLOW`, `NODUS_SCRIPT_FLOW`) are
  unchanged for exactly this reason; every existing flow's digest is byte-for-byte what it was.
- Recorded decision: `SwitchCaseEdgeGroup` (design §8 phase 3b) is not being added — the `when`
  list already has its semantics.
