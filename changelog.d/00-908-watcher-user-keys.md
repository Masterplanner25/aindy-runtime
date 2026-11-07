### Added — per-user watcher keys (#908)

- `POST /watcher/keys` lets a signed-in user mint a key with the new `watcher.signals` scope, and `GET` and `DELETE /watcher/keys` list and revoke their own. It takes a session, so one key can't mint another.
- `/watcher/signals` now accepts that key in `X-Platform-Key`. The signals belong to the key's owner, and naming another user answers 403 `TENANT_VIOLATION`.
- A key holding only `watcher.signals` works on the watcher routes and nowhere else, because it lives on a user's machine and any user can mint one. It is refused everywhere a platform key is resolved, including MCP.
- **Action for operators:** `X-API-Key` still works and can still name any user, so it stays a service credential. If a user's machine holds the deployment's `AINDY_API_KEY` for the watcher, give that user a key from `/watcher/keys` and rotate the shared one. The client needs the matching `aindy-sdk` release to send the new header.
