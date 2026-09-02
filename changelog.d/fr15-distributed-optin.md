### Added — distributed deployments can now opt in to async scheduler dispatch (`FR-15`)

- `AINDY_ASYNC_SCHEDULER_DISPATCH` **is no longer refused under `EXECUTION_MODE=distributed`.**
  A resume crosses the queue as a reconstructible descriptor rather than a closure the transport
  would drop, so the reason for the refusal is gone.
- **It is opt-in there, and does not inherit the thread-mode default.** Set it explicitly to
  enable it. Nothing changes for any existing deployment.
- *Why opt-in rather than on:* the transport is proven — a real one-node flow now resumes end to
  end over live Redis with nothing stubbed, an unreconstructible resume is dead-lettered, and
  duplicate delivery executes once. What is **not** proven is a separate worker *process*, the
  scheduler actually routing there, and cross-process concurrency. Every defect found on this
  path so far has lived at a process boundary, including the last one — a worker whose flow
  registry was empty. Defaulting it on would assert evidence nobody has.
- **What to watch after enabling:** `aindy_execution_dispatch_total{mode="async"}` should move,
  and the dead-letter queue should not. A resume that cannot be rebuilt is dead-lettered by
  design, so a DLQ entry is a signal to read rather than a silent loss.
- Thread-mode deployments are unaffected — that half shipped in 2.7.0 and its default is unchanged.
