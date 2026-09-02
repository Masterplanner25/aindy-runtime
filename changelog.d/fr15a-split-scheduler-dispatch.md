### Added — the scheduler's async dispatch is its own switch, and it is observable (`FR-15` (a))

- **New: `AINDY_ASYNC_SCHEDULER_DISPATCH`** (default `false`) — may the scheduler hand a drained
  item to the thread pool instead of running it on the 1-second heartbeat tick? This is the half
  of `FR-15` that fixes the actual defect: `schedule()` is the only queue drainer and runs each
  item synchronously, so one slow flow starves every other queued item along with wait expiry and
  stale-wait cleanup.
- **`AINDY_ASYNC_HEAVY_EXECUTION` is now route-facing only.** It still gates `POST /agents` and
  the nodus execute route, both of which answer `202` queued instead of a result when it is on.
  It no longer influences scheduler dispatch. **Nothing changes for anyone by default** — both
  flags remain off, and with the new one off the scheduler path is byte-identical to before.
- *Why split rather than flip:* one variable meant two unrelated things, so turning it on to fix a
  scheduler defect would also have changed two HTTP response shapes without anyone asking for it.
  Same repair as `EVENTBUS-PUBLISH-LATCH-1` and FR-17's `AINDY_ASYNC_JOB_LOOP_CLOSURE`.
- **New metric `aindy_execution_dispatch_total{mode, eu_type}`** — whether an execution ran inline
  or went to the pool. Nothing observed that decision before; an operator could only read an env
  var, which cannot distinguish "the async path is on" from "it is on and nothing uses it".

### Fixed — the scheduler can no longer be pointed at a transport that would discard its work

- `AINDY_ASYNC_SCHEDULER_DISPATCH` **has no effect under `EXECUTION_MODE=distributed`**, and
  refuses it before even an explicit opt-in. Setting it on a production overlay is a documented
  no-op, not an error.
- *Why an operator should care:* in distributed mode the async path does not submit to a thread
  pool — it serialises a job payload and **drops the callback**. The scheduler's work *is* a
  callback, and there is no `JobLog` row for a worker to recover it from, so the worker would log
  `JobLog not found`, **acknowledge the message, and report success** while the resume never ran.
  The flow would sit in `waiting` forever with no dead-letter entry and no retry. That is worse
  than the starvation being fixed, which at least eventually runs. `docker-compose.prod.yml` sets
  `EXECUTION_MODE: distributed`, so this is the default production shape.

### Changed — what CI proves about dispatch

- `tests/unit/test_scheduler_async_dispatch_gate.py` (25 tests) pins the gate precedence, the
  distributed refusal, and — for the first time — `_decide_mode`'s eight type×priority
  combinations. That decision, which the whole `FR-15` diagnosis rests on, previously had no test.
- `tests/integration/test_soak_scheduler_dispatch.py` proves the drainer is released while
  dispatched work is still running, that no item is lost or duplicated, and that concurrent
  resumes each obtain their own database session on live PostgreSQL. Both suites are
  mutation-tested 5/5.
