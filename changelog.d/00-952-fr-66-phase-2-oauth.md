### Added — OAuth for the MCP endpoint, so ChatGPT and claude.ai can add the server (FR-66 phase 2, DEC-129, #952)

- **Schema step: Alembic `0022`** adds `mcp_oauth_clients` and `mcp_oauth_grants`. Schema contract `2026-11-09`.
  Nothing reads them until `AINDY_MCP_OAUTH` is on.
- With `AINDY_MCP_OAUTH=on` and `AINDY_MCP_PUBLIC_URL=https://…`, `mcp-server --transport http` is its own OAuth
  authorization server for `/mcp`:
  - discovery from a `401`, dynamic client registration, `/authorize` with PKCE S256, `/token`, `/revoke`;
  - a server-rendered sign-in and consent page at `/oauth/consent`, which never creates an account.
- **Tokens:**
  - An access token is a 1-hour JWT under its own derived key, never valid as a session token, and held to its scopes
    like a platform key (DEC-126).
  - A refresh token lasts 30 days and rotates on every use; replaying an old one ends the grant.
  - A password change, a disabled account, or ending the grant stops them.
  - Codes and refresh tokens are stored hashed; client secrets are stored sealed.
- **Users see and end their connected apps** at `GET` / `DELETE /auth/mcp-grants`, from a signed-in session.
- **Registration is open but capped** (`AINDY_MCP_OAUTH_MAX_CLIENTS`, `AINDY_MCP_OAUTH_REGISTRATIONS_PER_HOUR`).
  `AINDY_MCP_OAUTH_CLIENTS=closed` turns it off; then register clients with
  `aindy-runtime mcp-oauth register-client`.
- **With OAuth on, `/mcp` accepts only its own access tokens** (DEC-130). Header keys still work on SSE, or over HTTP
  with OAuth off.
- `key_scopes_authorize` takes `exact_capability=` (default off): an OAuth grant may name a tool's capability as its
  scope when no runtime scope covers it. Platform keys are unchanged.
- FR-66 stays open until a real claude.ai connector and a real ChatGPT connector each read a memory node (D7).
