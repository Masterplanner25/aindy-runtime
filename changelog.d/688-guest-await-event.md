### Added — `await_event(event_type, schema)` is the Nodus guest wait; `nodus_builtins.py` removed (DEC-017, #688)

- A guest script suspends its flow with **`let payload = await_event("event.name", <schema or nil>)`**:
  on the first run the call sets the wait and halts the script exactly there; on the resumed run
  the same call returns the payload delivered by `POST /platform/flows/runs/{id}/resume`. Pass a
  schema (syscall dialect) to make the wait typed — a non-matching resume is refused with 422 —
  or `nil` for untyped. The three state keys (`nodus_wait_requested`, `nodus_wait_event_type`,
  `nodus_wait_resume_schema`) remain the wire contract and still work if set directly.
- **Why not `wait`:** it is a reserved nodus built-in and cannot be registered over.
- **Removed:** `AINDY.runtime.nodus_builtins` (the documented-but-never-wired `event.wait()` /
  `memory.*` namespaces) and `nodus_worker.WorkerWaitSignal`. Nothing imported either; the
  design they implemented — a host exception propagating out of the guest — is impossible on
  every nodus version (host exceptions are swallowed into the script result).
- Everything before `await_event()` runs again on resume; the script starts with an empty
  namespace plus `nodus_received_events` (DEC-012). Guard phase-1 effects with the
  `if (get_state("nodus_received_events") == nil)` branch unless they are mediated.
