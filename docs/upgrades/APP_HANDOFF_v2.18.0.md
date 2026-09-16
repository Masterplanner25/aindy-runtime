---
title: "App Handoff — Runtime v2.18.0"
api_version: "1.0"
last_verified: "2026-09-16"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.18.0

Pin bump `constraints.txt` `==2.17.0` → `==2.18.0` (your contract test moves the floor with it).

**Required of you: nothing.** No schema step, no required code change. This release closes
**your FR-31** (filed 2026-09-16 from your 2.17.0 verification) and ships three runtime-side
fixes/cleanups behind it. One consumer-visible change on a route you do not call (§2), one removal
you never imported (§3), and your 2.17.0 asks are unchanged (§5).

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.18.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Which path are you on?

| If the container reports… | Read |
|---|---|
| **2.9.0 through 2.17.0** | Pin bump + rebuild. No schema step. Go. |
| **2.8.0** | Same, plus the envelope change from `RUNTIME_2_9_0_UPGRADE.md` §1 you skipped. |
| **2.7.0 or earlier** | `RUNTIME_2_11_0_UPGRADE.md` §1 first — you owe 2.8.0's `flow_runs.graph_signature` step (`bootstrap-schema --reconcile`, Alembic `0018`), then this. |

2.18.0 adds no schema: Alembic head `0018`, `SCHEMA_CONTRACT_VERSION` `2026-09-10` unchanged,
`git diff v2.17.0..v2.18.0 -- AINDY/db/models/ AINDY/memory/memory_persistence.py` empty.

---

## 1. FR-31 — what was fixed, and what your diagnosis found that we had not

Your mechanism was verified line for line: `nodus_execute` registered lazily on the first script
run; the rehydrated callback looking the flow up at wake time; the wake consumed before the miss.
**Pre-existing on every release** — every earlier live run parked and resumed inside one process
lifetime.

