### Added — a flow can declare how two writers resolve one state cell (`FLOW-PARALLEL-1`)

- Flow definitions accept `state_policies: {cell: {...}}`, declaring per-cell conflict
  resolution: `last_write_wins`, `reduce` (with a commutative, associative operator), or
  `barrier` (every named writer must have written).
- **Why this lands before fan-out exists.** The engine merged node output with
  `state.update(patch)` — last-write-wins, harmless only because plan steps are strictly
  sequential and there is never a second writer. The moment fan-out is added that silently
  becomes a *completion-order* race: two branches writing one cell yield whichever finished
  last, varying between runs and unreproducible from the record. Adding the policy afterwards
  costs far more, because by then flows exist that depend on the accidental ordering and each
  has to be audited to find out which.
- **An undeclared double-write raises rather than resolving.** The runtime does not pick a
  default: last-write-wins is right for a "latest reading" cell and wrong for a counter, and a
  silently-wrong merge produces a plausible value nobody checks. Branches writing *different*
  cells need no declaration.
- **Determinism is the property, not merging.** `last_write_wins` resolves in declaration order,
  never completion order; `reduce` accepts only commutative, associative operators, so its result
  does not depend on order at all — a non-commutative operator is refused rather than supported.
- **No behaviour change today.** With a single writer the merge is exactly `state.update(patch)`.
  It is wired onto the live path rather than beside it so the first fan-out is written against
  the real seam.
- `state_policies` is deliberately **not** part of the flow graph signature: `FLOW-GRAPH-SIGNATURE-1`
  hashes topology, not semantics, so editing a policy does not quarantine in-flight runs.
