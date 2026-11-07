---
title: "App Handoff — Runtime v2.31.4"
api_version: "1.0"
last_verified: "2026-11-07"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.31.4

**A security release. Upgrade promptly if more than one user can sign in to your stack.** It fixes the remaining
findings from the runtime's audit that needed a scoped API key to reach, and adds per-user watcher keys. A later advisory
will carry the details.

Bump the `constraints.txt` pin to `==2.31.4` (your contract test moves the floor with it), then **rebuild the image**. No
ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

There is no dependency-pin, env-var or schema change. There is one new API key scope, `watcher.signals`, and three new
routes.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.31.4 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. Webhooks answer to their creator (#907, DEC-116)

A subscription now receives only the events of the user who created it. A subscription receives every tenant's events, and
events with no user, only if its creator is an admin at delivery, or if it has no creator, such as a manifest's. `is_admin`
is read at delivery and cached for 60 seconds, and a failed read delivers nothing extra.

**Who loses events:** a subscription created through a non-admin key. An admin demoted later stops receiving other users'
events within about a minute.

## 2. Script and workflow names have owners (#907, DEC-116)

- **`POST /platform/nodus/upload`:** `overwrite` replaces only your own script, otherwise **403 `script_not_owned`**. A
  script that exists only on disk now counts as existing, so it answers 409 without `overwrite`.
- **`POST /platform/nodus/flow`:** a name someone else holds answers **403 `workflow_not_owned`**. It always replaced the
  workflow before.
- **Operators** can still replace either.

**Things with no recorded owner can only be replaced by an operator.** That covers a script restored from disk after a
restart, even for whoever first uploaded it, and a workflow registered before this release or by a plugin. Your
`.nd` workflows register at boot through `register_nodus_workflow`, which doesn't enforce an owner, so that path is
unchanged. A key can no longer replace them over HTTP.

## 3. `/client/error` and `/client/vitals` (#907)

A report's owner now comes from a valid session, not the body's `user_id`. A report with no token, or a bad one, is still
accepted, with no owner. Your client sends these through `authRequest`, so signed-in reports should keep their owner, as
long as it sends the bearer token there. Please confirm that (§6).

## 4. Per-user watcher keys (#908, DEC-117)

- **`POST`, `GET`, `DELETE /watcher/keys`:** a signed-in user mints, lists and revokes their own keys, with the fixed scope
  `watcher.signals`. A session is required, so one key can't mint another. A duplicate name answers 409.
- **`/watcher/signals`:** takes that key in `X-Platform-Key`. Signals belong to the key's owner, and naming anyone else
  answers **403 `TENANT_VIOLATION`**.
- **Confinement:** a key holding only `watcher.signals` is refused everywhere else, including MCP.
- **`X-API-Key`:** unchanged. It can still name any user, so it is for services only.

**Your flows don't change.** With the user's own key, `watcher_signals_receive` receives the key's owner in the signals and
as the flow's user. `sys.v1.watcher.query` receives the owner as well.

**If a user's machine holds your `AINDY_API_KEY` for the watcher:** give them a key from `POST /watcher/keys`, set
`AINDY_WATCHER_KEY` in the client, and rotate the shared key. That needs the next `aindy-sdk` release, which comes after
this one.

## 5. Still open

A few low-severity findings from the audit remain. Your own routers were not part of the runtime's audit, so don't read this
as closing every multi-tenant question on your side.

`publish.yml` job names and the `production` gate are unchanged, so your release watcher is unaffected.

## 6. Asks

1. Bump the pin to `==2.31.4`, rebuild, and run the in-container check above.
2. Run your full test suite on it, and report any `TENANT_VIOLATION` or unexpected 403.
3. List your active webhook subscriptions by creator, and tell us if any non-admin subscription relied on other users' events.
4. Confirm a signed-in `/client/error` report still arrives with its owner.
5. Tell us who runs a watcher and with which key. If anyone's machine holds `AINDY_API_KEY`, plan the rotation in §4.

**Please tell us as soon as you are on this version.** The advisory waits for it.

Report in your `RUNTIME_2_31_4_UPGRADE.md`.
