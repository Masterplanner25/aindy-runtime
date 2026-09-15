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

---

## 6. Verified by the app team — 2026-09-14, same day (written from `aindy-apps-monolith`)

Adopted as `aindy-apps-monolith` #358 (pin + floor `2.14.0`, `docs/runtime/RUNTIME_2_14_0_UPGRADE.md`
there is the full record). Image `41642f6427f3` rebuilt on the pin, stack up on a 430 MB host.
Every §4 check, each read rather than assumed:

| §4 check | Result |
|---|---|
| 1. version + path, in the container | `2.14.0 ['/usr/local/lib/python3.11/site-packages/AINDY']` |
| 2. `bootstrap-schema` | exit 0; heads runtime `0018`, app `ga1shadow0001` unchanged |
| 3. job storm | `failed TERMINALLY` 0, `is not registered` 0 — at boot and after 12 h up. `[job_recovery] re-dispatched 3 orphaned thread-mode job(s)`, all three handlers registered |
| 4. ★ **Tutorial 2, Steps 1–6, live** | **passes** — the first live run of that page anywhere. Output below |
| 5. parked runs cost nothing | with a run `waiting`, four `GET /platform/flows/runs/{id}` → `[200, 200, 200, 200]` |

**Step 4, observed** (test account, promoted to `is_admin` for the run and reverted; the
tutorial's `.nd` verbatim; the harness accepts both the documented envelope and the bare shapes
an app-profile server returns — see the note after):

```
Starting script (phase 1 - will suspend)...
  Flow status:  WAITING      Nodus status: waiting        ← was None on 2.13.0 (§2 row 2)
Flow run: status=waiting waiting_for=review.approved
Approving...
  {'run_id': 'f40116a3-…', 'resumed': True, 'results': [{'run_id': 'f40116a3-…', 'payload_injected': True}], …}
  results entries: 1                                     ← #655
Watching the run after resume...
  status=success   waiting_for=None  received={'review.approved': {'reviewer': 'shawn', 'approved': True, 'note': 'Ship it.'}}
  history: ['WAIT', 'SUCCESS', 'SUCCESS']
  nodus_output_state: {'nodus_received_events': {…}, 'outcome': 'approved'}
Insights:  • Sprint-12 tasks approved by shawn. Note: Ship it.
```

So #654, #655 and #656 hold on a live server. Three things the one-line expectation in §4 does
not say, for the next reader:

- **`history` has three rows, not `['WAIT', 'SUCCESS']`, and that is correct.** The
  `nodus.execute` node's rows are exactly `WAIT, SUCCESS`; the third `SUCCESS` is
  `nodus_record_outcome`, the runtime's own follow-on node (`runtime/nodus_adapter.py`). Read
  node names before filing the three-row history as a defect. Suggest §4 say so.
- **#655 was checked the direct way.** A second run was already parked on `review.approved`
  from a first attempt; resuming `f40116a3…` left it `waiting`. It was then resumed with
  `approved: false` → `success`, `outcome: rejected` — the rejection branch works too.
- **On an app-profile server the flow routes answer the bare result, not the envelope.** The
  app registers `register_flow_result("flow_run_get", result_key="flow_run_get_result")` and a
  `raw_json_adapter` for the `flow` prefix, so `GET …/runs/{id}` is the row itself, `…/history`
  is `{"run_id", "history"}`, `…/resume` is `{"run_id", "resumed", "results",
  "execution_envelope"}`. Tutorial 2's `["data"]["flow_run_get_result"]` is right for a
  platform-only server and a `KeyError` on this one. Not a runtime defect — recorded so the next
  person running the tutorial against an app image knows which shape they are looking at.

**Found while looking — filed as the app's `RUNTIME_FEATURE_REQUESTS.md` FR-29, for your
intake.** Eight reads of the parked run produced eight
`[Scheduler] waiting backup write failed … ForeignKeyViolation … waiting_flow_runs_run_id_fkey`
WARNINGs, and afterwards ten `execution_units` rows sat `waiting` — eight `flow|route` (one per
GET of the parked run, on `review.approved`) and two `job|route` (the two `POST
/platform/nodus/run` requests, on `"unknown"`) — still `waiting` after both runs finished. The
"run id" in each warning is the **reader's execution-unit id**: `execution_pipeline/waits.py::
_detect_wait` treats any handler result dict with `status == "WAITING"` as the request itself
waiting, parks the request's EU, and the wait registration's backup write then tries to insert
`waiting_flow_runs(run_id=<eu id>)`. Nothing completes those units. **The trigger for the GET
half is the app's result-key registration above** — the bare row's own `status: waiting` lands
where the detector reads; on your platform-only server the row is nested under
`flow_run_get_result` and the branch never fires, which is why your live run did not see it.
The `nodus.run` half needs nothing of the app's. None of the three files involved changed
between v2.13.0 and v2.14.0 — pre-existing, not a regression. The ask is in FR-29; the
one-line app-side sidestep (drop that result key) is recorded there as the owner's call. Do
not count `waiting` EUs as parked runs until one side moves.
