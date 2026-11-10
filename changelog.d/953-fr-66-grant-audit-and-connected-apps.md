### Added — MCP grant audit events and a Connected Apps panel (FR-66) (#953)

- **Each MCP OAuth grant is now recorded when it is made and when it ends.** Before this, a grant left only log lines, so nothing durable showed who connected which app, or why a connection ended.
  - `mcp_grant.created` is written on consent.
  - `mcp_grant.revoked` is written when a grant ends, with a `reason`: `user`, `client`, `superseded`, `refresh_replay` or `account_changed`.
  - The payloads hold ids, the client, scopes and the reason. They never hold a code, a token or a secret.
- **The platform console has a Connected Apps panel** (`/platform/connected-apps`). It lists the signed-in user's connected MCP apps and can end one, which stops its tokens within seconds. Until now, a user could see or end a connection only by calling `/auth/mcp-grants` directly.
