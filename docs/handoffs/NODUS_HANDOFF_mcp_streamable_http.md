---
title: "nodus-mcp — Streamable HTTP for NodusServer (FR-66)"
api_version: "1.0"
last_verified: "2026-11-09"
status: current
owner: "platform-team"
---

# nodus-mcp — a streamable HTTP app for `NodusServer`

Written from `aindy-runtime` for whoever is working in `nodus-mcp`. **One release asked for, additive: 0.1.6.**

## 1. Why

The app behind A.I.N.D.Y. wants ChatGPT and claude.ai to read it (its FR-66). Both add a remote MCP server by URL and
speak **streamable HTTP**, one endpoint conventionally at `/mcp`; both are moving off SSE. The runtime's MCP server is
a `NodusServer`, and nodus-mcp 0.1.5 serves it two ways:

- `NodusServer.run(transport="stdio")`;
- `NodusServer.run_sse_app()`, a Starlette app with `/sse` and `/messages/` (the #7 and #11 fixes).

There is no streamable HTTP runner. The runtime could build one around `NodusServer._server`, but that is a private
attribute, and the SDK-major branching (`_SDK_V2`, #12) and the per-call header context (`_request_meta`) already
live here. **Decided on the runtime side as DEC-125: the transport belongs in nodus-mcp.** The runtime's design is
`docs/design/MCP_REMOTE_TRANSPORT_DESIGN.md` §4.

**Not asked: OAuth.** The runtime runs its own authorization server for this endpoint. nodus-mcp only has to let a
host put that server's routes and middleware into the same app (§2, A2).

## 2. The asks

### A1 — `NodusServer.streamable_http_app()` (required)

```python
def streamable_http_app(
    self,
    *,
    path: str = "/mcp",
    stateless: bool = True,
    json_response: bool = False,
    security_settings: "TransportSecuritySettings | None" = None,
    routes: Sequence[BaseRoute] = (),
    middleware: Sequence[Middleware] = (),
) -> Starlette: ...
```

- **It wraps `mcp.server.streamable_http_manager.StreamableHTTPSessionManager`** around the low-level server and mounts
  its ASGI handler at `path`.
- **The app's lifespan runs `session_manager.run()`.** This is the trap: a host that `Mount`s a Starlette app inside
  another does **not** run the inner app's lifespan, so the manager never starts and every request fails. That is why
  `routes` and `middleware` are parameters: the host adds its routes to *this* app instead of mounting it.
- **`stateless=True` by default.** The runtime deploys behind a proxy without sticky sessions, and keeps no MCP
  session state of its own.
- **`security_settings` passes through.** Behind a reverse proxy the `Host` header is the public name, and the SDK's
  DNS-rebinding protection must be told which hosts to allow, or it refuses them.

### A2 — the `auth_hook` context over this transport (required)

`auth_hook(name, args, meta)` must get the same `meta` it gets over SSE, with `headers` from the HTTP request.
- **Expected to work already:** the SDK's streamable transport attaches the Starlette request to each message
  (`ServerMessageMetadata(request_context=request)`), which is what `_request_meta` reads.
- **The ask is a test that proves it:** a real `streamablehttp_client`, a header sent, the header seen in the hook. The
  same shape as #12's SSE end-to-end test.
- **Also surface what host middleware authenticated:** when present, `request.scope["user"]` and `request.scope["auth"]`
  as `meta["user"]` and `meta["auth"]`.
  - The runtime's bearer middleware verifies the OAuth token once, at the HTTP layer, and the hook should read that
    result rather than re-parse the header.
  - **Please don't rely on the SDK's `get_access_token()` contextvar for this.** We believe the session manager runs the
    server in its own task group, so a contextvar set by request middleware isn't visible to the handler. We haven't
    proved that; a test either way would settle it.

### A3 — both SDK majors (required)

The same as 0.1.4: one code path per `mcp` major, both driven by the same end-to-end tests. If 2.x's session-manager API
differs, branch on it here, as `_SDK_V2` does today, so hosts don't have to.

### A4 — `run(transport="streamable-http")` (nice to have)

It blocks under uvicorn, as a convenience for scripts. The runtime will call `streamable_http_app()` itself.

## 3. What the runtime does with it

- `aindy-runtime mcp-server --transport http` serves `streamable_http_app()`, bound to `127.0.0.1` by default.
- With OAuth on, the runtime passes its authorization routes (`/.well-known/*`, `/authorize`, `/token`, `/register`,
  `/revoke`, the login and consent page) as `routes`, and its bearer check as `middleware`.
- The bearer check answers `401` with `WWW-Authenticate` on `/mcp` itself, before any MCP message is read, because that
  `401` is how both clients discover OAuth.
- **The pin:** `nodus-mcp>=0.1.6`, bumped at all three sites (`pyproject.toml`, `AINDY/requirements.txt`, the `Install
  MCP extra` CI step; NODUS-UPGRADE-1).

## 4. Status and links (`aindy-runtime`)

- **FR-66:** the app's `RUNTIME_FEATURE_REQUESTS.md`, filed 2026-11-09.
- **Design:** `docs/design/MCP_REMOTE_TRANSPORT_DESIGN.md` (#948). Phase 0 is this handoff; phase 1, `--transport
  http`, waits on the release.
- **#947** made a declared failure class survive the dispatcher (the FR-66 side note). It is independent of this handoff.
- **Decision:** DEC-125 (`docs/governance/DECISION_LOG.md`).
