### Changed — an MCP call made with a platform key is held to the key's scopes (DEC-126, #PR)

- **Read before upgrading if you run `mcp-server` multi-tenant** (`AINDY_MCP_SERVER_MULTI_TENANT=true`) and callers use
  `X-Platform-Key`. Each call's tool must now be one the key's scopes authorize, by the map `POST /platform/syscall`
  already used (`memory.read` with `memory.read` or `memory.write`; `memory.write` with `memory.write`; …).
  `platform.admin` authorizes everything, and a capability the map doesn't list needs it.
- **Why it was wrong:** the key's owner was resolved and its scopes ignored, so a key scoped for something else could
  call any exposed tool, and the same key got different answers from MCP and `/platform/syscall`.
- A refused call returns `failure_class: permission`. A key that worked before and is now refused needs the scope added,
  or a new key minted with it.
- Session JWTs and the single configured identity (stdio, or single-identity SSE) are unchanged.

### Added — `aindy-runtime mcp-server --transport http`: streamable HTTP at `/mcp` (FR-66 phase 1, #PR)

- The transport ChatGPT and claude.ai connect to. It has the same identity modes and scope rule as SSE, and is stateless,
  so it needs no sticky sessions. Built on nodus-mcp 0.1.6's `streamable_http_app()` (DEC-125); the `[mcp]` extra now
  requires `nodus-mcp>=0.1.6`.
- **It checks `Host` and `Origin` and fails closed (DEC-127):**
  - It binds `127.0.0.1` by default, where only loopback names are accepted.
  - Any other bind refuses to start unless `AINDY_MCP_SERVER_ALLOWED_HOSTS` names the public host.
  - `AINDY_MCP_SERVER_ALLOWED_ORIGINS` lists browser origins.
- **ChatGPT and claude.ai still can't connect:** their connectors send no header, so they need OAuth, which is phase 2.
  A client that can send a header (Claude Code, the SDKs) can use it now.
- **CI fails instead of skipping when the MCP stack doesn't import.** `Runtime Contracts` sets `AINDY_REQUIRE_MCP=1`,
  and `test_mcp_stack_required.py` then fails if the stack is missing. Before this, eight MCP test files could skip
  together while the job stayed green.
