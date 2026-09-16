---
title: "App Handoff — Runtime v2.17.0"
api_version: "1.0"
last_verified: "2026-09-16"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.17.0

Pin bump `constraints.txt` `==2.16.0` → `==2.17.0` (your contract test moves the floor with it).

**Required of you: nothing.** No schema step, no required code change — your 22 `register_tool`
calls, your 7 conditional-edge lambdas and your `to_envelope` imports are all unchanged surfaces.
This release is six runtime-side items from our own backlog: one **removal** (§1) you never used,
**three unflagged behaviour changes** on paths you do use (§2), and three **opt-ins** (§3) — two
of which carry an ask for you (§3.1, §3.3), on your schedule.

> ## ★ Confirm what you are actually running — in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.17.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Which path are you on?

| If the container reports… | Read |
|---|---|
| **2.9.0 through 2.16.0** | Pin bump + rebuild. No schema step. Go. |
| **2.8.0** | Same, plus the envelope change from `RUNTIME_2_9_0_UPGRADE.md` §1 you skipped. |
| **2.7.0 or earlier** | `RUNTIME_2_11_0_UPGRADE.md` §1 first — you owe 2.8.0's `flow_runs.graph_signature` step (`bootstrap-schema --reconcile`, Alembic `0018`), then this. |

2.17.0 adds no schema: Alembic head `0018`, `SCHEMA_CONTRACT_VERSION` `2026-09-10` unchanged,
`git diff v2.16.0..v2.17.0 -- AINDY/db/models/ AINDY/memory/memory_persistence.py` empty.

---

## 1. Removed — `ExecutionWaitSignal` (#679)

`AINDY.core.execution_gate.ExecutionWaitSignal` is gone. **You never imported it** (checked:
your only `execution_gate` imports are `to_envelope`, three routers — unchanged). It let a route
handler park the *request's* execution unit "to be resumed later", a promise nothing could keep:
a route has answered its client before it can raise, nothing re-executes a request, and its
resume callback flushed and closed without committing anyway. A request's `execution_units` row
now completes when its handler returns, by every path.

**One optional cleanup follows.** The ~10 `waiting` route units you kept as FR-29 evidence are
now unreachable by any mechanism (nothing can transition them). Retire when you are done with
them:

```sql
update execution_units set status='failed' where status='waiting' and source_type='route';
```

---

## 2. Unflagged behaviour changes on paths you use — checked against your source

