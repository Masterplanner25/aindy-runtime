### Fixed — reading a waiting run no longer parks the reader's execution unit (`WAIT-DETECT-SHAPE-1`, app FR-29) (#670)

Filed by the app team from their live 2.14.0 run of Tutorial 2. The execution pipeline treated
**any** handler result dict whose `status` upper-cased to `WAITING` as *the request itself*
waiting: it parked the request's `execution_units` row, registered a scheduler wait under that
row's id, and emitted `execution.waiting`. Two things return such a dict, and neither is the
request waiting:

- **A read.** `GET /platform/flows/runs/{id}` of a parked run, on a server where a consumer has
  registered a result key for `flow_run_get` (the app does), returns the bare run row — whose
  own `status: "waiting"` parked the *reader*. Eight reads, eight units in `waiting` forever,
  each with a `[Scheduler] waiting backup write failed … ForeignKeyViolation` WARNING because
  the scheduler was handed a unit id as a `waiting_flow_runs.run_id`. The platform-only server
  nests the row under `flow_run_get_result`, which is why the runtime's own live run never
  saw it.
- **A start.** `POST /platform/nodus/run` on a script that suspends returns an execution record
  with top-level `status: "WAITING"` and `waiting_for` nested under `data` — so the detector
  parked the request's unit on the literal event `"unknown"`, which nothing emits. This was
  the case the branch was written for, and it never resumed a unit: nothing re-executes a
  returned request. Pre-existing on every release; not a 2.14.0 regression. Almost certainly
  the 105 `job|route` / `flow|route` units left stuck by the 2026-09-13 tutorial run.

- **Only an explicit `ExecutionWaitSignal` (raised or returned) parks a request's unit now.**
  A request's unit describes the request: when the handler returns, it completes. The run it
  read or started carries its own wait on `flow_runs` and its own unit (#656). The handler's
  result is untouched — `data.status == "WAITING"` still reaches the caller.
- **The scheduler's `waiting_flow_runs` backup write skips an id that is not a flow run** (a
  DEBUG line, not a WARNING that reads like data loss). Still reachable by an
  `ExecutionWaitSignal` raised from a `flow.*` route.
- **Consumer-visible:** the pipeline envelope of `POST /platform/nodus/run` (and any route
  returning a WAITING record) now says `status: "success"` with `data.status: "WAITING"`,
  where it said `status: "waiting"` and `metadata.eu_wait_for: "unknown"`; its trace carries
  `execution.completed`, not `execution.waiting`. Tutorial 2's causal-graph listing and
  `EXECUTION_CONTRACT.md` are updated. Consumers that read `data.status` (the app's routers
  do) are unaffected. Dashboards counting `execution_units` by `status` stop inflating
  `waiting` by one row per such request.
- Unit tests call the routes through the booted app on both server shapes and read back the
  unit the envelope names; the suspending-script case drives a real Nodus script with the
  scheduler spied (exactly one `register_wait`, the run's). Mutation-checked 3/3 — the third
  survived a first draft whose signal control only *raised* the signal, which never reaches the
  detector. **Not re-run live.**
