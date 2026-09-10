### Added — declared fan-out in the flow engine, default-off (#616)

`FLOW-PARALLEL-1` phase 1. **Off by default**; set `AINDY_FLOW_FAN_OUT=1` to run a declared
group's branches concurrently. Phase 0 (#603) widened the transaction — ordinals allocated per
superstep at a barrier, the merge moved out of per-node status handling — and both already took a
list of one. This is what lengthens it.

A flow can now declare `FanOutEdgeGroup(["b", "c", "d"])` as a node's successors. The group runs
as **one superstep**: branches execute in declaration order, each on **its own database session**,
their patches merge centrally on the runner's session, and the whole group gets one contiguous
block of `FlowHistory` ordinals allocated at the barrier.

- **The flag gates concurrency, not semantics.** With it off, a declared group runs its branches
  sequentially in declaration order — same patches, same merge, same ordinals, same history. Only
  the timing differs, so turning it off can never change what a flow computes.
- **Width is bounded process-wide**, not per flow run. Runners are created from request handlers,
  syscall dispatch, rehydration and scheduler recovery, so a per-run bound would allow
  *runs × width* concurrent sessions against a connection budget shared with request handling.
  One shared pool, defaulting to 4, overridable with `AINDY_FLOW_FAN_OUT_MAX_WIDTH`.
- **`WAIT` inside a group is refused**, loudly and by decision. Holding the other branches'
  patches across a suspension would need a durable partial-superstep record that does not exist.
- **Branches must converge on the same successor**, enforced rather than chosen. Declared join
  policies (`all`, `any`, `quorum(k)`) are phase 2; until then a failed branch fails the superstep.

**No existing flow changes.** A flow that declares no group resolves a frontier of one and takes
exactly the previous code path. **Graph signatures of existing flows are unchanged** — verified
against the live `AGENT_FLOW`, `NODUS_SCRIPT_FLOW` and `NODUS_COMPILE_AND_RUN_FLOW` digests, so no
suspended run quarantines on upgrade. Adding a group to a flow *does* change that flow's
signature, which is correct: it is a topology change, and a run planned against the sequential
shape must quarantine.

No schema change and no migration: a superstep is N existing `FlowHistory` rows, not a new table.
