---
title: "App Handoff — Runtime v2.31.6"
api_version: "1.0"
last_verified: "2026-11-08"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.31.6

**Answers FR-57, FR-58 and FR-60, and fixes MEM-PATH-NULL-1.** Not a security release, but FR-57 is the switch you asked
for to close registration.

Bump the `constraints.txt` pin to `==2.31.6` (your contract test moves the floor with it), then **rebuild the image**. No
ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

There is no schema, dependency-pin or API-key-scope change. There is one new setting, `AINDY_REGISTRATION` (§2), and one
new operator command (§1).

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.31.6 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. Memory nodes saved without a path can be found by path (#917, DEC-119)

You counted 26 of the owner's 1,397 nodes with a stored path. The rest were invisible to `memory.tree`, a wildcard
`memory.list` and `walk_path`, which filter on the stored column, while every reader was shown a derived path.

- **New writes:** `MemoryNodeDAO.save` now stores that same derived path, `/memory/{user}/_legacy/{memory_type}/{id}`, for
  an owned node written without one. Your direct `dao.save` calls get it with no change.
- **Existing rows:** run this once after the rebuild.

  ```bash
  docker exec <api-container> aindy-runtime memory backfill-paths            # reports how many would change
  docker exec <api-container> aindy-runtime memory backfill-paths --yes      # stores them, in committed batches
  ```

  It stores exactly the path readers were already shown, so nothing a user has seen changes. Running it again finds
  nothing. A node with no owner is left alone.

## 2. Registration can be closed (FR-57, DEC-120)

- **`AINDY_REGISTRATION=closed`:** `POST /auth/register` answers exactly as before, with 202, the same body and the same
  timing, but creates no account and sends nothing, including no notice to an existing address. Login, password reset
  and existing accounts are unchanged.
- **Other values:** `open` is the default. Any other value counts as `closed`, and an empty value means the default, so an
  unset Compose variable keeps today's behaviour.
- **Not built:** invite tokens (your optional second step).

## 3. `/db/verify` needs an operator (FR-58, DEC-120)

The `/db` router now requires an admin session or a `platform.admin` key instead of the shared `AINDY_API_KEY`.
`apps/rippletrace/bootstrap.py` mounts this router at your root, and the check is on the router, so your mount gets it
too. A caller with only the shared key now gets **401**. If nothing of yours calls it, you can also drop the mount, as you
offered.

## 4. Trace helpers can be scoped to one user (FR-60, DEC-120)

`build_trace_graph`, `detect_root_event`, `detect_terminal_events` and `calculate_trace_span` take an optional `user_id`.
With it, they keep only that user's events and memory nodes, and the edges whose ends both remain.

- **The runtime's route:** `/observability/execution_graph` passes the caller to every helper that accepts the parameter.
- **Yours:** you register your own `build_trace_graph` (`apps/rippletrace/bootstrap.py`) and call it from
  `GET /apps/rippletrace/{trace_id}`. Pass `user_id` there to get the same scoping. Until you do, your helpers are
  called as before.

## 5. Asks

1. Bump the pin to `==2.31.6`, rebuild, and run the in-container check above.
2. Run `aindy-runtime memory backfill-paths`, then `--yes`, and report both counts. Afterwards a
   `memory.tree` on `/memory/<owner id>/**` should return about the 1,397 nodes, not 26.
3. Set `AINDY_REGISTRATION=closed` in your compose if you want registration closed, and confirm a register request
   gets 202 and creates nothing.
4. Pass `user_id` through your trace route's helpers (§4), and tell us if anything of yours calls `/db/verify`.
5. Run your full test suite on it, and report any unexpected 401 or 403.

Report in your `RUNTIME_2_31_6_UPGRADE.md`.
