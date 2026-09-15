---
title: "App Handoff — Runtime v2.15.0"
api_version: "1.0"
last_verified: "2026-09-14"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.15.0

Pin bump `constraints.txt` `==2.14.0` → `==2.15.0`. Floor `>=2.14.0,<3.0` can stay.

**Required of you: nothing.** No schema step, no required code change. This release closes
**your FR-29** (filed 2026-09-14 from your live Tutorial 2 run; `WAIT-DETECT-SHAPE-1`, #670)
and takes eight dependabot bumps (#669). One unflagged behaviour change, on a surface you read
but do not branch on (§2). One optional one-off cleanup (§4 step 3) for the rows every release
before this one leaked.

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.15.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Which path are you on?

| If the container reports… | Read |
|---|---|
| **2.9.0 through 2.14.0** | Pin bump + rebuild. No schema step. Go. |
| **2.8.0** | Same, plus the envelope change from `RUNTIME_2_9_0_UPGRADE.md` §1 you skipped. |
| **2.7.0 or earlier** | `RUNTIME_2_11_0_UPGRADE.md` §1 first — you owe 2.8.0's `flow_runs.graph_signature` step (`bootstrap-schema --reconcile`, Alembic `0018`), then this. |

2.15.0 adds no schema: Alembic head `0018`, `SCHEMA_CONTRACT_VERSION` `2026-09-10` unchanged,
`git diff v2.14.0..v2.15.0 -- AINDY/db/models/ AINDY/memory/memory_persistence.py` empty.

---

## 1. FR-29 — what was fixed, and the two things your diagnosis could not see

Your diagnosis was right to the line and is not repeated here. The intake reproduced it at
the route, on both server shapes, before the detector was touched
(`tests/unit/test_wait_detect_reader_park_fr29.py` — every route case calls the route through
the booted app and reads back the unit the envelope names).

- **`_detect_wait` now honours `ExecutionWaitSignal` only** — raised, or returned. The dict
  branch (any handler result with `status: "WAITING"`) is gone. A request's execution unit
  describes the request: when the handler returns, it completes. The run it read or started
  carries its own wait on `flow_runs` and its own execution unit (#656).
- **`_persist_wait_backup` checks the id names a `flow_runs` row before merging** (your ask 3).
  A non-run id is a DEBUG skip, not a WARNING that reads like data loss.
- **Your ask 2 — a path out for a "legitimately" parked request unit — is answered by
  construction, not built, because the half you called legitimate never was.** The dict
  branch was written for `POST /platform/nodus/run` on a suspending script, and it never
  worked: `_format_execution_response` nests `waiting_for` under `data`, so the detector read
  neither `wait_for` nor `waiting_for` and parked the request on the literal event `"unknown"`.
  And even a correctly named wait had nowhere to go — `resume_execution_unit` moves a unit
  `waiting → resumed → executing`, and nothing re-executes a returned request. **The dict path
  parked units; it never once resumed one**, on any release. That is your two `job|route` rows
  on `"unknown"`, and it is almost certainly the 105 `job|route` / `flow|route` units our own
  2026-09-13 tutorial run left "undiagnosed".
- **Your `flow_run_get` result key can stay.** It no longer arms anything. Do not drop it on
  our account — it is a response-shape decision that is yours.
- One precision on your census: `getFlowRun` *is* exported (`client/src/api/operator.js:12`,
  `ROUTES.OPERATOR.FLOW_RUN`), which a grep for `flows/runs` cannot see; no component calls
  it, so your "nothing of ours calls the route" holds — by one dead export.

---

## 2. Consumer-visible edges — checked against your source

| Change | Where you would feel it | Your source |
|---|---|---|
| Pipeline envelope of `POST /platform/nodus/run` (and any route returning a `WAITING` record) says `status: "success"` with `data.status: "WAITING"`; was `status: "waiting"` + `metadata.eu_wait_for: "unknown"` | code branching on the envelope's top-level `status`, or on `eu_wait_for` | your routers read `result.data.status` (`goals_router.py:41`, `freelance_router.py:154`, `leadgen_router.py:61`, `research_results_router.py:67`) — unaffected; `eu_wait_for` → 0 hits |
| A request's trace carries `execution.completed`, not `execution.waiting`, when it reads or starts a waiting run | the rippletrace causal graph for such a request | the `flow.waiting` event on the run is unchanged — that is the wait |
| `execution_units` stops gaining one `waiting` row per read of a parked run / per suspending `nodus/run` | dashboards counting units by status; `ExecutionConsole.jsx`'s `waiting` filter | the count stops growing; the rows already there stay (§4 step 3) |
| `[Scheduler] waiting backup write failed … ForeignKeyViolation` stops appearing on reads | log noise only | — |
| `click` 8.5.0, `jiter` 0.16.0, `psycopg2` 2.9.13, `tqdm` 4.70.1; SPA `react` 19.3.0 / `vite` 8.3.0 | nothing expected | your `constraints.txt` pins only `aindy-runtime`; these resolve through it |

**Not touched, deliberately:** your `task_service.py:582` moves a task's own unit to `waiting`
by hand on pause. That is a unit *you* own describing a thing *you* are waiting on — the
opposite of the defect — and nothing here changes what `ExecutionUnitService.update_status`
does.

---

## 3. What you can opt into

Nothing new is flagged this release. The 2.13.0 knobs (`AINDY_QUOTA_MAX_TENANT_TOKENS`,
`AINDY_QUOTA_MAX_TOKENS`, `AINDY_RUN_SCOPED_QUOTA`) stay default-off with the same guidance.

---

## 4. Verification after the rebuild

```bash
# 1. You are on 2.15.0 — IN THE CONTAINER, path printed
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
#    expect: 2.15.0  ['/usr/local/lib/python3.11/site-packages/AINDY']

# 2. No schema drift (exit 0)
docker exec <api> aindy-runtime bootstrap-schema

# 3. OPTIONAL one-off: retire the units every prior release leaked. Only `source_type='route'`
#    rows — run-level rows (flow|scheduler, job|scheduler, agent|…) are real waits, leave them.
#    Your ten from 2026-09-14 are here; so is anything the operator console read before.
psql -c "select type, source_type, wait_condition->>'event_name' as ev, count(*) from execution_units where status='waiting' group by 1,2,3;"
psql -c "update execution_units set status='failed' where status='waiting' and source_type='route';"

# 4. ★ FR-29, live — the fix has not been re-run against a server; this is that run.
#    Tutorial 2 Steps 1–6 as you ran them on 2.14.0. The tutorial's OUTPUT is unchanged; the
#    thing to read this time is the table afterwards. With the run parked (Step 3), read it
#    eight times — the count on the first line of §3's SELECT must not move — and after Step 6:
psql -c "select type, source_type, status from execution_units where status='waiting';"
#    expect: NO `route` rows. The run's own unit (flow, non-route) is `waiting` while parked and
#    leaves `waiting` when the run completes. Zero `waiting backup write failed` lines in the log.
```

If step 4 shows a `route` row in `waiting` after a read, file it with the row's
`wait_condition` and the request's trace id — every response key on the tutorial page was read
live and the after-the-fix behaviour is stated from route-level tests, not inferred.

---

## 5. Version-pin hygiene

Bump `constraints.txt` `aindy-runtime==2.14.0` → `==2.15.0`. The `pyproject.toml` floor
`>=2.14.0,<3.0` can stay — nothing in this release adds a symbol you import.
