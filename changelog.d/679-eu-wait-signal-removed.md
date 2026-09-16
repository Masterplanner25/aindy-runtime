### Removed — `ExecutionWaitSignal`; a request's execution unit can never enter `waiting` (`EU-WAIT-SIGNAL-DEAD-1`, #679)

- `AINDY.core.execution_gate.ExecutionWaitSignal` is gone, with the pipeline branches that
  honoured it. It promised that a route handler could park the *request's* execution unit to
  be resumed later — unfulfillable by construction (a route has already answered its client;
  nothing re-executes a request), raised by nothing in any repo, and its resume callback
  rolled back on session close, so a parked unit stayed `waiting` forever. Importing the name
  now fails; no consumer did.
- Behaviour: a request's `execution_units` row always reaches `completed`/`failed` when its
  handler returns, whatever shape the handler returned. The pipeline no longer emits
  `execution.waiting` or sets `metadata.eu_wait_for` (both were reachable only via the signal).
- **Operators:** any `execution_units` row of a route type still in `waiting` predates this
  and is unreachable by any path — including the ~10 the app kept as FR-29 evidence. The
  retire `UPDATE` offered in the 2.16.0 handoff applies to them.
