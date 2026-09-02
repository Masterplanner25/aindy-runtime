### Fixed — distributed dispatch no longer silently discards work it cannot reconstruct

- `dispatch()` now raises `UndistributableWorkError` when work is routed to the distributed
  queue with no `log_id` in its context, instead of enqueueing a payload keyed on an id no
  worker can resolve. The refusal happens **before** anything is pushed.
- *Why this mattered:* `dispatch()` takes a callback. Under `EXECUTION_MODE=distributed` that
  callback is never carried — the runtime sends a job payload and the worker rebuilds the work
  by resolving `log_id` against `JobLog`. Without one, the old code manufactured an id
  (`... or eu_id or uuid4()`) precisely when there was none to use, and a worker treats an
  unresolvable job as **finished**: it logs `JobLog not found`, acknowledges the message rather
  than dead-lettering it, and reports success. The work vanished with no dead-letter entry, no
  retry, and no failed status — every observable signal said it completed.
- **No operator action, and nothing that works today is refused.** All three live job-submission
  paths pass `log_id`; that was the entire guarantee, and it was an accident rather than a check.
  The guard makes it a rule so the next caller to omit one gets an error at the call site instead
  of a silent permanent loss.
- Found while scoping `FR-15 (a)`, which would have been the first caller to hit it. Filed and
  fixed separately because it is a property of `dispatch()`, not of the scheduler.
