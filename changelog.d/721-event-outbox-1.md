### Changed — `EVENT-OUTBOX-1`: inside a request, a queued system event rides the handler's transaction; a handler that raises is rolled back (#721; DEC-060..062)

- **Events:** `queue_system_event` inside a pipeline now adds the `system_events` row to the
  handler's own session and never commits it itself; it rides whatever the handler commits next
  (the FR-30 execution-unit finalize is the last commit on every request). Before, the event was
  a dict in memory, written after the handler on its own commit under a swallowing `try` — a crash
  between the handler's commit and that flush kept the work and lost the record of it. The id
  returned is the row's id (it always was a client-side uuid). The post-handler pass still runs the
  derived effects (internal handlers, feedback signals, memory capture, webhooks, scheduler wake)
  once per event. Non-request paths (scheduler, worker, callbacks) are unchanged — they already
  wrote on the caller's session.
- **★ Behaviour change to read before upgrading — a handler that RAISES is now rolled back.**
  The pipeline rolls the request session back before recording `execution.failed`. Previously the
  failure event's own commit landed the handler's pending, uncommitted writes as a side effect; a
  route that raised (an `HTTPException` included) after writing without committing would keep
  those writes. It no longer does. A handler that *returned* is never rolled back, whatever the
  post-handler machinery does. Recorded on the envelope as side effect `handler.rollback`.
- The request's execution-unit row is committed where it is created (it used to ride the next
  commit), so the finalize that follows a rollback still finds it.
- **Test-suite rule** (for app-side suites that reuse the shared-session fixture shape): the app's
  rollback under a shared outer transaction erases the test's own fixture rows — a route that
  answers 4xx/5xx must be tested on a private engine (`tests/fixtures/db.py::build_private_engine`).
