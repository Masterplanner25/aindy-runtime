### Changed — fan-out branches run concurrently by default (#797)

**Operators: read before upgrading.** `AINDY_FLOW_FAN_OUT` now defaults ON. Only
`0/false/no/off` turn it off.

- **What changes.** The branches of a declared `FanOutEdgeGroup` run at the same time, up to
  `AINDY_FLOW_FAN_OUT_MAX_WIDTH` (default 4) across the whole process. Each running branch holds
  its own database connection.
- **What does not change.** Results: the flag affects timing only. Turned off, a group still runs
  its branches one at a time, in declaration order, and ends in the same state. A flow that
  declares no group is unaffected.
- **The evidence.** The flip rests on a real-Postgres soak (DEC-097), because no flow declares a
  group yet:
  - branches overlap on separate connections, and each branch's write commits;
  - an `any` join past a failed branch reads `partial` and names that branch;
  - four runs fanning out at once all complete within the width.

### Fixed — the flow runner no longer holds a transaction while its branches run (#797)

- The runner's session sat `idle in transaction` for a whole superstep. The branch pool is
  shared by the process, so that hold grew with every other run's queued branches.
- In the soak, one held connection was dropped and the run failed at the barrier with
  `PendingRollbackError`. The runner now commits before the branches start. It already commits
  after every node, so this commits the declaring node's work one step earlier.
