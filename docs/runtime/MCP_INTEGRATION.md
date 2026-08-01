---
title: "MCP Integration (client-side)"
api_version: "1.0"
last_verified: "2026-07-11"
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

   > **The extra caps the SDK at `mcp<2`.** `nodus-mcp 0.1.2` is built against the 1.x
   > low-level server API; under `mcp 2.0.0` constructing a server raises
   > `AttributeError: 'Server' object has no attribute 'list_tools'`. If you install `mcp`
   > yourself rather than through the extra, apply the same cap. See
   > [TECH_DEBT MCP-SDK-2X-1](../../TECH_DEBT.md).
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
- **How it dispatches.** Each exposed syscall becomes an MCP tool whose handler calls
  `dispatch_syscall(name, args, user_id=<identity>)` — which grants the syscall its own
  capability (least-privilege, SDK-SYSCALL-GRANT-1) and manages its own DB session. The
  allowlist is the gate. Verified end-to-end on Postgres (write → read-back through the tool
  handlers).

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
- **Fail-closed.** A call whose headers resolve to no valid identity is denied. Writes still
  require `AINDY_MCP_SERVER_ALLOW_WRITES=true`.
- **stdio stays single-identity.** Multi-tenant is meaningful only over SSE (stdio carries no
  per-request headers); the server refuses to start stdio with the flag on. Without the flag, SSE
  runs as the single configured `AINDY_MCP_SERVER_USER_ID`.
- **Requires `nodus-mcp>=0.1.2`** (SSE `/messages/` mount + `auth_hook` header context).

**Deferred (MEB-3b):** tenant/session attribution columns on `EffectRecord` (records *which*
session produced each effect — the program's only schema-contract bump) and an optional
per-session capability-ceiling token. See `MEDIATED_EFFECT_BOUNDARY_PROGRAM.md`.
