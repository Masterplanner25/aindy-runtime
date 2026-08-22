### Fixed — async jobs now record that they started and finished (FR-17, #518)

`emit_system_event` refuses any `execution.*` event emitted with neither an execution pipeline
nor the async-execution context active. Two async-job sites tripped that guard, and because the
emitter catches and logs, the rows simply never existed:

- **Submission.** `submit_async_job` emits the job's root `execution.started`. Submissions that
  come from a route have a pipeline and were fine; submissions from a scheduler tick, the
  event-bus subscriber thread or an app `bootstrap.py` have none, and were dropped with
  `WARNING [AsyncJob] … ExecutionContract violation`. Reported by the app team on a live stack.
- **The worker thread.** `_execute_job_inline` activated that context only when
  `AINDY_ASYNC_JOB_LOOP_CLOSURE` was set — **off by default** — so with the default settings
  *every* async job's `execution.completed` / `execution.failed` was discarded. Async traces
  started and never ended.

Both now declare the async-execution boundary, via a new `async_execution_scope()` context
manager. The events were not renamed to a non-`execution.*` type the way `scheduler.queued` was:
`_ensure_root_execution_event_id` and `_has_existing_execution_started` locate an async job's
trace root by `type == execution.started`, so a rename would trade a missing row for a broken
trace root.

**Changed meaning of `AINDY_ASYNC_JOB_LOOP_CLOSURE`.** It no longer decides whether an async
job's execution events are recorded — only whether each job emits a `SCORE_COMPUTED` record and
joins the Infinity loop, which is what its name says. One flag was carrying both an operator
preference and a runtime latch; that is the shape `EVENTBUS-PUBLISH-LATCH-1` was split to avoid.

**What operators will see:** more rows in `system_events` for async jobs — one `execution.started`
per submission that previously produced none, and a terminal `execution.*` per job where the
default previously produced none. Memory capture follows persistence exactly as it already did
for route-driven submissions; `RUNTIME_INTERNAL_TASK_NAMES` still refuses to capture the
runtime's own maintenance jobs, so the RT-MEMTXN-LEAK-1 cycle stays cut.
