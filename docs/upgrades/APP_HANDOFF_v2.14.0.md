---
title: "App Handoff — Runtime v2.14.0"
api_version: "1.0"
last_verified: "2026-09-14"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.14.0

Floor/pin move `>=2.13.0,<3.0` → bump the `constraints.txt` pin `==2.13.0` → `==2.14.0`.

**Required of you: nothing.** No schema step, no required code change. Four runtime defects in
the WAIT/resume and job-retry paths are fixed, none behind a flag; every consumer-visible edge
of them was checked against your source (§2) and none of your code sits on one. This is a plain
pin bump + rebuild — and then, for once, a verification step that is worth doing (§4): none of
the four fixes has been re-run against a live server yet, and your rebuilt container is the
first one that can.

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.14.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```
>
> `importlib.metadata`, `pip show` and `import AINDY._version` are cwd-sensitive (the 2.11.0
> handoff's correction records both sides of that trap).

---

## 0. Which path are you on?

| If the container reports… | Read |
|---|---|
| **2.9.0 through 2.13.0** | Pin bump + rebuild. No schema step. Go. |
| **2.8.0** | Same, plus the envelope change from `RUNTIME_2_9_0_UPGRADE.md` §1 you skipped. |
| **2.7.0 or earlier** | `RUNTIME_2_11_0_UPGRADE.md` §1 first — you owe 2.8.0's `flow_runs.graph_signature` step (`bootstrap-schema --reconcile`, Alembic `0018`), then this. |

2.14.0 adds no schema: Alembic head `0018`, `SCHEMA_CONTRACT_VERSION` `2026-09-10` unchanged,
`git diff v2.13.0..v2.14.0 -- AINDY/db/models/ AINDY/memory/memory_persistence.py` empty.

---

## 1. What you gain without doing anything

All four were found on 2026-09-13 by running the runtime's own tutorials live against 2.13.0.
Each was verified by tests that drive the real runner (mutation-checked); the live re-run is §4.

- **A suspended Nodus script now receives the payload that resumed it** (#654). The flow runner
  merged SUCCESS node patches only, so a guest's WAIT patch — the one carrying
  `nodus_wait_event_type` — reached `flow_history` and never the run's state; on resume the
  bridge found no pending wait and dropped the injected payload. Guest WAIT/RESUME with a
  payload had never worked. You run no guest script that waits today (`nodus_wait_requested`
  → 0 files under `apps/`), so this is capability you did not have, not behaviour that moved.
  A second, latent defect went with it: `PersistentFlowRunner.resume()` aliased the ORM
  `run.state` dict, so under `expire_on_commit=False` the snapshot never advanced.
- **`POST /platform/flows/runs/{id}/resume` resumes that run only** (#655). It used to inject
  the payload into, and wake, every run parked on the event name — any tenant. The wake is now
  scoped by run id end to end (local scan, Redis message, cross-instance fallback). **Your own
  `POST /apps/agent/runs/{id}/resume` was never affected**: it wakes by the agent run's
  correlation, which is `run_<uuid4>` — unique per run.
- **A waiting flow run no longer holds a tenant concurrency slot** (#656). Four parked waits
  used to 429 every route for the tenant (`AINDY_QUOTA_MAX_CONCURRENT`, default 5). A run now
  holds a slot exactly while it executes; admission is decided once per acquisition rather
  than re-decided per node with the run's own slot in the count; and the run's `ExecutionUnit`
  goes `waiting` while parked. **Not a cap raise.** Your `WaitRecovery` sweep
  (`apps/tasks/bootstrap.py:384`, `notify_event(correlation_id=)`) is untouched — the new
  `run_id` kwarg is optional.
- **An async job whose handler is not registered fails once, terminally** (#657). It was
  re-dispatched in-process ~87×/s forever, uncounted. You register 23 handlers at bootstrap
  (`register_job(` → 23 call sites) and submit no jobs directly (`dispatch_job` → 0), so the only
  way you meet this is a `job_logs` row naming a handler you have since renamed or stopped
  loading — it now fails on its first attempt with `AsyncJobHandlerNotRegistered` in
  `error_message` instead of flooding the log.

---

## 2. Consumer-visible edges — checked against your source, none fire for you

| Change | Where you would feel it | Your source |
|---|---|---|
| A custom node's WAIT `output_patch` now lands on the run's state and is visible to the re-run | any node returning `{"status": "WAIT", …, "output_patch": …}` | your one WAIT node, `genesis_track_message` (`apps/automation/flows/flow_definitions.py:206`), sends no patch; it reads `state["event"]`, which the resume route still injects |
| The Nodus execution record's `nodus_status` is `"waiting"` on a WAIT, not `None` | code branching on `nodus_status is None` | `nodus_apply.py:75` tests `!= "success"` — unaffected |
| The event-bus Redis message gains a `run_id` key (additive) | an instance on 2.13.0 sharing Redis with one on 2.14.0 during a rolling deploy fans out as before; nothing breaks | your `api` and `worker` build from one image; a mixed pair only exists if you roll them separately |
| `ExecutionUnit` rows for parked flow runs read `waiting`, not `executing` | dashboards counting `executing` EUs | count drops for parked runs — correct, not a regression |
| `AINDY_RETRY_BACKOFF_BASE_MS` (1000) / `_MAX_MS` (30000) now apply to thread-mode job retries | jobs submitted with `max_attempts > 1` | you submit no jobs directly; your `AutomationLog.max_attempts` default of 3 is your own table, not the runtime's `JobLog` |
| sdist ships `docs/operations/` + `docs/governance/`; package `Documentation` URL → `docs/`; `examples/openclaw/` removed | nothing at runtime | — |

---

## 3. What you can opt into

Nothing new is flagged this release. The 2.13.0 knobs (`AINDY_QUOTA_MAX_TENANT_TOKENS`,
`AINDY_QUOTA_MAX_TOKENS`, `AINDY_RUN_SCOPED_QUOTA`) stay default-off with the same guidance.
`AINDY_RETRY_BACKOFF_BASE_MS=0` restores immediate thread-mode retries if you ever want them.

---

## 4. Verification after the rebuild — this one matters

```bash
# 1. You are on 2.14.0 — IN THE CONTAINER, path printed
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
#    expect: 2.14.0  ['/usr/local/lib/python3.11/site-packages/AINDY']

# 2. No schema drift (exit 0)
docker exec <api> aindy-runtime bootstrap-schema   # exit 0; 3 = additive reconcile owed (not this release), 4 = stop

# 3. No job storm on boot. If any `job_logs` row named a handler you no longer register, it
#    fails ONCE now. Expect at most one line per such row, and then silence:
docker logs <api> 2>&1 | grep -c "failed TERMINALLY"
docker logs <api> 2>&1 | grep -c "is not registered"      # must not keep growing

# 4. ★ The live run nobody has done yet: Tutorial 2, end to end, against YOUR container.
#    docs/tutorials/02-event-driven-automation.md — Steps 1–6. On 2.13.0 it stops at Step 6
#    (history WAIT, WAIT); on 2.14.0 the documented "after the fix" output is
#      status=success  received={'review.approved': {...}}   history: ['WAIT', 'SUCCESS']
#    and the resume response's `results` has exactly ONE entry. That exercises #654, #655 and
#    #656 in one pass (the tutorial's parked runs used to 429 the tenant before Step 6).

# 5. Parked runs cost nothing: with a run waiting (Step 5 above), any read-only GET for the
#    same tenant answers 200, and it still does after four of them.
```

If Step 4 does not produce `['WAIT', 'SUCCESS']`, do not debug the tutorial: file it against the
runtime with the run's history — every response key on that page was read from a live 2.13.0
server, and the after-the-fix output is stated from the runtime's second-run test.

---

## 5. Version-pin hygiene

Bump `constraints.txt` `aindy-runtime==2.13.0` → `==2.14.0`. The `pyproject.toml` floor
`>=2.13.0,<3.0` can stay — nothing in this release adds a symbol you import — or move to
`>=2.14.0` if you want the four fixes guaranteed present wherever the app is installed.
`pip install -c constraints.txt` keeps your dev venv on what your container runs.
