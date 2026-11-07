---
title: "App Handoff — Runtime v2.31.2"
api_version: "1.0"
last_verified: "2026-11-07"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.31.2

**Upgrade promptly if more than one user can sign in to your stack.** This is a security fix, and a
later advisory will carry the details.

Bump the `constraints.txt` pin to `==2.31.2` (your contract test moves the floor with it), then
**rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**You are on 2.30.0 at last report, so this also carries 2.31.0 and 2.31.1.** Go straight to 2.31.2, and read
[`APP_HANDOFF_v2.31.0.md`](./APP_HANDOFF_v2.31.0.md) and [`APP_HANDOFF_v2.31.1.md`](./APP_HANDOFF_v2.31.1.md)
too. Their asks still stand.

There is no route, scope, dependency-pin, env-var or schema change.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.31.2 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. What changes (#900, DEC-113)

Identity now comes only from authentication at every step of a call.

- **A forwarded `user_id` must match the caller.** This covers `payload` on `sys.v1.job.submit`, `initial_state`
  on `sys.v1.flow.run`, `intent_data` on `sys.v1.flow.execute_intent` and `input_payload` on `sys.v1.nodus.execute`.
  A mismatch gets `TENANT_VIOLATION`. A missing `user_id` is not filled in. Only that key is checked, one level down.
- **Scripts are always refused.** In-process callers follow `AINDY_PAYLOAD_IDENTITY_DISPATCH`, so `observe` makes
  them count on `aindy_payload_identity_mismatch_total` instead.
- **`call_tool` in a Nodus script needs a token issued for that run and user.** Tokens from `mint_token` qualify.
  A token that has only expired still replays recorded steps, and `execute_tool` still refuses it before a tool runs.
- **A flow node uses `agent_run_id` from its state only if the caller owns that run.** Otherwise it logs a warning and
  ignores it, along with the state's token and continuation flag.
- **`require_execution_unit` returns `None` rather than another user's unit.** Units from
  `POST /apps/memory/nodus/execute` get a per-user source id, so a repeated task gets a new unit after this upgrade.
  Old rows are not changed.
- **A user id that isn't a valid UUID reads nothing on owner-only memory reads.** Calls with no user at all are unchanged.

## 2. What we expect on your stack

**We expect no change on your stack, but we have not run this against it.** Our search of your repo found
no `run_flow` or `sys.v1.job.submit` caller passing a `user_id` that differs from the caller, but it was a text search.
Several of yours read a forwarded `user_id`: the `agent.create_run` and `agent.approve_run` jobs, the other job
handlers reading `payload["user_id"]`, and the flow nodes in `watcher_flows.py` and `tasks_flows.py`. Those nodes and
handlers are safe through these syscalls after this release, because the mismatch is refused before they run.

**A background caller is the likeliest thing to break:** code acting for a user while dispatching as another identity.
It would show as `TENANT_VIOLATION`, in the log and on `aindy_payload_identity_mismatch_total`.

## 3. Your code should still take identity from context

The runtime guards the syscalls above. It can't guard a route of yours that writes a caller's value into flow state
or a job payload and then starts it directly. Where a node or job handler has `context` or `ctx`, prefer its user over
the state's or the payload's.

## 4. Open and not fixed here

Some of the audit's findings are not in this release. The advisory and later releases will cover them. Don't treat
this release as closing every multi-tenant question.

`publish.yml` job names and the `production` gate are unchanged, so your release watcher is unaffected.

## 5. Asks

1. Bump the pin to `==2.31.2`, rebuild, and run the in-container check above.
2. Run your full test suite on it, and report any `TENANT_VIOLATION`.
3. After a day of normal traffic, report `aindy_payload_identity_mismatch_total` by `syscall`, and any log line
   saying `ignoring agent run state`, `belongs to another user` or `does not belong to this run`. We expect none.
4. Complete the asks in the 2.31.0 and 2.31.1 handoffs. If the second query in the 2.31.1 handoff doesn't return 0,
   tell us first.

Report in your `RUNTIME_2_31_2_UPGRADE.md`.
