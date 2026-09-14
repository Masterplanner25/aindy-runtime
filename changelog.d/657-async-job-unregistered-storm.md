### Fixed — an async job whose handler is not registered fails once instead of re-dispatching forever (`ASYNC-JOB-UNREGISTERED-STORM-1`) (#657)

A `JobLog` row naming a handler that no loaded process registered — a runtime booted without
its app, a renamed handler, a rolled-back deploy — was re-dispatched in a tight in-process
loop: **~87 attempts per second per job, uncounted, forever** (45,000 log lines in ten minutes
from two rows, observed live). Three faults stacked: the "not registered" `raise` came before
`attempt_count += 1`, so `max_attempts` (default 1) never bound it; the increment was only ever
committed as a side effect of the started-event emit, and the except branch's `rollback()`
discarded it otherwise; and the thread-mode retry was an immediate executor submit with no
delay — only the distributed queue path honoured a backoff.

- **An unregistered handler is terminal.** `_execute_job_inline` raises
  `AsyncJobHandlerNotRegistered` (a `RuntimeError` subclass — existing catches still hold) and
  the retry branch refuses it whatever `max_attempts` says. The row ends `failed` after exactly
  one attempt with `attempt_count=1` and the message in `error_message`.
- **Every attempt is counted.** The attempt is numbered before the handler lookup and restored
  after the except branch's rollback, so a registered handler that fails without a started event
  is no longer uncounted either.
- **Thread-mode retries back off.** A retryable failure with budget left is re-dispatched after
  the dispatcher's existing exponential delay (`AINDY_RETRY_BACKOFF_BASE_MS`, default 1000 ms,
  doubling per attempt, capped by `AINDY_RETRY_BACKOFF_MAX_MS`, default 30000 ms) via a daemon
  `Timer` — the same curve the distributed path already applied through `enqueue_delayed`.
  `AINDY_RETRY_BACKOFF_BASE_MS=0` restores the old immediacy.
- **Operator-visible:** a terminal failure logs one `failed TERMINALLY (not retried)` WARNING;
  a retry logs `rescheduling with backoff` and the delay. Rows already storming on a running
  2.13.0 stop on the first attempt after upgrade (boot recovery re-dispatches `pending` rows
  once; they now fail). `docs/runtime/RETRY_POLICY.md`'s async-job section is corrected — it
  described the pre-fix loop as if it already counted before the handler ran.
- Unit tests drive `_execute_job_inline` against a real `JobLog` on a private SQLite engine;
  mutation-checked 4/4. Not re-run live.
