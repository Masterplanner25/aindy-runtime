### Fixed — a runtime syscall handler that fails no longer rolls back its caller's session (DEC-124, FR-64, #933)

- When a write handler (`memory.write`, `memory.link`, `memory.delete`, `event.emit`, `agent.ensure_initial_run`) ran on a session its caller passed in (`metadata["_db"]`, as flow nodes and the memory routes do) and failed, it rolled back the caller's whole session. That discarded whatever the caller had not committed yet, against the handler's own contract. Under a test fixture that holds the app's and the test's sessions in one outer transaction it also erased the caller's `FlowRun`, which is how FR-64 was found.
- Now the handler puts its work in a savepoint and rolls back only that. Success is unchanged.
- **Still rolls back everything:** an app handler that calls `rollback()` on the session itself, and `MemoryNodeDAO.save()` on a database error. A savepoint can't stop a session-level rollback.
