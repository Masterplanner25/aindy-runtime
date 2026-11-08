---
title: "App Handoff — Runtime v2.31.5"
api_version: "1.0"
last_verified: "2026-11-07"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.31.5

**A security release.** It picks up nodus-lang 5.15.1, which closes a path traversal reachable from guest scripts, and fixes
the last two low-severity findings from the runtime's tenant audit.

Bump the `constraints.txt` pin to `==2.31.5` (your contract test moves the floor with it), then **rebuild the image**. No
ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**You are on 2.31.3 at last report, so this also carries 2.31.4.** Go straight to 2.31.5, and read
[`APP_HANDOFF_v2.31.4.md`](./APP_HANDOFF_v2.31.4.md) too. Its asks still stand, with the version read as this one.

There is no schema, env-var or API-key-scope change. The only dependency-pin change is nodus-lang (§1).

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, importlib.metadata as m; print(v.__version__, m.version('nodus-lang'), list(AINDY.__path__))"
> # expect: 2.31.5 5.15.1 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. nodus-lang 5.15.1 (#913)

A guest script calling `resume_workflow(id)` with an id like `../x` could read or delete a `.json` file outside nodus's
run-state directory. nodus built the path without validating the id, and that code doesn't go through the guest's
`allowed_paths`. `resume_workflow` is always allowed to guests, so any user who can run a Nodus script could reach it.
5.15.1 validates every run-state id.

**Your stack:** `docker-compose.prod.yml` runs Nodus in `api`, which sets `NODUS_RUN_STATE_ROOT` to its own volume. So on
that stack the reachable files were the ones on that volume. Without that variable, the root is `.nodus` under the server's
working directory, next to everything else. Keep it set on any process that runs Nodus.

You don't pin `nodus-lang` yourselves, so the runtime's `==5.15.1` decides it.

## 2. Someone else's agent run answers like a missing one (#912, DEC-118)

These answered 403 for a run that exists but isn't yours, and 404 for one that doesn't exist. Both now answer **404** with
the same detail:

- `get_agent_run_runtime`, `list_agent_run_steps_runtime`, `list_agent_run_events_runtime`
- `GET /apps/coordination/runs/{id}/children`

Your `GET /apps/agent/runs/{id}` uses `get_agent_run_runtime`, so it changes the same way. A test asserting 403 for another
user's run needs to expect 404.

## 3. The effect ledger checks a slot's tenant (#912, DEC-118)

A slot recorded for another user is refused rather than replayed. No such collision is known: every scope is owner-bound
since 2.31.2. The caller then runs without the gate, as for any ledger failure, or refuses under `AT_MOST_ONCE`.

**Watch `aindy_effect_gate_outcomes_total{outcome="tenant_mismatch"}`. It should always read 0.** Anything else is a scoping
bug, so tell us. Rows written before attribution existed have no tenant and behave as before.

## 4. Asks

1. Bump the pin to `==2.31.5`, rebuild, and run the in-container check above. It should print nodus-lang 5.15.1.
2. Run your full test suite on it, and report any unexpected 404 or `TENANT_VIOLATION`.
3. Do the asks in `APP_HANDOFF_v2.31.4.md` against this version.
4. After a day of normal traffic, report `tenant_mismatch` from `/metrics`.

**Please tell us as soon as you are on this version.** The advisory for 2.31.4's fixes waits for it.

Report in your `RUNTIME_2_31_5_UPGRADE.md`.
