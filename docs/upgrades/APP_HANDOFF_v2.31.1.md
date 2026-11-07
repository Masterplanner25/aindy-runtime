---
title: "App Handoff — Runtime v2.31.1"
api_version: "1.0"
last_verified: "2026-11-06"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.31.1

Bump the `constraints.txt` pin to `==2.31.1` (your contract test moves the floor with it), then
**rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**You are on 2.30.0 at last report, so this also carries 2.31.0.** Go straight to 2.31.1, and read
[`APP_HANDOFF_v2.31.0.md`](./APP_HANDOFF_v2.31.0.md) too. Its asks still stand. You don't need to stop
on 2.31.0.

**What this release is.** Two fixes, both ours:
- `sys.v1.memory.search`, `.tree` and `.trace` were refused on every MCP call, and now they aren't (#896,
  DEC-112). Your `scripts/mcp_server.py` lists all three, correctly, as the runtime's default.
- `memory.trace` could return another user's node, and now it can't (#897, `MEM-TRACE-SCOPE-1`).

There is no route, scope, dependency-pin, env-var or schema change.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.31.1 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. What changes for you

The MCP server used to infer a tool's capability from its name, so `memory.search` was granted
`memory.search` while the syscall declares `memory.read`. It now grants what the syscall declares.

- **Your launcher gains those three reads and nothing else.** All seven of its tools run as
  `AINDY_MCP_SERVER_USER_ID`. `search` and `tree` filter by the caller. `trace` does too since #897 (§2).
- **Your launcher's refusal of writes still matters more now.** On a server with
  `AINDY_MCP_SERVER_ALLOW_WRITES`, `sys.v1.memory.delete` used to be refused on every call and now really
  hard-deletes. Your launcher exits if that variable is set, so this doesn't reach you through it. It would
  reach a bare `aindy-runtime mcp-server` started with writes on.
- **Any syscall named in `AINDY_MCP_SERVER_TOOLS` is now callable, whatever its capability is called.**
  Your launcher sets that list with `setdefault`, so an inherited value would replace its read-only list.
  That was already true; it now matters more, because a value could name a syscall that used to be refused.
- **In-process dispatch is unchanged.** Calls without `capability=`, including the Nodus `sys()` seam and
  extension workers, still infer it from the name.

## 2. `memory.trace` could cross users (#897)

`causal_trace` checked ownership on its first lookup and not on the hops after it, so a node sharing an event
id with another user's returned that user's node, content included. We reproduced this with seeded rows and
haven't seen it happen. It predates this release: `GET /platform/memory/trace` was reaching it already, on any
deployment serving that route. Your own `sys.v1.memory.trace` registration in
`apps/automation/syscalls/syscall_handlers.py` isn't live, because a second `register_all_domain_handlers`
later in that file replaces the function that lists it.

A trace may now be shorter than before, but only if it crossed users. Ask 3 tells us whether one ever could have.

## 3. A rule in your repo is now out of date

`sys.v1.work.context` and `.facts` declare `work.context` and `work.facts` because the old rule required
a match. `apps/masterplan/syscalls/syscall_handlers.py` says so in two comments, and
`tests/unit/test_mcp_read_access.py` pins it. **Nothing breaks if you leave them.** The names still work,
and that test still passes. If you want `masterplan.read` back, the rule no longer stops you. Either way, the
comments now describe the past.

## 4. `tree` and `list` may still come back small

This fix makes the tools reachable. It doesn't give your nodes paths. `MemoryNodeDAO.save` stores only the
path it is given, memory capture passes none, and `tree` and wildcard `list` filter on the stored column, so
a null-path node can't be found that way. We've filed this as `MEM-PATH-NULL-1` and changed nothing, because
our only data is a sample of 10 nodes from your stack, all null.

`memory.read` and `memory.search` with a query don't need a path. A bare `/memory` is an exact lookup and
always finds nothing, so test with `/memory/<your user id>/**`.

## 5. What we haven't verified

Tests run the real dispatcher and the real `tree`, `list` and `trace` handlers, on SQLite, with seeded event
ids for the #897 cases. Real
`memory.search` ran with `recall` stubbed, because it needs embeddings. Nobody has made a live MCP call
against this build on a real stack, so ask 2 is the first time that happens.

`publish.yml` job names and the `production` gate are unchanged, so your release watcher is unaffected.

## 6. Asks

1. Bump the pin to `==2.31.1`, rebuild, and run the in-container check above.
2. From an MCP client on your launcher, call `nodus_memory_search` with a query and `nodus_memory_tree` with
   `/memory/<your user id>/**`. Report whether each returns `success`, and how many nodes.
3. Run these and report the numbers. The first decides `MEM-PATH-NULL-1`. The second is how many pairs of
   nodes could have crossed users before #897, and 0 means none could:

   ```sql
   SELECT count(*) AS total, count(path) AS with_path FROM memory_nodes;
   SELECT count(*) FROM memory_nodes a JOIN memory_nodes b
     ON a.source_event_id = b.root_event_id AND a.user_id <> b.user_id;
   ```

   If the second isn't 0, tell us before anything else, and don't treat those traces as private.
4. Confirm the search in the 2.31.0 handoff: no caller of `sys.v1.agent.undo`, no `compensate=` hook, no plan
   declaring `expects`.

Report in your `RUNTIME_2_31_1_UPGRADE.md`.
