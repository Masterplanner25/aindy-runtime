### Fixed — an agent-run resume served by a process that did not park the run now wakes it, and never answers `resuming` for nothing (FR-44, #760)

- `POST …/agent/runs/{id}/resume` published the run's wait event and returned. A run's wait
  is registered in memory, by the process that parked it, and re-armed only at boot. So a
  resume served by any other process (a second api instance, a worker, an api that booted
  before the park) reached 0 waiters. For an authority-gate decision it still answered
  `run_status: "resuming"`, and the run stayed `waiting` until something restarted. The app
  observed exactly that: 200, `waiters_notified: 0`, three minutes `waiting`, then `completed`
  11 s after an api restart.
- The route now arms the run's wait on the serving process before publishing, if that process
  holds none. It reuses boot rehydration, scoped to the one run. If another process still holds
  the wait, the second registration is safe: the resume callback's `waiting → executing` claim is
  atomic, so exactly one re-drive runs. An `abort` arms nothing.
- **`authority_gate.run_status` is `"resuming"` only when a waiter on this process was woken.**
  Otherwise it is `"waiting"`, with `reason: "no_local_waiter_woken"`. The decision stays recorded
  on the step row and replays on the next re-drive. `waiters_notified` still counts local wakes
  only, as it always has.
- **Also fixed:** `rehydrate_waiting_agent_runs(db, run_ids=[…])` raised on string ids in the
  UUID column. Boot calls it unscoped, so the scoped form had never run.
