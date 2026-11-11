---
title: "App Handoff — Runtime v2.35.0"
api_version: "1.0"
last_verified: "2026-11-10"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.35.0

**Answers FR-66 (MCP for ChatGPT and claude.ai) up to its evidence step, and the runtime's part of FR-65.** The
watcher's part of FR-65 is **`aindy-sdk` 1.2.1** (§6). **Schema step: Alembic `0022`.**

Bump the `constraints.txt` pin to `==2.35.0`, declare `read_only=True` on your two MCP reads **in the same change**
(§1), then **rebuild the image** and run §0. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.35.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. ★ Schema step: Alembic `0022`, two new tables

The runtime Alembic head moves **`0021` → `0022`**, and the schema contract moves **`2026-11-08` → `2026-11-09`**. The
new tables are `mcp_oauth_clients` and `mcp_oauth_grants`; nothing reads them until `AINDY_MCP_OAUTH` is on. This is
the output of our `Upgrade Path` job, which upgrades a 2.34.0 database:

```text
$ aindy-runtime bootstrap-schema
state=upgrade_required operator_action=startup_reconcile exit_code=3

$ aindy-runtime bootstrap-schema --reconcile
ok: stamped alembic_version_runtime from revision 0021 to 0022 (the schema report matched the packaged models).
```

So your entrypoint, which branches on exit 3, stops on the first boot unless `AINDY_BOOTSTRAP_RECONCILE` is on. Run
`docker exec <api> aindy-runtime bootstrap-schema --reconcile` once and start again. Confirm afterwards that
`alembic_version_runtime` reads `0022`.

## 1. ★ Your MCP launcher refuses to start until your two reads say they read (DEC-128, #951)

- **What changed.** An MCP server now refuses to start when it would expose a syscall that is not registered with
  `read_only=True`, unless `AINDY_MCP_SERVER_ALLOW_WRITES` is set. Before this, an explicit `AINDY_MCP_SERVER_TOOLS`
  list could expose a write whatever `ALLOW_WRITES` said. The runtime declares its own ten reads, including the five
  memory reads your launcher lists.
- **What it means for you.** `scripts/mcp_server.py` lists `sys.v1.work.context` and `sys.v1.work.facts` and refuses
  `ALLOW_WRITES`, which is right. Neither syscall declares `read_only`, so on 2.35.0 the launcher stops at startup,
  naming them.
- **The fix**, in `apps/masterplan/syscalls/syscall_handlers.py`: add `read_only=True` to both `register_syscall`
  calls. **Do it in the same change as the pin bump.** The keyword is new in 2.35.0, so on 2.34.0 it is a
  `TypeError` at import.

## 2. Platform keys over MCP are held to their scopes (DEC-126, #951)

Over the MCP server, a call made with a platform key now needs a scope that authorizes the tool's capability. It is
the same rule `POST /platform/syscall` already used: `memory.read` or `memory.write` for the memory reads, and
`platform.admin` for everything. **Your stdio launcher runs as one configured identity, so it is unaffected.** This only
reaches a multi-tenant server (`AINDY_MCP_SERVER_MULTI_TENANT=true`) whose callers send `X-Platform-Key`.

## 3. FastAPI 0.141 → 0.162 (#950)

- **One change reaches clients:** a JSON body that is literally `null`, sent to a non-nullable `Body(...)` parameter, is
  now 422 instead of the default.
- **Unknown keyword arguments** to `Query`, `Body`, `FastAPI()` and the like now raise `TypeError` at import. We scanned
  your `apps/` for both on 2026-11-09 and found none; a router added since is worth a look.
- **FastAPI's own OTel auto-setup is opt-in.** The runtime configures its own exporter, so nothing changes there.

The full list is in `CHANGELOG.md` under 2.35.0.

## 4. FR-66: what you can connect now, and the evidence that closes it

New and **off by default**:

- **`aindy-runtime mcp-server --transport http`**: streamable HTTP at `/mcp`. It binds `127.0.0.1`, checks `Host` and
  `Origin`, and fails closed (DEC-127).
- **OAuth for `/mcp`**, so ChatGPT and claude.ai can add it by URL (DEC-129, DEC-130). With it on, `/mcp` accepts only
  its own 1-hour access tokens, and each one is held to the scopes the user approved. It also adds:
  - a sign-in and consent page at `/oauth/consent`, which never creates an account;
  - `GET` / `DELETE /auth/mcp-grants`, where a user sees and ends their connections;
  - audit events `mcp_grant.created` / `mcp_grant.revoked`, which say why a connection ended.

