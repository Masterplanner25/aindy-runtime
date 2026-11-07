---
title: "App Handoff — Runtime v2.31.3"
api_version: "1.0"
last_verified: "2026-11-07"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.31.3

**Upgrade promptly if more than one user can sign in to your stack.** With 2.31.2, this fixes the main security
findings from an audit of the runtime. A later advisory will carry the details.

Bump the `constraints.txt` pin to `==2.31.3` (your contract test moves the floor with it), then
**rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**You are on 2.30.0 at last report, so this also carries 2.31.0, 2.31.1 and 2.31.2.** Go straight to 2.31.3, and read
[`APP_HANDOFF_v2.31.0.md`](./APP_HANDOFF_v2.31.0.md), [`APP_HANDOFF_v2.31.1.md`](./APP_HANDOFF_v2.31.1.md) and
[`APP_HANDOFF_v2.31.2.md`](./APP_HANDOFF_v2.31.2.md) too. Their asks still stand, with the version in each read as
this one.

There is no scope, dependency-pin, env-var or schema change. Five routes change who may call them (§1).

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.31.3 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. The agent registry's routes need an operator (#903, DEC-114)

`agent_registry` has no owner, so it can't be scoped to a caller. These routes now answer **403** unless the caller is an
admin session or holds a `platform.admin` key:

- `GET /apps/coordination/agents`
- `GET /apps/coordination/agents/status`
- `POST /apps/coordination/agents/register`
- `POST /apps/coordination/agents/{id}/heartbeat`
- `DELETE /apps/coordination/agents/{id}`

**The callers who lose these are ordinary sessions, which used to reach them through `agent.run`, and platform keys
without `platform.admin`.**

`GET /apps/coordination/graph` stays open. A non-operator now sees only the agents in their own events. The other
coordination routes are unchanged, and so are the runtime's own self-registration and delegation ranking.

## 2. What we expect on your stack

**We expect nothing in normal use to change.** On your stack on 2026-11-07, read-only:

- `agent_registry` held one row, the built-in local agent, last seen 2026-10-14.
- None of 49 agent runs had been delegated.
- `request_metrics` showed one or two calls per route out of about 463,000 requests, the latest on 2026-09-23.
- `client/src` has no caller of these routes.

Three things in your repo do touch this:

- **`scripts/smoke_autonomy.py`** calls all five. Its registry steps now need an admin login. Nothing we found runs it
  automatically.
- **`docs/api/API_REFERENCE.md`** lists these routes, so it needs the new rule.
- **`distribute_goals` in `goal_service.py`** calls `list_agents` directly, which is unchanged. We found no caller of it.

## 3. Still open

Some of the audit's findings are not in this release. Most of those need an operator-issued or shared key to reach. The rest
are low severity. Your own routers were not part of the audit, so don't treat this as closing every multi-tenant question.

`publish.yml` job names and the `production` gate are unchanged, so your release watcher is unaffected.

## 4. Asks

1. Bump the pin to `==2.31.3`, rebuild, and run the in-container check above.
2. Run your full test suite on it, and report any `TENANT_VIOLATION` or unexpected 403.
3. Complete the asks in the 2.31.0, 2.31.1 and 2.31.2 handoffs against this version.
4. Update `API_REFERENCE.md`, and tell us if anything of yours needs these routes as a non-admin.

**Please tell us as soon as you are on this version.** The advisory waits for it.

Report in your `RUNTIME_2_31_3_UPGRADE.md`.
