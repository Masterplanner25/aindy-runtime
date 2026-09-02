### Changed — the scheduler no longer runs drained work on its own heartbeat (`FR-15` (a), thread mode)

- **`AINDY_ASYNC_SCHEDULER_DISPATCH` now defaults to `true`.** A queued item is handed to the
  thread pool instead of being executed inside the 1-second scheduler tick, so one slow flow no
  longer blocks every other queued item — or wait expiry, or stale-wait cleanup, which share that
  tick. Set `AINDY_ASYNC_SCHEDULER_DISPATCH=0` to restore the previous behaviour.
- *Evidence:* `tests/integration/test_soak_scheduler_dispatch.py` on live PostgreSQL — the drainer
  is released while dispatched work is still running, nothing is lost or duplicated, and
  concurrent resumes each obtain their own database session.
- **★ This reaches thread-mode deployments only, and that is the honest scope rather than a
  caveat.** The setting has no effect under `EXECUTION_MODE=distributed`, which
  `docker-compose.prod.yml` sets, because the distributed transport cannot carry the scheduler's
  resume callback. **If you run the production overlay, nothing about your dispatch behaviour
  changes and the serialisation described in `FR-15` is still present.** Fixing it there requires
  reconstructing the resume from `run_id` rather than carrying a closure — a build, still open.
- `aindy_execution_dispatch_total{mode="async"}` is how you confirm which behaviour a deployment
  actually has; reading the environment variable cannot tell you, since distributed mode overrides it.
