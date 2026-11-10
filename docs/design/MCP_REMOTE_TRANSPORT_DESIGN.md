---
title: "MCP Remote Transport and OAuth (FR-66) — Design"
api_version: "1.0"
last_verified: "2026-11-10"
status: current
owner: "platform-team"
---

# FR-66 — an MCP server that ChatGPT and claude.ai can add — design

**Status (2026-11-09).** Every decision is made, each as recommended: D3 = DEC-125 (the transport is in nodus-mcp), D5 = DEC-128 (with a declared `read_only`), and D1, D2, D4, D6, D7 = DEC-129.

- **Phase 1 is built (#951)** on nodus-mcp 0.1.6: `--transport http`, platform keys held to their scopes on every transport (DEC-126), a fail-closed `Host` check (DEC-127), and writes refused without `ALLOW_WRITES` (DEC-128).
- **Phase 2 (OAuth) is built.** Its build-time choices are DEC-130 (accepted 2026-11-10): exact-capability scopes for OAuth grants only, a missing `resource` accepted (a foreign one refused), client secrets sealed under derived keys, and `/mcp` accepting only MCP tokens when OAuth is on.
- **FR-66 stays open** until D7's two real connectors work.

Originally a proposal under
`AGENT_WORKING_RULES.md` §8: it adds an internet-facing authentication surface, stores new credentials, and needs a
schema step.

## 1. The request

The app's `RUNTIME_FEATURE_REQUESTS.md` FR-66 (filed 2026-11-09, P3): the owner wants to read A.I.N.D.Y. from ChatGPT
and claude.ai, not only from Claude Code and Claude Desktop. Both web products add a *remote* MCP server by URL:

- **Transport.** They speak **streamable HTTP**, one endpoint conventionally at `/mcp`. Both are moving off SSE.
- **Auth.** They offer **OAuth** (with dynamic client registration) or no auth. A connector set up in their UI cannot
  send a custom header, so neither `Authorization: Bearer <session JWT>` nor `X-Platform-Key` can be configured.
- **The app's workarounds failed:** OpenAI's Secure MCP Tunnel connected but every call was denied upstream, and
  no auth (the owner's Nodus MCP server behind ngrok) is not acceptable for this data.

The asks:
1. `--transport http` (streamable HTTP at `/mcp`).
2. OAuth 2.1 per the MCP authorization spec: protected-resource metadata, dynamic client registration, PKCE. The token
   must map to a real user, so DEC-103 (payload `user_id` bound to the caller) and DEC-112 (the tool gets its declared
   capability) hold.
3. A way to keep the app's read-only allowlist on that transport, and to refuse writes.
4. Nothing about exposure. The app puts it behind its own HTTPS.

## 2. What exists, measured in this repo

- **Transports.** `aindy-runtime mcp-server --transport` takes `stdio` or `sse` (`runtime_only.py:1051`).
  `mcp_server.serve_sse` runs `NodusServer.run_sse_app()` under uvicorn.
- **nodus-mcp 0.1.5 has no streamable HTTP runner.** `NodusServer.run(transport=...)` takes `stdio`; `run_sse_app()`
  builds a Starlette app with `/sse` and `/messages/`. The installed `mcp` SDK (1.27.2) ships
  `mcp.server.streamable_http_manager`, so the transport exists one layer down.
- **Identity.**
  - stdio and single-identity SSE dispatch everything as `AINDY_MCP_SERVER_USER_ID`.
  - Multi-tenant SSE (`AINDY_MCP_SERVER_MULTI_TENANT=true`) resolves each call's headers in an `auth_hook`
    (`_resolve_session_identity`), sets `_SESSION_IDENTITY`, and the handler dispatches as that user.
  - The dispatch path after the hook is the same for every transport: `bind_payload_user_id` (DEC-103), an owned
    execution unit, `dispatch_syscall(..., capability=<declared>)` (DEC-112).
- **Allowlist.**
  - The default is five read syscalls (`READ_ONLY_SYSCALLS`).
  - `AINDY_MCP_SERVER_ALLOW_WRITES=true` adds four write syscalls.
  - `AINDY_MCP_SERVER_TOOLS` replaces the list outright, writes included, whatever `ALLOW_WRITES` says.
  - The app's launcher (`scripts/mcp_server.py`) sets `AINDY_MCP_SERVER_TOOLS` to seven read tools and refuses to
    start if `ALLOW_WRITES` is set. That second check is the app's, not the server's.
- **Credential surfaces we could build on.**
  - Session JWTs (`create_access_token` / `decode_access_token`, with `token_version` revocation on password change).
  - Platform API keys (`platform_api_keys`: hashed, scoped, `expires_at`, `revoked_at`, `last_used_at`).
  - Domain-separated signing keys (`_derive_domain_key`, already used so an email-verify or reset token is never a
    session token).
  - `authenticate_user` (password, `is_active`, verified-email checks).
- **The SDK's OAuth kit.** `mcp.server.auth` has handlers for `/authorize`, `/token`, `/register`, `/revoke` and both
  metadata documents. It defines an `OAuthAuthorizationServerProvider` protocol for the storage and decisions, and a
  `TokenVerifier` for a resource server that trusts someone else's tokens. What we would write is the provider, the
  login and consent page, and the storage.

## 3. What gets built

Three pieces. All are off until an operator turns them on.

1. **`--transport http`** (§4): streamable HTTP at `/mcp`, with the same identity modes as SSE.
2. **An OAuth authorization server for the MCP endpoint** (§5, §6): dynamic client registration, authorization code with
   PKCE, refresh, revocation and metadata. Its access tokens are accepted **only** by the MCP endpoint.
3. **Server-side tool policy** (§7): the token's scopes, the allowlist and the write switch are all enforced by the
   server, so the app's launcher check becomes a second line rather than the only one.

## 4. The transport

**Accepted (DEC-125): a nodus-mcp release** that adds `NodusServer.streamable_http_app()` beside `run_sse_app()` (an
outbound handoff under `docs/handoffs/`).
- The SDK-major branching (`_SDK_V2`) and the per-call header context the `auth_hook` reads
  (`_request_meta` → `headers`) already live there.
- A second copy in the runtime would read `NodusServer._server`, a private attribute.
- **Fallback, if Nodus can't ship it soon:** the runtime wraps `StreamableHTTPSessionManager` around the low-level
  server itself, pinned to the nodus-mcp version it was written against.
- Either way, NODUS-UPGRADE-1 applies: bump at all three sites.

Behaviour:
- `--transport http` serves `/mcp`. It binds `127.0.0.1` by default; exposure is the operator's choice.
- **Stateless mode** (no server-side session affinity), so the app can run it behind a proxy without sticky sessions.
  *To verify against both clients before building.*
- The identity modes are SSE's:
  - single configured identity, **refused** when `AINDY_MCP_OAUTH` is on (§5);
  - header identity (multi-tenant), useful to Claude Code and other clients that can send a header;
  - OAuth (§5).

## 5. OAuth: who issues the token

**Recommended (D1): the runtime is its own authorization server, for the MCP endpoint only**, built on the SDK's
handlers. The user signs in with their A.I.N.D.Y. account; there is no third-party identity provider to run.

- **The alternative is a resource server only:** the operator runs an identity provider (Keycloak, Auth0, …), and we
  verify its tokens with a `TokenVerifier`. Less code here, but every operator then needs an identity provider. The
  provider's `sub` is also an *asserted* identity, so mapping it to a `User` row is INITIATOR-IDENTITY-1's problem: it
  may only constrain, never name a user, without an explicit link we would also have to build.
- **Same process as `/mcp`.** The authorization endpoints, the login and consent page, and `/mcp` are one Starlette app
  under one public base URL, `AINDY_MCP_PUBLIC_URL`. Required when OAuth is on; it is the issuer and the resource
  identifier the metadata advertises.
  - The server refuses to start if that URL is not `https://` (localhost excepted).
  - The platform API and SPA are untouched.
- **The login and consent page is server-rendered HTML in that process, not the SPA.**
  - The SPA keeps its token in browser storage on another origin, so a redirect from ChatGPT cannot see it.
  - The page posts email and password to `authenticate_user`, with the same checks and rate limit as `POST
    /auth/login`. It never registers anyone: registration stays FR-57's.
  - A CSRF token on the form; no JavaScript.

### Flow

1. **Discovery.**
   - The client calls `/mcp` and gets `401` with `WWW-Authenticate` pointing at
     `/.well-known/oauth-protected-resource`.
   - That document names the authorization server, which is this same server; its metadata is at
     `/.well-known/oauth-authorization-server`.
2. **Registration (DCR, `POST /register`).** The client registers its name and redirect URIs and gets a `client_id`.
   It is a public client: PKCE, no secret.
3. **Authorize.** `GET /authorize` with PKCE (`S256` only), `state`, `scope` and `resource` (RFC 8707). The user signs
   in, sees the consent page and approves or refuses.
4. **Code.** A redirect to the **exactly registered** URI, carrying a single-use code that lives 60 seconds and is bound
   to the client, the user, the PKCE challenge, the scopes and the resource.
5. **Token.** `POST /token` exchanges the code and its verifier for an access token and a refresh token.
6. **Calls.** `/mcp` with `Authorization: Bearer <access token>` → the `auth_hook` verifies it, sets
   `_SESSION_IDENTITY` and the token's scopes, and the existing dispatch path runs unchanged.
7. **Refresh and revoke.** Refresh tokens rotate on use; reusing an old one revokes the whole grant. `POST /revoke`,
   and the user's own "connected apps" list (§6), end a grant.

## 6. Tokens and storage

**Recommended (D2): a short-lived signed access token, and a revocable grant row behind it.**

- **Access token:** a JWT signed with a **domain-separated key** (`_derive_domain_key(signing_key(), b"mcp-access")`,
  as the email-verify and reset tokens already are).
  - It carries `sub`, `aud` = `AINDY_MCP_PUBLIC_URL`, `scope`, `grant_id`, `tv` (the user's `token_version`) and a
    lifetime of 1 hour.
  - **It is never a session token, and a session token is never one of these.** `decode_access_token` rejects it
    (wrong key); the MCP verifier rejects a session JWT (wrong key and no `aud`). Both directions are tested.
- **Per call:** verify the signature, `aud` and expiry; then one read of the grant (revoked?) and the user
  (`is_active`, `token_version`).
  - This read is cached per grant for a few seconds, as CANCEL-REACH-1 caches its cancel read.
  - **Unlike CANCEL-REACH-1, it fails closed:** an unreadable grant is a refusal.
- **Two tables, Alembic `0022`** (a schema step, so the app takes the exit-3 path once):

  | Table | Holds |
  |---|---|
  | `mcp_oauth_clients` | DCR registrations: `client_id`, name, redirect URIs, created, last used |
  | `mcp_oauth_grants` | one per (user, client) consent: scopes, resource, the current refresh-token **hash**, the pending code **hash** with its PKCE challenge and expiry, `token_version` at consent, created, last used, revoked |

  - Codes live on the grant row; they are too short-lived to need their own table.
  - No secret is stored in the clear.
- **Platform API keys are not reused as access tokens.** It was considered: they are revocable and scoped already. But a
  refreshed token would mint a new key row every hour, the user's key list would fill with them, and a key is accepted
  by every HTTP route, which is wider than the MCP endpoint.
- **Connected apps.** `GET /mcp-grants` and `DELETE /mcp-grants/{id}` on the main API, scoped to the caller, so a user
  can see and end every grant. **Built:** the platform console's Connected Apps panel drives them, and every grant
  made or ended writes an audit event (`mcp_grant.created` / `mcp_grant.revoked` with its reason).
- **A password change ends every grant**: `token_version` moves, so the next call and the next refresh are refused.

## 7. Tool policy, enforced by the server

Every call over HTTP must pass three checks, in order:

1. **The tool is on the allowlist** (as today).
2. **The token's scopes include the syscall's declared capability.** OAuth scopes are the runtime's `Scopes` names
   (`memory.read`, …). The consent page offers the capabilities of the allowlisted tools, nothing more. DEC-112 stays
   true: the tool receives its declared capability; the token decides only whether the call may happen.
3. **A write needs the write switch.**
   - **Accepted (D5, DEC-128): `AINDY_MCP_SERVER_TOOLS` can no longer bring in a write syscall unless
     `AINDY_MCP_SERVER_ALLOW_WRITES=true`.** A syscall is a read only when it declares `read_only=True`;
     undeclared counts as a write.
   - The server refuses to start, naming the write, so the app's launcher check becomes the server's.
   - This changes the existing stdio and SSE behaviour too, so it gets its own changelog entry.
   - Even with writes on, a write scope is never pre-ticked on the consent page.

The second check applies to **every** identity that carries scopes, on every transport, and is tested on each.

## 8. Abuse and failure cases, each with a test

- A reused code; a wrong PKCE verifier; a `plain` challenge; a redirect URI that differs by one character; a missing or
  foreign `resource`. All are refused.
- An access token presented to `/platform/*`, and a session JWT presented to `/mcp`. Both are refused.
- A revoked grant, an expired token, a disabled user, and a password change since consent. All refused on the next call.
- A rotated refresh token used twice → the grant is revoked.
- Open DCR: a cap on registered clients (`AINDY_MCP_OAUTH_MAX_CLIENTS`), a rate limit on `/register`, and clients with
  no grant pruned after 30 days. A registered client alone can do nothing: a token still needs a user to sign in and
  consent.
- **The consent page is the phishing surface.** A DCR client's name is unverified, so the page shows the **redirect
  host** prominently, beside the name.
- **A scope that doesn't cover the tool's capability → `permission`**, never `transient`. #947 made a declared class
  survive the dispatcher.

## 9. Decisions for the owner

| # | Decision | Recommended |
|---|---|---|
| D1 | Who issues tokens | **The runtime, for the MCP endpoint only.** An external identity provider (resource server only) stays possible later, with an explicit account link |
| D2 | Token shape | **A 1-hour signed access token with a domain-separated key, plus a rotating refresh token on a revocable grant row.** Not platform API keys |
| D3 | Where the transport is built | **ACCEPTED (DEC-125): nodus-mcp**, as `streamable_http_app()` in 0.1.6 (the handoff). The runtime-side fallback stays only if it can't ship |
| D4 | Dynamic client registration | **Open, capped and rate-limited**, because both target clients register this way. An operator can close it (`AINDY_MCP_OAUTH_CLIENTS=closed`) and pre-register clients with a CLI command |
| D5 | Writes in an explicit tool list | **Refused unless `ALLOW_WRITES`**, on every transport (a behaviour change) |
| D6 | Default | **Off** (`AINDY_MCP_OAUTH` unset). The transport is usable with header auth without OAuth |
| D7 | Evidence to close FR-66 | **A real claude.ai connector and a real ChatGPT connector**, each reading one memory node on the app's stack through its HTTPS. Not a test client |

## 10. Phases

| Phase | What | Usable by |
|---|---|---|
| 0 | nodus-mcp `streamable_http_app()` in 0.1.6 (`docs/handoffs/NODUS_HANDOFF_mcp_streamable_http.md`), or the fallback | — |
| 1 | `--transport http` with header identity; scope enforcement on every transport (§7.2, DEC-126); the `Host` check (DEC-127); D5 (DEC-128). **Built 2026-11-09 (#951)** | Claude Code and any client that can send a header |
| 2 | OAuth: Alembic `0022`, DCR, authorize with login and consent, token, refresh, revoke, metadata; connected-apps routes. Off by default | ChatGPT and claude.ai, once exposed |
| 3 | Evidence (D7) on the app's stack; any fixes it finds. (The SPA card and grant audit events were built ahead of it, 2026-11-10.) | — |

Phases 1 and 2 are separate releases, so the schema step travels alone.

## 11. To verify before building

- That both clients work with stateless streamable HTTP, and with which protocol revision. Read their current docs at
  build time; these products change monthly.
- Whether ChatGPT still asks for particular tool names or shapes (it once expected `search` and `fetch` for some modes).
  If so, that is a small adapter on top of the existing allowlist, not a change to the syscalls.
- Which redirect URIs each client registers. The exact-match rule must not need a wildcard to work with either.

## 12. Not in this design

- Exposure, TLS termination and the domain: the app's (FR-66 says so).
- Accounts created through OAuth: never. A user must already exist (FR-57).
- MCP writes from the web clients. D5 and the consent page make them possible, but nothing here turns them on.
- The single-identity modes over stdio and SSE are unchanged, except for D5.
