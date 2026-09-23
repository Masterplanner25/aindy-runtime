### Fixed — FR-15: a follower api no longer loses a woken resume; the worker opens its scheduler (#751; DEC-072)

- **Why it was wrong (silent loss #5):** under `EXECUTION_MODE=distributed` the background lease
  is contended, and an api that is not the leader runs no scheduler heartbeat — `schedule()` is
  never called there. It still registers waits and still answers `POST /platform/flows/runs/{id}/resume`,
  whose `notify_event` enqueued the woken resume into the api's in-memory queue, which nothing
  drained. `woken: true`, the run `waiting` forever, every counter flat. Now a process with no
  local drainer forwards the woken resume straight to the dispatcher (the same call `schedule()`
  makes; the async hint routes it to the durable queue). New counter
  `aindy_scheduler_resume_forwarded_total`. A leader keeps its queue; thread mode is untouched.
- **Silent loss #6:** the worker never marked its scheduler's rehydration complete, so every bus
  event it received was buffered and, at 1000, dropped. The worker now rehydrates waiting flow
  runs and opens its scheduler at startup, as the api's lifespan does.
- **Evidence, first time:** a resume parked on the api was executed by the worker process over
  Redis with the DLQ flat — on the new dev-host evidence topology `docker-compose.fr15-evidence.yml`
  (DEC-072: it mounts the host's docker socket; an instrument, never a deployment profile).
- Recorded, not fixed: `AINDY_SCHEMA_RECONCILE=true` on a blank database fails on FK order
  (use `bootstrap-schema`); `LOG_LEVEL=DEBUG` on the worker coincided with the nodus warm worker
  hanging on a trivial guest — suspected frame-channel corruption, unproven.
