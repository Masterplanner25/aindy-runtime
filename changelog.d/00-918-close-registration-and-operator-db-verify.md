### Added — registration can be closed: `AINDY_REGISTRATION` (FR-57, #918)

- **`AINDY_REGISTRATION=closed`** stops `POST /auth/register` from creating accounts. It answers exactly as an open register does, with the same 202 and body after the same hashing cost, and creates and sends nothing, so it reveals nothing about which addresses have accounts. `open` is the default. Any other value counts as `closed`, and an empty value means the default. Existing accounts, login and password reset are unchanged. Invite-only registration isn't built.

### Changed — `GET /db/verify` needs an operator (FR-58, #918)

- **Action for operators:** the whole database schema used to be readable with only the shared `AINDY_API_KEY`. It now needs an admin session or a `platform.admin` key, and a shared-key-only caller gets 401. The check is on the router, so it also covers an app that mounts it at its root.

### Added — trace helpers can be scoped to one user (FR-60, #918)

- `build_trace_graph`, `detect_root_event`, `detect_terminal_events` and `calculate_trace_span` take an optional `user_id`. With it, they keep only that user's events and memory nodes, and the edges whose both ends remain. `/observability/execution_graph` passes the caller to every helper that accepts it, so the app's own helpers still work until they add the parameter. Nothing today puts two users' events in one trace, so this is defence in depth.
