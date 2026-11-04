---
title: "App Handoff — Runtime v2.30.0"
api_version: "1.0"
last_verified: "2026-11-04"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.30.0

Bump the `constraints.txt` pin to `==2.30.0` (your contract test moves the floor with it), then
**rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current. This comes from your
`RUNTIME_2_29_2_UPGRADE.md` §3; thanks for the readings.

**What this release is.** It has two changes:
- `AINDY_MEMORY_RECALL_OWN_SESSION` defaults ON (#885, DEC-106), **which changes nothing on your stack**;
- a new operator command for your 2,387 stranded execution units (#886, DEC-107).

There is no route, scope, env var, dependency-pin or schema change.

> ## ★ After the rebuild: confirm what you are running, then report the dry run
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.30.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
>
> docker exec <api-container> aindy-runtime jobs settle-stale-units        # reports only
> docker exec <api-container> aindy-runtime jobs settle-stale-units --yes  # settles
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. Own-session recall defaults on (#885, DEC-106)

You have run this flag on since 2026-09-23, and your `RUNTIME_2_25_0_UPGRADE.md` counter window
(10-02 to 10-09: 0 at every stage, 405 recalls) is the evidence the flip rests on. So you see no
change. **Your 11-04 reading was not needed; we asked before reading that row closely.** Your reply's
point that 57 requests add little was right.

You can leave `AINDY_MEMORY_RECALL_OWN_SESSION=true` in your `.env` or remove it. Only `0`, `false`,
`no` or `off` disable it now, and a blank value means on.

The flag's test exposed one thing that may reach your suite. On a shared-connection SQLite fixture
under `StaticPool`, closing the read session rolls back the raw connection, which can erase a test's
rows. It is the fixture, not the route: on separate connections the same double-approve answers 200,
200 and runs once. If you see rows vanish after a recall in a test on such a fixture, this is why.

## 2. Closing your stranded units (#886, DEC-107)

Your count of 2,387 is every row the old cleanup failed, so we built the command.

- **What it moves:** a `job_log` unit still `pending`, whose row is `failed` with exactly the message
  *"Stale: never started within 1 hour of creation"*.
- **What it writes:** the unit goes `failed`, with `completed_at` set. **The job row does not change.**
- **What it leaves:** every other unit. A unit whose row failed any other way is left to be noticed.
- **How it runs:** committed batches of 500 (`--batch-size`). Nothing runs on upgrade.

`failed` rather than `completed`, because the job row says the job never ran. The dry run should
report 2,387 units, unless the count has moved since 11-04. **`--yes` writes the owner's database, so
it is the owner's call.**

## 3. The Postgres JSON filter is proven

You reported that the sweep's filter had met no real row on your stack. `test_job_log_settle_postgres.py`
now runs it on real Postgres in CI, scoped to one node beside a second that must not match. It passes,
so nothing was hiding in 2.29.1 or 2.29.2. You need to do nothing.

`publish.yml` job names and the `production` gate are unchanged, so your release watcher is
unaffected.

## 4. Asks

1. Bump the pin to `==2.30.0`, rebuild, and run the in-container check above.
2. Run `jobs settle-stale-units` as a dry run and report its count. Run `--yes` if the owner agrees,
   then report your query from the 2.29.2 handoff again; it should read 0.
