---
title: "MCP Integration (client-side)"
api_version: "1.0"
last_verified: "2026-11-09"
status: current
owner: "platform-team"
---

# MCP Integration

ECOGAP-4 / G4b. Lets AINDY agents call tools hosted on external **MCP (Model Context
Protocol)** servers, by discovering those tools at startup and registering each as a
normal AINDY agent tool. The MCP wire protocol lives in the published `nodus-mcp`
package; the runtime only wires it in.

**Status:** both directions shipped, opt-in.
- **Client-side** — AINDY agents call *out* to external MCP tools (below).
- **Server-side** — expose AINDY syscalls *as* an MCP server to Claude Desktop etc. over
  stdio or SSE, with opt-in per-session multi-tenant identity over SSE (MEB-3a)
  ([below](#server-side--expose-aindy-syscalls-as-mcp)). Only the EffectRecord attribution
  schema bump (MEB-3b) is deferred — see [TECH_DEBT ECOGAP-4](../../TECH_DEBT.md).

## Enable it

1. Install the extra:
   ```bash
   pip install "aindy-runtime[mcp]"
   ```
   (`mcp` is pinned explicitly here because `nodus-mcp` treats the official SDK as
   optional and does not pull it in, but the SSE client transport requires it.)

   > **The extra no longer caps the SDK** (since #727). From 2026-07-31 it held
   > `mcp<2` because `nodus-mcp` 0.1.2/0.1.3 called `Server.list_tools()`, which `mcp 2.0.0`
   > removed; `nodus-mcp 0.1.4` branches per SDK major at import and the live round-trip
   > test passes under both `mcp 1.27` and `2.2`. If you install `mcp` yourself, any
   > version `>=1.0.0` works. History: [TECH_DEBT MCP-SDK-2X-1](../../TECH_DEBT.md).
2. Configure servers and turn it on:
   ```bash
   AINDY_MCP_CLIENT_ENABLED=true
   AINDY_MCP_SERVERS=[{"name":"fs","url":"http://localhost:8080/sse","risk":"high"}]
   ```
3. Restart. On first tool load the runtime connects to each server, lists its tools, and
   registers them as `mcp_<server>_<tool>` (e.g. `mcp_fs_read_file`).

Off by default — outbound MCP egress is a deliberate operator choice.

## How it works

- `AINDY.platform_layer.mcp_client.bootstrap()` runs (memoized, once per process) from
  `tool_registry._ensure_tools_loaded` — the one entry point that runs in every
  tool-executing process, including the nodus_worker subprocess — so MCP tools resolve
  wherever `execute_tool` runs. It is a **no-op unless enabled** and boot-safe (any
  failure — an unreachable server, bad config — is logged and swallowed; it does not even
  import `nodus-mcp` when disabled). It is wired here rather than via a plugin manifest
  entry because the runtime `platform-only` profile must stay manifest-empty (the "runtime
  boots clean without plugins" contract). Discovery runs once per process; in the
  per-execution nodus_worker subprocess that is once per spawn — a known cost tied to
  NODUS-WARMPOOL-1, incurred only when MCP is enabled.
- When enabled, each remote MCP tool is registered via `register_tool` into `TOOL_REGISTRY`
  — the **executable** tool path (`register_agent_tool`'s `_agent_tools` surface is
  discovery-only and is not run by `execute_tool`).
- Registered tools carry capability **`outbound.mcp`** and default **risk `high`**, so agent
  runs must be granted that capability and (per risk policy) approved. This is also the seam
  G4a will gate once mediated egress is activated.
- MCP clients are async but `execute_tool` is synchronous; all async work runs on one
  dedicated background event loop (`mcp_client._run_sync`), which is safe whether or not the
  caller is already inside a running loop. v1 connects per call; a persistent per-server
  connection pool is a deferred optimization.

## Tool naming and collisions

Tools are namespaced `mcp_<server>_<remote-tool-name>`; the `name` in each `AINDY_MCP_SERVERS`
entry must be unique. This keeps external tools from colliding with runtime-native tools.

## Verification

A live round-trip (real SSE `NodusServer` → discovery → tool call through the sync bridge)
is exercised in development; the unit suite (`tests/unit/test_mcp_client.py`) covers
registration shape, the bridge, resilience, and the disabled no-op with `nodus-mcp` mocked.

## Server-side — expose AINDY syscalls as MCP

Run AINDY as an MCP **server** so an external MCP client (Claude Desktop, etc.) can call
AINDY syscalls as tools. It's a standalone process the client spawns over stdio:

```bash
pip install "aindy-runtime[mcp]"
AINDY_MCP_SERVER_USER_ID=<a-registered-user-id> \
DATABASE_URL=postgresql://... \
aindy-runtime mcp-server --transport stdio
```

Claude Desktop config (`claude_desktop_config.json`) points `command`/`args` at that.

- **Single configured identity (default).** Every external call runs as
  `AINDY_MCP_SERVER_USER_ID`. Correct for the canonical local single-operator case (Claude
  Desktop on your machine). For per-session multi-tenant identity, see below.
- **Read-only by default.** Exposes `memory.read/search/list/tree/trace`. Set
  `AINDY_MCP_SERVER_ALLOW_WRITES=true` to also expose `memory.write`, `memory.delete`,
  `flow.run`, `event.emit`. `AINDY_MCP_SERVER_TOOLS` overrides the allowlist explicitly.
- **An explicit list cannot bring in a write without that flag (DEC-128).** A syscall counts as
  a read only when it is registered with `read_only=True`; the server refuses to start naming
  any other. An app syscall that reads must declare it: `register_syscall(..., read_only=True)`.
- **How it dispatches.** Each exposed syscall becomes an MCP tool whose handler calls
  `dispatch_syscall(name, args, user_id=<identity>)` — which grants the syscall its own
  capability (least-privilege, SDK-SYSCALL-GRANT-1) and manages its own DB session. The
  allowlist is the gate. Verified end-to-end on Postgres (write → read-back through the tool
  handlers).
- **App syscalls (FR-53 (a)).** Before it builds its tool registry the server runs
  `load_plugins()` for the manifest selected exactly as at boot (`AINDY_PLUGIN_MANIFEST` /
  `AINDY_BOOT_PROFILE`), so an app syscall named in `AINDY_MCP_SERVER_TOOLS` is served. Plugin
  output goes to stderr while they load, because stdout is the stdio channel. A plugin that fails
  to load stops the server (DEC-101), as it stops the API server's boot. Runtime-only selects an
  empty manifest. **Serving runtime tools only?** Run the MCP process with
  `AINDY_BOOT_PROFILE=platform-only` and no plugin loads. With a legacy flat `{"plugins": […]}`
  manifest, which has no profiles, leave `AINDY_PLUGIN_MANIFEST` unset instead.
- **A `user_id` argument may only repeat the caller (FR-53 (b), DEC-099).** The outside AI writes
  the tool arguments. A `user_id` among them that differs from the call's identity is refused
  (`TENANT_VIOLATION`, `failure_class: permission`) before dispatch and before an execution unit is
  claimed. An absent one is filled with the caller's, so handlers that require the key work.
  This holds for any allowlisted syscall, including app handlers that read `payload["user_id"]`.
- **A tool is granted the capability its syscall declares, and nothing else (DEC-112).** The
  allowlist decides what is exposed, so naming a syscall in `AINDY_MCP_SERVER_TOOLS` is enough
  to make it callable, whatever its capability is called. Before DEC-112 the grant was inferred
  from the name, which refused `memory.search`, `memory.tree`, `memory.trace` and
  `memory.delete`. Other callers of `dispatch_syscall` without `capability=` still infer it
  (`register_syscall`'s docstring). Tools appear as `nodus_<domain>_<action>`; that prefix comes
  from `nodus-mcp`'s `syscall_entry_to_tool`.

### SSE transport + per-session multi-tenant (MEB-3a)

Serve over HTTP instead of stdio, and optionally resolve a distinct identity per session:

```bash
AINDY_MCP_SERVER_MULTI_TENANT=true \
DATABASE_URL=postgresql://... \
aindy-runtime mcp-server --transport sse --host 0.0.0.0 --port 8080
```

- **Per-session identity.** With `AINDY_MCP_SERVER_MULTI_TENANT=true`, an `auth_hook` resolves
  each session's `Authorization: Bearer <jwt>` or `X-Platform-Key` header to a real user via the
  runtime's existing auth surface (`decode_access_token` / platform-key — no new mechanism), and
  every call dispatches as *that* identity. The syscall dispatcher then enforces per-syscall
  capability + tenant isolation for that user.
- **A platform key is held to its scopes (DEC-126).** The tool's declared capability must be
  one the key's scopes authorize, by the same map `POST /platform/syscall` uses
  (`auth/api_key_auth.py::DISPATCH_CAPABILITY_SCOPES`): `memory.read` with `memory.read` or
  `memory.write`, and so on. `platform.admin` authorizes everything, and a capability the map
  does not list needs it. Otherwise the call is refused with `failure_class: permission`
  before dispatch. A session JWT is not scope-gated, as on that route. A watcher-only key is
  refused outright (DEC-117).
- **Fail-closed.** A call whose headers resolve to no valid identity is denied. Writes still
  require `AINDY_MCP_SERVER_ALLOW_WRITES=true`.
- **stdio stays single-identity.** Multi-tenant is meaningful only over SSE or HTTP (stdio
  carries no per-request headers); the server refuses to start stdio with the flag on. Without
  the flag, SSE and HTTP run as the single configured `AINDY_MCP_SERVER_USER_ID`.
- **Requires `nodus-mcp>=0.1.2`** (SSE `/messages/` mount + `auth_hook` header context).

### Streamable HTTP (FR-66 phase 1)

The transport ChatGPT and claude.ai connect to: one endpoint, `/mcp` by default. It needs
`nodus-mcp>=0.1.6` (`NodusServer.streamable_http_app()`, DEC-125).

```bash
AINDY_MCP_SERVER_MULTI_TENANT=true \
DATABASE_URL=postgresql://... \
aindy-runtime mcp-server --transport http --port 8080            # binds 127.0.0.1

# Behind your own HTTPS proxy, name the public host:
AINDY_MCP_SERVER_ALLOWED_HOSTS=mcp.example.com \
aindy-runtime mcp-server --transport http --host 0.0.0.0 --port 8080
```

- **Identity and scopes are SSE's**: the same headers, the same per-call resolution, the same
  scope rule. Stateless: no sticky sessions needed behind a proxy.
- **The `Host` and `Origin` check fails closed (DEC-127).** On a loopback bind only loopback
  names are accepted. On any other bind the server refuses to start unless
  `AINDY_MCP_SERVER_ALLOWED_HOSTS` lists the host name(s) clients use (`name`, `name:port`, or
  `name:*`). A wrong `Host` gets 421. A browser `Origin` must be in
  `AINDY_MCP_SERVER_ALLOWED_ORIGINS` (403 otherwise); a request with no `Origin`, which is what
  a server-side client sends, passes.
- **ChatGPT and claude.ai need OAuth** (below): their connectors can't send a header. A client
  that can (Claude Code, the SDKs) uses the headers above.

### OAuth for `/mcp` (FR-66 phase 2, DEC-129, DEC-130)

So ChatGPT and claude.ai can add the server by URL. Off by default; needs Alembic `0022`.

```bash
AINDY_MCP_OAUTH=on \
AINDY_MCP_PUBLIC_URL=https://mcp.example.com \
DATABASE_URL=postgresql://... \
aindy-runtime mcp-server --transport http --host 0.0.0.0 --port 8080   # behind your HTTPS proxy
```

Then add `https://mcp.example.com/mcp` as a connector. The client finds everything from the
`401` it gets first.

- **The runtime is its own authorization server, for `/mcp` only.** It serves `/authorize`,
  `/token`, `/register`, `/revoke`, `/.well-known/oauth-authorization-server`,
  `/.well-known/oauth-protected-resource/mcp`, and a sign-in and consent page at `/oauth/consent`.
- **The consent page:**
  - Users sign in with their existing account; it never creates one.
  - The page shows the address the user will be sent back to, because a client's name is
    whatever it registered.
  - Read scopes are pre-ticked; a write scope is offered only when the server exposes writes
    (DEC-128), and is never pre-ticked.
- **With OAuth on, `/mcp` accepts only its own access tokens.** A session JWT or a platform key
  sent to `/mcp` gets `401`, and an MCP token is never a session token anywhere else.
- **Tokens and grants:**
  - An access token lasts 1 hour and is held to its scopes like a key (DEC-126). A scope can also
    be a tool's own capability name when no scope names it, as for an app's `work.context`
    (DEC-130, OAuth grants only).
  - A refresh token lasts 30 days and rotates on every use; replaying an old one ends the grant.
  - A password change, a disabled account, or ending the grant stops its tokens, within
    seconds.
- **Users see and end their connections** at `GET` / `DELETE /auth/mcp-grants`, from a signed-in
  session only.
- **Registration (D4):**
  - Open by default, with caps: `AINDY_MCP_OAUTH_MAX_CLIENTS` (500) and
    `AINDY_MCP_OAUTH_REGISTRATIONS_PER_HOUR` (30).
  - Redirect URIs must be https, or http to a loopback host.
  - Clients that never got a grant are pruned after 30 days.
  - `AINDY_MCP_OAUTH_CLIENTS=closed` turns self-registration off; register clients with
    `aindy-runtime mcp-oauth register-client --name … --redirect-uri …`.
- **Nothing secret is stored in the clear.** Codes and refresh tokens are stored as hashes.
  A client secret is sealed with keys derived from the signing keys, so after a signing-key
  rotation's grace window a confidential client must register again.
- **The `Host` check** allows the public URL's host when `AINDY_MCP_SERVER_ALLOWED_HOSTS` is unset.

**Deferred (MEB-3b):** tenant/session attribution columns on `EffectRecord` (records *which*
session produced each effect — the program's only schema-contract bump) and an optional
per-session capability-ceiling token. See `../design/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md`.
