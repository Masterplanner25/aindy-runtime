### Fixed — memory routes no longer fail when an optional field is left out (FR-50, #783)

- `POST /memory/recall` with only `query`, or with only `tags`, and `POST /memory/nodes` without
  `node_type` all answered **400** (`Input validation failed for 'sys.v1.memory.read' / '.write'`).
  **Why it was wrong:** since these routes moved onto the syscall dispatcher (2026-08-16), they
  forwarded their optional fields as `null`, and the syscalls' input schemas type those fields
  (`string` / `list`), so the call was refused before it ran. The routes now leave an unset
  field out. A recall with neither `query` nor `tags` is still a 400.