- **Your ask 1 — registered at boot.** `nodus_execute` is registered by `register_all_flows()`
  (the API's flow-engine phase) and by the FR-15 worker's boot, which had never registered the
  runtime-owned flows at all. Both resume builders also resolve it on a miss.
- **Your ask 2 — a skipped resume no longer consumes the registration.** A flow this process
  genuinely does not hold (a plugin flow not loaded here) re-arms the wait under the run id; the
  run is never claimed; the next wake resumes it. The WARNING now says `the wait is RE-REGISTERED`.
- **Your ask 3 — the route says whether anything was woken.** See §2.
- **What reading your filing found:** `agent_execution` — the AGENT_FLOW backend's flow name, the
  one your runs use — was **never** in `FLOW_REGISTRY`. So an agent run parked by the 2.17.0
  authority WAIT gate could not have survived a restart either. It now resolves for **resume
  only** (`resolve_resumable_flow`) and is deliberately not registered publicly: a `FLOW_REGISTRY`
  entry is startable through `sys.v1.flow.run`, and the agent flow checks tool capability only
  when the state carries an `execution_token` — a public registration would have let a `flow.run`
  holder run tools with no token (DEC-020).

**Your §5 step 4 order ("run a script first") is retired.** On 2.18.0 the handoff's original
step — park on the old image, upgrade, resume — passes on a fresh boot. §4 step 4 below is that
check, unmodified.

---

## 2. Consumer-visible edges — checked against your source

| Change | Where you would feel it | Your source |
|---|---|---|
| **`POST /platform/flows/runs/{id}/resume`: each result carries `woken: bool`, and `resumed` now means *woken*, not "payload stored"** (#689). A resume with nothing registered to wake answers `200` with `payload_injected: true, woken: false, resumed: false` and a WARNING; the payload stays on the row and the next wake after rehydration delivers it | any client that keyed on `resumed: true` to mean "stored" | **nothing of yours calls this route** (your 2.17.0 adoption: `flows/runs` → 0 hits; `getFlowRun` read-only, uncalled). `Genesis.jsx:152`'s `data.resumed` is the Genesis session's own field, not this route |
| On a multi-instance deployment in thread mode, a wait claimed from an instance that died is now actually resumed (#686) | only if you run more than one api instance — you run one | — |
| A flow run completing with no `user_id` / `workflow_type` on its row now finalises its execution unit (#685) | your route-started flows always carry both | no change expected in your `execution_units` counts |
| Boot log: the `[rehydrate] … waiting EU(s)` lines are gone; `wait_eus_rehydration_failed` is retired (listed, never emitted) | log-shape only | if a dashboard filters on that code it keeps parsing and never fires |

**Not touched:** the pipeline envelope shape, `require_execution_unit`, `to_envelope`,
`register_tool`, `run_flow`, every route path and status code you consume today.

---

## 3. Removed — `AINDY.runtime.nodus_builtins` and `nodus_worker.WorkerWaitSignal` (#688)

**You never imported either** (your 2.17.0 adoption grepped `apps/ tests/ client/src` for the
module: 0). The guest `event.wait()` / `memory.*` namespaces it documented could never have
worked: a host-function exception does not propagate out of the nodus guest on any version, and
`wait` is a reserved nodus name. The guest wait is now one call:

```js
let approval = await_event("review.approved", <schema or nil>)   // halts here on run 1; returns the payload on the resumed run
```

The three state keys (`nodus_wait_requested`, `nodus_wait_event_type`, `nodus_wait_resume_schema`)
remain the wire contract and still work if set directly. Everything before `await_event()` runs
again on resume (DEC-012) — the `if (get_state("nodus_received_events") == nil)` guard remains the
safe pattern when phase 1 has effects. You run no waiting guest script today; nothing to change.

---

## 4. Verification after the rebuild

```bash
# 1. You are on 2.18.0 — IN THE CONTAINER, path printed
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
#    expect: 2.18.0  ['/usr/local/lib/python3.11/site-packages/AINDY']

# 2. No schema drift (exit 0)
docker exec <api> aindy-runtime bootstrap-schema

# 3. Boot log: the two lines this release retires are absent; nothing new at WARNING or above
docker logs <api> 2>&1 | grep -cE "\[rehydrate\] (Found|WAIT rehydration registered)"    # expect 0

# 4. ★ FR-31 live, the handoff's original order, no workaround: park a Tutorial 2 run on 2.17.0
#    (Steps 1–3), upgrade, and resume it FIRST — before any script has run in the new process.
curl -X POST .../platform/flows/runs/<parked>/resume -d '{"event_type":"review.approved","payload":{"reviewer":"you","approved":true,"note":"after restart"}}'
#    expect in the response:  resumed: true, results: [{…, payload_injected: true, woken: true}]
psql -c "select status from flow_runs where id='<parked>';"     # expect: success (within seconds)
docker logs <api> 2>&1 | grep -c "not in FLOW_REGISTRY"          # expect 0

# 5. The failure shape, deliberately: resume a run that has NO registered wait (e.g. the same
#    run again, now `success`) — the route must say so rather than claim a resume.
#    (A run that is not `waiting` is a 400 from the ownership check; to see `woken: false` you
#    need a `waiting` row with no scheduler entry, which a fresh boot BEFORE rehydration produces
#    for ~a second. Optional — the unit test pins it; skip unless you want to see the wire.)
```

---

## 5. Your two 2.17.0 asks — unchanged, still yours

Recorded in your `RUNTIME_2_17_0_UPGRADE.md` §3 as owner's calls, correctly. Nothing in 2.18.0
changes their shape or cost:

- **Named predicates (#680):** migrate the seven lambdas to `when` **behind a drain** (the
  flow's digest moves once).
- **Authority gate (#681):** declare `on_denial="wait"` or a `degraded_variant` on one real tool
  so phase 3 has a first denial to flip on. **2.18.0 makes this safer to try:** a gate-parked run
  now survives a restart (§1), which it could not have on 2.17.0.

---

## 6. Version-pin hygiene

Bump `constraints.txt` `aindy-runtime==2.17.0` → `==2.18.0`; your contract test moves the floor
to `>=2.18.0,<3.0` with it. Symbols removed: `AINDY.runtime.nodus_builtins.*`,
`AINDY.runtime.nodus_worker.WorkerWaitSignal` (you import neither). New symbols you may import:
`AINDY.runtime.nodus_execution_service.resolve_resumable_flow`, `ensure_runtime_flows_registered`.