How to run it is in `docs/runtime/MCP_INTEGRATION.md` (§ "OAuth for `/mcp`"). In short:

```bash
AINDY_MCP_OAUTH=on \
AINDY_MCP_PUBLIC_URL=https://<your public host> \
AINDY_MCP_SERVER_TOOLS=sys.v1.memory.read,sys.v1.memory.search,sys.v1.memory.list,sys.v1.memory.tree,sys.v1.memory.trace,sys.v1.work.context,sys.v1.work.facts \
aindy-runtime mcp-server --transport http --host 0.0.0.0 --port 8080   # behind your HTTPS proxy
```

**What the consent page offers:**
- Your two reads have no runtime scope that names them, so it offers each as its own capability: `work.context` and
  `work.facts` (DEC-130).
- Reads are ticked by default.
- No write is offered, because writes are off.

**FR-66 closes on D7, which is yours to run.** Connect a **real claude.ai connector** and a **real ChatGPT connector** to
your stack. Have each one read a memory node, and report:
- what each client registered;
- whether discovery from the first `401` worked;
- what the consent page showed;
- the tool call's result;
- anything that failed.

**One known edge to watch:** on `mcp` SDK 1.x, `/revoke` makes `client_secret` a required field, even for a public
client. A client that leaves it out gets 400 when it disconnects. The grant stays live until the user ends it at
`/auth/mcp-grants`.

**Connected apps for your users.** The runtime's platform console has a Connected Apps panel, but that console admits
only admins. An ordinary user needs a page in your app that calls `GET` / `DELETE /auth/mcp-grants`; it is
session-only, so a platform key gets 403.

## 5. Two smaller fixes (#947)

- **A bad memory path is now `failure_class: "invalid"`.** A memory read with a path outside `/memory` used to say
  `transient`, which told an MCP client to retry a call that can't succeed.
- **`GET /watcher/signals?signal_type=focus_achieved`** (and `context_switch`, `heartbeat`) no longer answers 422.

## 6. FR-65: the watcher is `aindy-sdk` 1.2.1

- **What it does:**
  - It ends a session after `AINDY_WATCHER_IDLE_TIMEOUT` seconds without keyboard or mouse input (default 300). The
    session ends at the last input.
  - It sends `session_ended` when you stop it.
  - It adds `metadata.end_reason` (`input_idle`, `idle` or `stopped`) beside the keys you read.
  - It takes your own app lists (`AINDY_WATCHER_WORK_APPS`, …).
  - It installs an `aindy-watcher` command.
  - The header half of FR-65 (`AINDY_WATCHER_KEY`, sent as `X-Platform-Key`) shipped in 1.1.0.
- **★ Scores change.** 1.1.0 and earlier inflated `focused_seconds` and `focus_score`: ten minutes of work read as about
  605 minutes. It also fired `distraction_detected` on one sample after a minute of work. Both are fixed, so
  `focus_quality` from before and after the upgrade aren't comparable. Mark the date if you trend it.
- **Retire `scripts/watcher.py`.** 1.2.x does all three of its fixes. Run
  `aindy-watcher` with `AINDY_WATCHER_KEY` set, and set `AINDY_WATCHER_API_URL`, since the SDK defaults to
  `http://localhost:8000`.
- **If you keep the launcher, use 1.2.1, not 1.2.0.** 1.2.0's loop called `classify(window, rules)`. Your launcher
  replaces `classify` with a one-argument function, so under 1.2.0 every sample raised `TypeError` and nothing was
  sent. 1.2.1 calls `classify(window)` again. We ran your `install()` against it.
- **Your pyproject range** `aindy-sdk>=1.0,<2.0` admits 1.2.0. Pin `>=1.2.1` when you adopt.

## 7. Asks

1. Bump the pin to `==2.35.0` and add `read_only=True` to `work.context` and `work.facts` in the same change (§1).
   Rebuild, run `bootstrap-schema --reconcile` once (§0), and run the in-container check. Report that
   `alembic_version_runtime` reads `0022`, and that your MCP launcher starts.
2. Run D7 (§4) and report it. That closes FR-66.
3. Move to `aindy-sdk>=1.2.1` and retire `scripts/watcher.py` (§6).
4. When you build a connected-apps page for your users, use `/auth/mcp-grants` (§4).