| Change | Where you would feel it | Your source |
|---|---|---|
| **`POST /platform/flows/runs/{id}/resume` no longer silently fails to wake the run when the payload carries a `correlation_id` key** (#678) | any resume whose payload has a client-side `correlation_id` field — before, the payload landed on the row and the wake was vetoed by that key: `resumed: true` on the wire, run parked forever | your own `/runs/{run_id}/resume` (`agent_router.py:249`) is the *agent* resume (`resume_agent_run_runtime` → `publish_event`), not this route; the tutorials and the console use this one. **If you have runs stuck `waiting` with `state.event` already set, this is why — resume them again** |
| **The same route can answer `422`** when the waiting node declared a `resume_schema` and the payload does not satisfy it (#677) | only a run whose flow/script opted in; **none of yours does today**, so nothing changes until one does | your routers branch on `!= "success"` already (2.9.0 handoff); a 422 is refused-before-anything-happened — the run stays `waiting`, correct and retry |
| **A cancelled agent run stops sooner** (#682) | `sys.v1.agent.cancel` → the run's next *syscall* is refused before its handler runs, and an **isolated** tool's worker (`register_tool(isolation=…)` — you declare none today) is terminated and killed instead of running to its 120 s budget | your `ExecutionConsole` may show more refused effects / fewer completed steps on cancelled runs; that is the mechanism, measurable on `aindy_run_cancel_observed_total{surface}` (new labels `syscall`, `tool_worker`) |

**Not touched:** the pipeline envelope shape, `require_execution_unit`, `to_envelope`, every
route path and status code you consume today except the 422 above (which no run of yours can
yet produce).

---

## 3. What you can opt into — and two asks

### 3.1 Named flow predicates (#680) — **ask: migrate your seven lambdas, behind a drain**

`{"target": …, "when": "<name>"}` + `@register_predicate("<name>")` beside today's
`{"target": …, "condition": <lambda>}`. `"default"` is built in (your `lambda s: True`
fall-throughs). Why it is worth doing: the graph signature now includes a named decision, so a
predicate you *rename or reroute* between suspend and resume quarantines the suspended run
instead of resuming it into a decision it was never planned for. Your lambdas are the blind spot
`FLOW-GRAPH-SIGNATURE-1` documented; naming them closes it for your flows.

**★ The cost, and why it is your call and not ours:** converting an edge from `condition` to
`when` changes that flow's graph signature **once**, so any run suspended on that flow at
upgrade time is quarantined (dead-lettered with a reason). Migrate a flow when it has no parked
runs — drain, deploy, done. Your seven: `automation/flows/flow_definitions.py:723`,
`automation/flows/watcher_flows.py:160,164,168`, `tasks/flows/tasks_flows.py:465,469,473` (the
two `watcher_decision` switches are the same shape twice: `== "execute"`, `== "defer"`,
`default`). Every existing flow's digest is byte-for-byte unchanged by the upgrade itself.

### 3.2 Typed wait payloads (#677) — optional, no ask

A flow node returning `WAIT` may declare `resume_schema` (syscall dialect: `required` +
`properties[<name>].type`); a Nodus script sets `nodus_wait_resume_schema` beside its two wait
keys. A resume that does not satisfy it is refused with 422 before anything is injected or
woken. Adoption is visible on `aindy_flow_resume_payload_total{outcome="accepted|rejected|untyped"}`
— `untyped` is every wait today.

### 3.3 The authority WAIT gate (#681) — **ask: declare one, so phase 3 has evidence**

`register_tool(..., on_denial="wait")` (default `"fail"`). When a tool is refused for lack of
authority and no `degraded_variant` recovers it, the agent run **parks** instead of failing —
`AgentRun.status = "waiting"`, `wait_state {event_type, flow_run_id, authority_gate}`, the
accumulated steps intact — and an operator resumes it:

```
POST /platform/flows/runs/{wait_state.flow_run_id}/resume
{"event_type": "agent.authority.decision", "payload": {"decision": "skip" | "abort", "note": "…"}}
```

`skip` records the step `skipped` and continues; `abort` fails the run with your reason. There is
no `grant` — the gate cannot widen authority; an unknown decision re-parks the run; a payload
without `decision` is 422. Behind the existing `AINDY_AUTHORITY_NEGOTIATION` (default off).

**The ask:** `AUTHORITY-NEGOTIATION-1` phase 3 is "flip the default once a real tool declares a
variant or a gate and a denial has been observed." **Zero tools declare either — including your
22.** Pick one whose denial you would rather have parked than failed (the ones that send or
spend are the natural candidates), declare `on_denial="wait"` or a `degraded_variant`, turn the
flag on in a non-production profile, and tell us what the first denial looked like. Until a real
tool declares one, the mechanism has no evidence to flip on.

---

## 4. Verification after the rebuild

```bash
# 1. You are on 2.17.0 — IN THE CONTAINER, path printed
docker exec <api> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
#    expect: 2.17.0  ['/usr/local/lib/python3.11/site-packages/AINDY']

# 2. No schema drift (exit 0)
docker exec <api> aindy-runtime bootstrap-schema

# 3. Nothing of yours imports the removed name (expect no output)
grep -rn "ExecutionWaitSignal" apps/

# 4. Every existing flow's signature is unchanged — a run parked before the upgrade must resume,
#    not quarantine. Park one on 2.16.0 (Tutorial 2 Steps 1–5), upgrade, resume it (Step 5):
psql -c "select id, status, dead_letter_reason from flow_runs where status in ('waiting','dead_letter') order by created_at desc limit 5;"
#    expect: the parked run resumes and completes; ZERO new dead_letter rows from the upgrade.

# 5. OPTIONAL: retire the unreachable `waiting` route units (§1)
psql -c "update execution_units set status='failed' where status='waiting' and source_type='route';"

# 6. ★ #678 live — resume with a payload that carries your own correlation_id key:
#    on 2.16.0 the run stayed waiting with state.event set; on 2.17.0 it wakes.
curl -X POST .../platform/flows/runs/<parked>/resume -d '{"event_type":"review.approved","payload":{"reviewer":"you","approved":true,"note":"x","correlation_id":"my-ref-1"}}'
psql -c "select status from flow_runs where id='<parked>';"    # expect: not 'waiting'
```

---

## 5. Version-pin hygiene

Bump `constraints.txt` `aindy-runtime==2.16.0` → `==2.17.0`; your contract test moves the floor
to `>=2.17.0,<3.0` with it. **One symbol was removed** (`ExecutionWaitSignal`, §1) and you do not
import it; new symbols you may import: `AINDY.runtime.flow_engine.register_predicate`,
`DEFAULT_PREDICATE`, `PREDICATE_REGISTRY`; `register_tool(on_denial=)`.
