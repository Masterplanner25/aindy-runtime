---
title: "App Handoff — Runtime v2.16.0"
api_version: "1.0"
last_verified: "2026-09-15"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.16.0

Pin bump `constraints.txt` `==2.15.0` → `==2.16.0` (your contract test moves the floor with it).

**Required of you: nothing.** No schema step, no required code change. This release closes
**your FR-30** and the **FR-29 addendum**, both filed 2026-09-15 from your 2.15.0 verification
(`EU-FINALIZE-UNCOMMITTED-1`, #673). One unflagged behaviour change on `execution_units` (§2), one
optional cleanup (§4 step 3), and a verification step that is the first live run of the fix.

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.16.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Which path are you on?

| If the container reports… | Read |
|---|---|
| **2.9.0 through 2.15.0** | Pin bump + rebuild. No schema step. Go. |
| **2.8.0** | Same, plus the envelope change from `RUNTIME_2_9_0_UPGRADE.md` §1 you skipped. |
| **2.7.0 or earlier** | `RUNTIME_2_11_0_UPGRADE.md` §1 first — you owe 2.8.0's `flow_runs.graph_signature` step (`bootstrap-schema --reconcile`, Alembic `0018`), then this. |

2.16.0 adds no schema: Alembic head `0018`, `SCHEMA_CONTRACT_VERSION` `2026-09-10` unchanged,
`git diff v2.15.0..v2.16.0 -- AINDY/db/models/ AINDY/memory/memory_persistence.py` empty.

---

## 1. FR-30 — what was fixed, and what your diagnosis got exactly right

Your mechanism was verified line for line: `_safe_finalize_eu` → `update_status` → `flush()`
only; last write on the request session; the `execution.completed` emit before it committed;
`get_db` closes without committing; rolled back. **Pre-existing since the table's first row.**

- **`_safe_finalize_eu` commits after a successful `update_status`.** The write's own site,
  inside its existing try/except — a commit failure records `execution_unit.finalize.<status>:
  failed` exactly as a flush failure did. Not `get_db` (your "not asking for" was right — a route
  session that never commits on its own is the correct default), not `update_status` (other
  callers manage their own transactions).
- **What you could not see, and neither could we:** the runtime's own route tests for FR-29
  asserted the reader's unit was `completed` and **passed on the broken code** — the shared test
  fixture puts the app's request session and the test's reader on one connection inside one
  transaction, where a flush reads exactly like a commit. Recorded as catalogue variant 15 in our
  CLAUDE.md; the new suite reads through a separate connection with a liveness control. Your
  table was the only instrument that could see it.
- **The 105 `executing` units our 2026-09-13 run left "undiagnosed" were this**, not FR-29 — the
  2.15.0 handoff's §1 said otherwise and is corrected in place. Your upgrade doc repeated the
  claim from ours ("so, almost certainly, were the 105"); it was ours to get wrong.

### FR-29 addendum — worse than reported, fixed in the same PR

`rehydrate_waiting_eus` seeds `waiting_flow_runs` with `run_id=eu_id` for **every** waiting unit
(`wait_rehydration.py:287`), and an execution-unit id is never a flow-run id. So on Postgres that
seed raised `ForeignKeyViolation` for every waiting unit on every boot since it was written —
not only for FR-29's ten leaked rows. SQLite does not enforce the FK, which is why no test saw it.
The `flow_runs`-exists guard now lives in `ensure_waiting_flow_run_row`, covering both callers;
a non-run id is a DEBUG skip. Your ten rows still exist until you retire them (2.15.0 handoff §4
step 3); they no longer make noise.

---

## 2. Consumer-visible edges — checked against your source

| Change | Where you would feel it | Your source |
|---|---|---|
| Route-sourced `execution_units` rows now end `completed` / `failed`; `executing` means *in flight* from the first request after upgrade | anything counting units by status — `ExecutionConsole.jsx`'s filter | the ~900 `executing` rows already there stay until retired (§4 step 3); the filter's count drops by whatever you retire, and stops growing |
| The `[rehydrate] waiting_flow_runs seed failed … ForeignKeyViolation` lines at boot stop | log noise only | your ten from 2026-09-14 produced ten per boot; zero now |
| Boot Smoke on the published wheel retries the PyPI install (#672) | nothing at runtime | — |

**Not touched, correctly:** `task_service.py:582`'s own `update_status(_eu.id, "waiting")` on
pause is followed by your own commit path; `require_execution_unit` and `to_envelope` are
untouched (the diff under `AINDY/` is `resources.py`, `wait_rehydration.py`, `_version.py`,
and the smoke workflow).

---

## 3. What you can opt into

Nothing new is flagged this release. The 2.13.0 knobs stay default-off with the same guidance.

---

## 4. Verification after the rebuild

```bash
# 1. You are on 2.16.0 — IN THE CONTAINER, path printed
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
#    expect: 2.16.0  ['/usr/local/lib/python3.11/site-packages/AINDY']

# 2. No schema drift (exit 0)
docker exec <api> aindy-runtime bootstrap-schema

# 3. OPTIONAL one-off: retire the route units every prior release left `executing`. Only
#    `source_type='route'` rows created BEFORE the upgrade — anything after it is a real
#    in-flight request (or a real defect, which is what step 4 checks).
psql -c "select type, status, count(*) from execution_units where source_type='route' group by 1,2 order by 1,2;"
psql -c "update execution_units set status='failed' where status='executing' and source_type='route' and created_at < '<upgrade time>';"
#    (2.15.0's `waiting` retire — status='waiting' and source_type='route' — still applies if not yet run.)

# 4. ★ FR-30, live — the fix has not been re-run against a server; this is that run.
#    Any handful of requests, then the table. A Tutorial 2 pass is the shape you measured (19
#    route units per pass on 2.15.0):
psql -c "select type, status, count(*) from execution_units where source_type='route' and created_at > '<upgrade time>' group by 1,2;"
#    expect: only `completed` (and `failed` for any 4xx) — ZERO `executing` once the requests
#    have returned. A row still `executing` after its request answered is the defect; file it
#    with the route name from its trace.

# 5. Boot log: zero `[rehydrate] waiting_flow_runs seed failed` lines.
docker logs <api> 2>&1 | grep -c "seed failed"
```

---

## 5. Version-pin hygiene

Bump `constraints.txt` `aindy-runtime==2.15.0` → `==2.16.0`; your contract test moves the floor
to `>=2.16.0,<3.0` with it. Nothing in this release adds a symbol you import.
