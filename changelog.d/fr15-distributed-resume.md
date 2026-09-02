### Added — a scheduler resume can now cross a process boundary (`FR-15`, stage 2)

- A resume travels the distributed queue as **two identifiers** — the run id and its execution
  type — instead of a callback that cannot be serialised. The worker rebuilds it at the far end
  with the same call the restart-rehydration sweeps make on every boot.
- **No behaviour change yet.** `AINDY_ASYNC_SCHEDULER_DISPATCH` still refuses
  `EXECUTION_MODE=distributed`; lifting that refusal is a separate change, after a soak. This
  entry builds the path and proves it, in the same build-prove-flip order stage (a) used.
- **An unreconstructible resume is dead-lettered, not acknowledged.** That is the whole point:
  a worker treats an unresolvable message as *finished* — warn, ack, report success — so the
  previous behaviour would have stranded a run in `waiting` forever with nothing to retry and
  no dead-letter entry. The resume branch is checked **before** the JobLog lookup for the same
  reason: a resume legitimately has no JobLog, and reaching that lookup means falling into the
  silent-loss path.
- A resume whose claim is lost to another instance is a **success**, not a failure. The rebuilt
  callback performs its own atomic claim, so duplicate delivery is a no-op by design; failing
  those would fill the dead-letter queue with correctly-deduplicated messages.
- The descriptor carries no state — only identifiers. A payload holding a segment index or a
  step list would be a snapshot, and a snapshot can be stale by the time it is read.
