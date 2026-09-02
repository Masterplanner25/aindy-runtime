### Fixed — a queued resume was discarded before it could be rebuilt (`FR-15`, stage 2 follow-up)

- The worker's resume branch sat **below** its JobLog claim guard. That guard returns "not
  claimed" when a job row is *missing*, and a resume has no JobLog by construction — so every
  resume was reported missing, **acknowledged**, and recorded as successfully completed
  without ever running. This is the same silent loss stage 2 was written to remove, arriving
  through a different door, and it was live for exactly one release-less window.
- The branch now runs before that guard. A resume is not left unclaimed by the move: it is
  claimed by the rebuilt callback's own atomic `UPDATE … WHERE status='waiting'` on the run
  row, which is the correct guard for one. The JobLog claim protects a different thing.
- **No operator impact** — `AINDY_ASYNC_SCHEDULER_DISPATCH` still refuses
  `EXECUTION_MODE=distributed`, so nothing enqueued a resume in the interim.
- *Why the existing tests missed it:* they called the resume helper directly, which starts
  past the line that was eating the message. The new test drives `process_one_job` — the real
  per-message entry point — with `_try_claim_job` left unstubbed, since stubbing it would
  remove the very guard that was swallowing the work.
