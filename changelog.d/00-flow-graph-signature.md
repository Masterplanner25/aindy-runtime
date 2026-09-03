### Added — a suspended run can no longer resume into a changed flow (`FLOW-GRAPH-SIGNATURE-1`)

**★ This release changes the schema.** One additive, nullable column — `flow_runs.graph_signature`
(Alembic `0018`). Per `FR-14`, an additive runtime column makes a bare `bootstrap-schema` exit
**3**; under `set -e` with `restart: unless-stopped` that is a crash loop, not a warning. Existing
deployments must run `aindy-runtime bootstrap-schema --reconcile`, or branch on exit code 3.

- A `FlowRun` was restored against whatever definition `register_flows()` produced *that* boot.
  Nothing recorded what the run was planned against, so a node renamed or an edge rerouted
  between suspend and resume executed against a definition the run was never planned for —
  **silently, and reported as success.**
- A run now records a hash of its flow's **shape** at start; a resume compares it and
  **quarantines the run** (`status="dead_letter"`, with a reason) instead of executing it. The
  check runs before the claim, so a quarantined run is not left stranded in `executing`.
- **Why this matters more than when it was filed:** the recent `FR-15` work made a resume a
  durable queue message rather than an in-process closure, so one can now sit in a queue **across
  a deploy** and be picked up by a worker running different code.

**What the signature covers, because that is the whole design.** Node identities and edge
topology: the start node, the terminal set, every edge source, each source's targets *in order*
(the runtime takes the first matching edge, so order is meaning), and *whether* an edge is
predicate-gated.

**What it deliberately excludes:** node bodies, `node_configs`, and predicate implementations
*and their names*. A hash that moved on every deploy would quarantine every in-flight run and be
switched off within a week.

**The blind spot, stated rather than left to be discovered:** a changed predicate that reroutes
control flow is **not** caught. This detects a moved graph, not a changed decision.

**Nothing is quarantined on upgrade.** An absent signature means "cannot tell" and proceeds
exactly as before — runs created before this column, and flows no longer registered, are
unaffected. A conflict requires two known signatures that differ.
