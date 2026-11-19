### Added — `await_event_for`: a Nodus wait that resumes at its own deadline (FR-55 (a)/(c), DEC-136) (#987)

- **New guest host function `await_event_for(event_type, schema, seconds)`.** A guest wait could end only on its
  event or in failure: the deadline failed the run with `WAIT_TIMEOUT`, so a workflow could not sleep between
  batches. It parks like `await_event` with its own deadline (1 second to 30 days), and when that passes the run is
  resumed and the call returns `{"timed_out": true, "deadline": …}`. A new leader-only scheduler job,
  `resume_deadline_waits` (every 15 s, the recovery lane), delivers it through the per-run resume path.
- **A per-wait deadline instead of the global 30 minutes** answers the per-workflow timeout ask without raising it
  for everyone. `await_event` is unchanged.

### Fixed — the stuck-run watchdog ignored a waiting run's own deadline (#987)

- It dead-lettered every run waiting longer than `FLOW_WAIT_TIMEOUT_MINUTES`, even one whose `wait_deadline` (from a
  flow's `wait_timeout_minutes`) was still ahead. It now leaves those, and the runs `await_event_for` resumes, alone.
