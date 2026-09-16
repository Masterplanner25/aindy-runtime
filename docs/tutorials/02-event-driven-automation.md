---
title: "Tutorial 2 — Event-Driven Automation"
last_verified: "2026-09-16"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Tutorial 2 — Event-Driven Automation

**Time:** ~8 minutes
**Difficulty:** Intermediate
**What you'll build:** A Nodus script that suspends its run until a human approves, then
finishes the work with the approval payload — no polling, no thread, no process held open.

> **★ Read this first — version matters for Step 6.** Run live against **2.13.0** on
> 2026-09-13, Steps 1–5 worked exactly as shown and **Step 6 did not complete**: the re-run
> script never saw the approval and suspended again. That was a runtime defect, not a tutorial
> error — `NODUS-RESUME-BRIDGE-1`: the node's WAIT output patch was recorded in `flow_history`
> but never merged into the run's state, so the bridge that hands the payload to the script
> could never fire. Guest WAIT/RESUME with a payload had never worked, through any path.
> **Fixed on `main` 2026-09-13; the first release after 2.13.0 carries it** (check
> `CHANGELOG.md`). On 2.13.0 this page still stops where it says it stops, and shows you how to
> observe that. Step 6's "after the fix" output below comes from the runtime's own
> second-run test (`tests/unit/test_nodus_resume_bridge.py`), which drives this exact script
> shape through start → wait → resume — not yet from a second live run of this page.
>
> **Run live on 2.14.0 by the app team, 2026-09-14, against their rebuilt container: Steps 1–6
> complete as documented** — `status=success`, `received={'review.approved': {…}}`, one
> `results` entry, insight written (`docs/upgrades/APP_HANDOFF_v2.14.0.md` §6). Two things that
> run adds to Step 6: `history` is `['WAIT', 'SUCCESS', 'SUCCESS']` — the third row is
> `nodus_record_outcome`, the runtime's follow-on node, not a re-execution — and on an
> **app-profile** server the flow routes answer the bare result (the run row itself, not
> `data.flow_run_get_result`) because the app registers a result key and a raw adapter for
> them; the `["data"][…]` reads on this page are for a platform-only server.

---

## Goal

```
Script runs → loads tasks → writes "pending" → SUSPENDS the run
      (you approve)
Script runs AGAIN from the top → sees the approval → writes the insight → emits
```

Read that second line carefully. **A guest script does not pause mid-line and continue. It
sets two state keys, exits, and when the resume arrives the runtime runs the same script again
with the payload in state.** Your script has to be written in two phases that branch on whether
the payload is there. Nothing is held open in between — the run is a row in `flow_runs` with
`status = 'waiting'`, and it survives a server restart.

---

## How WAIT / RESUME works

```
   first run                                         second run
   ─────────                                         ──────────
   script: get_state("nodus_received_events") → nil  script: get_state(...) → {"review.approved": {...}}
   script: set_state("nodus_wait_requested", true)   script: does the approved work
   script: set_state("nodus_wait_event_type",
                     "review.approved")
   script exits
   runtime: FlowRun.status → waiting
   runtime: SchedulerEngine.register_wait(
              event="review.approved",
              correlation_id=<run.trace_id>)
                    ·
                    ·   POST /platform/flows/runs/{run_id}/resume
                    ·   {"event_type": "review.approved", "payload": {...}}
                    ·
   runtime: payload → state["event"] → state["nodus_received_events"]["review.approved"]  ← fixed 2026-09-13; broken on 2.13.0
   runtime: re-enqueues the run; the nodus.execute node runs the script again
```

Three facts decide how you resume it, all checked against source and then against a live server:

- **The event bus carries no payload.** `sys.v1.event.emit` can *wake* a waiting run, but the
  resume callback is zero-argument — the re-run script finds nothing. Payload injection exists
  only in `route_event`, which only `POST /platform/flows/runs/{run_id}/resume` calls.
- **The wait is correlation-keyed to the run's own `trace_id`**, so an emit from a separate
  request (which carries *its* trace id) is skipped. The resume route sends none and matches.
- **The injected payload reaches the script only if the runner kept the node's WAIT patch.**
  The route writes `state["event"]`; the `nodus.execute` node bridges that into
  `nodus_received_events` only if `state["nodus_wait_event_type"]` is set, and that key lives
  in the node's WAIT output patch. **On 2.13.0 the runner merged SUCCESS patches only**, so the
  patch reached `flow_history` and never `flow_runs.state`, and the payload was dropped before
  the script ran (`NODUS-RESUME-BRIDGE-1`). Since 2026-09-13 the runner merges WAIT patches
  too, and a payload-less wake (the event bus) logs a WARNING instead of re-waiting silently.

The route needs `platform.admin` — the admin JWT from the prerequisites has it.

---

## Step 1 — Write the two-phase script

Create `wait_resume.nd`:

```js
// Tutorial 2 — two-phase: runs once, suspends, runs AGAIN from the top on resume
let received = get_state("nodus_received_events")
if (received == nil) {
    // ── Phase 1: first run ────────────────────────────────────────────────
    let result = sys("sys.v1.memory.read", {"path": "/memory/" + user_id + "/tasks/**", "limit": 20})
    let task_count = 0i
    if (result["status"] == "success") { task_count = len(result["data"]["nodes"]) }

    sys("sys.v1.memory.write", {
        "path": "/memory/" + user_id + "/pending/decision",
        "content": "Pending review: " + str(task_count) + " tasks loaded for sprint-12",
        "tags": ["pending", "awaiting-approval"],
        "node_type": "decision"
    })

    set_state("task_count", task_count)
    set_state("nodus_wait_requested", true)      // ← the suspend
    set_state("nodus_wait_event_type", "review.approved")
} else {
    // ── Phase 2: resumed with the approval payload ────────────────────────
    let approval = received["review.approved"]
    let reviewer = approval["reviewer"]
    let note = approval["note"]

    if (approval["approved"] == true) {
        sys("sys.v1.memory.write", {
            "path": "/memory/" + user_id + "/insights/decision",
            "content": "Sprint-12 tasks approved by " + reviewer + ". Note: " + note,
            "tags": ["approved", "sprint-12", reviewer],
            "node_type": "decision"
        })
        sys("sys.v1.event.emit", {
            "event_type": "sprint.review.completed",
            "payload": {"reviewer": reviewer, "approved": true}
        })
        set_state("outcome", "approved")
    } else {
        sys("sys.v1.memory.write", {
            "path": "/memory/" + user_id + "/insights/decision",
            "content": "Sprint-12 tasks rejected by " + reviewer + ". Reason: " + note,
            "tags": ["rejected", "sprint-12"],
            "node_type": "decision"
        })
        sys("sys.v1.event.emit", {
            "event_type": "sprint.review.rejected",
            "payload": {"reviewer": reviewer, "reason": note}
        })
        set_state("outcome", "rejected")
    }
}
```

`emit(...)` and `event.wait(...)` do not exist in the guest; the only exits are `sys()` and
`set_state()`. `user_id` is an injected global. Do not rely on a phase-1 `set_state` value
being readable in phase 2 — the script's `output_state` is stored under `nodus_output_state`,
not merged top-level (same defect family).

> **Since 2026-09-16 there is a one-call form (DEC-017):** `let approval = await_event("review.approved", <schema or nil>)`
> halts the script at the call on the first run and returns the payload on the resumed run, so the
> two phases can be written linearly. The script below keeps the explicit two-phase shape because
> it was run live in this form and because the guard around phase 1 is still the right pattern
> when phase 1 has effects (everything before `await_event` runs again on resume).
>
> **Optional, and worth doing: declare what may resume you.** Phase 2 reads
> `approval["reviewer"]`, `approval["note"]` and `approval["approved"]`. As written, a resume
> with `"payload": {}` is *accepted*, the wait is consumed, and the script fails on its second
> run — the run ends `failed`, not `waiting`. Add a third key in phase 1 and the runtime refuses
> a payload that does not fit **before** touching the run (HTTP **422**, run still waiting):
>
> ```js
> set_state("nodus_wait_resume_schema", {
>     "required": ["reviewer", "approved", "note"],
>     "properties": {"reviewer": {"type": "string"}, "approved": {"type": "boolean"}, "note": {"type": "string"}}
> })
> ```
>
> Same dialect and validator as syscall input schemas (`WAIT-TYPED-CONTRACT-1`, runtimes released after 2.16.0;
> see the developer guide §4). Not part of the recorded live run below, which was made without it.

---

## Step 2 — Upload and start it

Create `tutorial_02.py`:

```python
import os, time
from aindy_sdk import AINDYClient
import base64, json
def tenant_from_jwt(token: str) -> str:            # memory paths are /memory/{tenant}/…; tenant = JWT sub
    seg = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))["sub"]

client = AINDYClient(
    base_url=os.environ.get("AINDY_BASE_URL", "http://localhost:8000"),
    api_key=os.environ.get("AINDY_API_KEY", "replace_me"),   # admin JWT — Step 5 needs platform.admin
)
TENANT = tenant_from_jwt(client.api_key)

# Seed tasks (Tutorial 1 wrote these; harmless to repeat)
for content, tags in [
    ("Implement syscall versioning", ["engineering", "sprint-12"]),
    ("Write SDK unit tests",         ["engineering", "sprint-12"]),
    ("Publish documentation site",   ["docs", "sprint-12"]),
]:
    client.memory.write(f"/memory/{TENANT}/tasks/outcome", content, tags=tags, node_type="outcome")

print("Uploading script...")
with open("wait_resume.nd", encoding="utf-8") as f:
    client.post("/platform/nodus/upload", {"name": "wait_resume", "content": f.read(), "overwrite": True})

print("Starting script (phase 1 — will suspend)...")
result = client.nodus.run_script(script_name="wait_resume", input={"sprint": "sprint-12"})["data"]
run_id = result["run_id"]
print(f"  Run id:       {run_id}")
print(f"  Flow status:  {result['status']}")          # WAITING
print(f"  Nodus status: {result['nodus_status']}")    # None on 2.13.0; "waiting" after NODUS-RESUME-BRIDGE-1
```

**Observed output:**

```
Uploading script...
Starting script (phase 1 — will suspend)...
  Run id:       173c40f1…
  Flow status:  WAITING
  Nodus status: None            ← "waiting" on a runtime carrying NODUS-RESUME-BRIDGE-1's fix
```

The upload is `POST /platform/nodus/upload` (posted directly — `client.nodus.upload_script`
sends `source` where the route wants `content`; aindy-sdk 1.0.0 is broken there, see the SDK
handoff). `run_script` is `POST /platform/nodus/run`; its response is the pipeline envelope
with the execution record under `data`. Both need `flow.execute`. The run is now a `flow_runs`
row with `status = 'waiting'` and `waiting_for = 'review.approved'`, a `waiting_flow_runs` row
holds the scheduler registration, and `flow_run_rehydration` re-registers it after a restart.

---

## Step 3 — Verify it is waiting

```python
print("\nFlow run:")
run = client.get(f"/platform/flows/runs/{run_id}")["data"]["flow_run_get_result"]   # platform.admin
print(f"  status:      {run['status']}")          # waiting
print(f"  waiting_for: {run['waiting_for']}")     # review.approved

print("\nPending node written in phase 1:")
for node in client.memory.read(f"/memory/{TENANT}/pending/**")["data"]["nodes"]:
    print(f"  • {node['content']}   tags: {', '.join(node['tags'])}")
```

**Observed output:**

```
Flow run:
  status:      waiting
  waiting_for: review.approved

Pending node written in phase 1:
  • Pending review: 3 tasks loaded for sprint-12   tags: pending, awaiting-approval
```

`GET /platform/flows/runs/{run_id}` answers in the envelope; the run row — `status`,
`waiting_for`, `current_node`, `state`, `trace_id` — is `data.flow_run_get_result`.

---

## Step 4 — Subscribe a webhook (optional)

```python
try:
    sub = client.post("/platform/webhooks", {
        "event_type":   "sprint.review.*",             # prefix wildcard
        "callback_url": "http://localhost:9999/hook",
        "secret":       "tutorial-secret",
        "owner_class":  "first-party-app",             # the default, external-third-party, requires a provenance declaration
    })
    print(f"\n  ✓ Webhook subscribed — id {sub['id']}")
except Exception as e:
    print(f"\n  (webhook skipped: {e})")
```

Needs `webhook.manage`. The response is the subscription record, unwrapped.

---

## Step 5 — Approve it

```python
print("\nApproving...")
resumed = client.post(f"/platform/flows/runs/{run_id}/resume", {
    "event_type": "review.approved",
    "payload": {
        "reviewer": "shawn",
        "approved": True,
        "note":     "All tasks meet the sprint exit criteria. Ship it.",
    },
})
print(f"  {resumed['data']['flow_run_resume_result']}")
```

**Observed output:**

```
Approving...
  {'run_id': '173c40f1…', 'resumed': True, 'results': [{'run_id': '173c40f1…', 'payload_injected': True}]}
```

The route checks the named run is yours and `waiting` on that `event_type` (404 / 400
otherwise), injects the payload into its state, and publishes the event so the scheduler
re-enqueues it. The second execution is asynchronous.

> **One thing to know about this route, by version** (`RESUME-FANOUT-UNSCOPED-1`): on
> **2.13.0** `results` lists *every* run waiting on `review.approved` — not only the one in the
> path — because the fan-out was by event type with no run-id or tenant filter, so on a shared
> server your approval resumed other people's waits too. **Fixed on `main` 2026-09-13:** the
> resume is scoped to the named run end to end (injection, local wake, Redis broadcast,
> cross-instance fallback), and `results` has exactly one entry. The route stays
> `platform.admin`-gated.

---

## Step 6 — Watch phase 2 … not complete (today)

```python
print("\nWatching the run after resume...")
for _ in range(10):
    time.sleep(1)
    run = client.get(f"/platform/flows/runs/{run_id}")["data"]["flow_run_get_result"]
    print(f"  status={run['status']:9s} waiting_for={run['waiting_for']}  "
          f"received={run['state'].get('nodus_received_events')}")
    if run["status"] in ("success", "failed"):   # a finished FlowRun is "success", not "completed"
        break

hist = client.get(f"/platform/flows/runs/{run_id}/history")["data"]
steps = hist.get("flow_run_history_result", hist).get("history", [])
print(f"  history: {[s['status'] for s in steps]}")
```

**Observed output on 2.13.0:**

```
Watching the run after resume...
  status=executing waiting_for=review.approved  received=None
  status=waiting   waiting_for=review.approved  received=None
  ...
  history: ['WAIT', 'WAIT']
```

That is the 2.13.0 defect, visible: the run went `executing` (the resume fired, the script ran
again), `nodus_received_events` never appeared, and the script — seeing nil — requested the wait
again. Every further resume added another `WAIT` row to the history. Nothing errored and nothing
warned.

**On a runtime carrying the fix** (`main` from 2026-09-13; the first release after 2.13.0) the
same loop ends with:

```
  status=success   waiting_for=None  received={'review.approved': {'reviewer': 'shawn', 'approved': True, 'note': 'Ship it.'}}
  history: ['WAIT', 'SUCCESS']
```

followed by the approved insight under `/memory/{TENANT}/insights/**`. Two details worth
knowing, both read from the runtime's second-run test rather than guessed: the finished run's
status is **`success`** (the FlowRun vocabulary, not `completed`), and while the run is
waiting, `state["nodus_output_state"]` now carries what phase 1 set (`task_count`) — readable
on the run, though **not** handed back into phase 2's namespace; the re-run still starts from
the top with only `nodus_received_events` seeded.

---

## Step 7 — Read the causal trace

Everything that *did* happen is on the event graph, keyed by the run's `trace_id` from Step 2:

```python
trace_id = run["trace_id"]                       # the run row's trace_id (Step 3), not the request's
graph = client.get(f"/platform/observability/execution_graph/{trace_id}")["data"]["observability_rippletrace_result"]
for ev in graph["nodes"]:
    print(f"  {ev.get('source', '?'):22s} {ev.get('type', '?')}")
print(f"  ({len(graph['edges'])} causal edges)")
```

**Observed output:**

```
  platform.nodus.run     execution.started
  syscall_dispatcher     syscall.executed
  platform.nodus.run     execution.waiting    ← execution.completed on a runtime past 2.14.0 (FR-29)
  flow                   flow.node.started
  nodus                  nodus.execute.started
  flow                   flow.node.completed
  flow                   flow.waiting
  (5 causal edges)
```

The graph is `data.observability_rippletrace_result` — `nodes`, `edges`, `root_event`,
`terminal_events`, `ripple_span`, `insights`. **The third line changed after 2.14.0
(`WAIT-DETECT-SHAPE-1`, the app team's FR-29):** the *request's* execution unit used to be
parked alongside the run — on the event `"unknown"`, which nothing emits — because the pipeline
read the execution record's `status: "WAITING"` as the request itself waiting. Now the request
completes (`execution.completed`; the envelope's top-level `status` is `success`, and
`data.status` is still `WAITING`) and only the run is parked: the `flow.waiting` line below is
the wait, and `flow_runs` / the run's own execution unit are where it lives. The same defect
parked the unit of every `GET …/runs/{id}` of a waiting run on an app-profile server, one row
per read, which is how it was found. This is phase 1 only: the resume's own events
(`nodus.event.wait_resumed`, the second `flow.waiting`) carry the *resume request's* trace id,
not the run's, so they are on a different graph. Two traces for one run is itself a thing to
know.

---

## Complete script

```python
import os, time
from aindy_sdk import AINDYClient
import base64, json
def tenant_from_jwt(token: str) -> str:
    seg = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))["sub"]

client = AINDYClient(base_url=os.environ["AINDY_BASE_URL"], api_key=os.environ["AINDY_API_KEY"])
TENANT = tenant_from_jwt(client.api_key)

for content in ["Syscall versioning", "SDK tests", "Docs site"]:
    client.memory.write(f"/memory/{TENANT}/tasks/outcome", content, tags=["sprint-12"], node_type="outcome")

with open("wait_resume.nd", encoding="utf-8") as f:
    client.post("/platform/nodus/upload", {"name": "wait_resume", "content": f.read(), "overwrite": True})

run_id = client.nodus.run_script(script_name="wait_resume")["data"]["run_id"]
print(f"Suspended — run {run_id}")

client.post(f"/platform/flows/runs/{run_id}/resume", {
    "event_type": "review.approved",
    "payload": {"reviewer": "shawn", "approved": True, "note": "Ship it."},
})
print("Approval sent — script re-running...")

for _ in range(10):
    time.sleep(1)
    run = client.get(f"/platform/flows/runs/{run_id}")["data"]["flow_run_get_result"]
    if run["status"] in ("success", "failed"):
        break
print(f"Final status: {run['status']}  (2.13.0: 'waiting' again — NODUS-RESUME-BRIDGE-1; fixed after)")
for node in client.memory.read(f"/memory/{TENANT}/insights/**")["data"]["nodes"]:
    print(f"Insight: {node['content']}")
```

---

## What works, and what does not

```
tutorial_02.py                          wait_resume.nd
      │  run_script()                        │ phase 1: read, write pending      ✓
      │ ───────────────────────────────►     │ set nodus_wait_* → exit           ✓
      │                                      ▼ FlowRun.status = waiting          ✓ (durable, rehydrated)
      │  POST …/runs/{id}/resume             ·
      │ ───────────────────────────────►     ·  payload → state["event"]         ✓ (2.13.0: also into every other
      │                                      ▼ re-enqueued, script re-runs       ✓  waiting run — fixed 2026-09-13)
      │                                      │ nodus_received_events populated   ✓ since 2026-09-13 (✗ on 2.13.0 — NODUS-RESUME-BRIDGE-1)
      │                                      │ phase 2                           ✓ since 2026-09-13 (✗ on 2.13.0)
```

The waiting run costs nothing while it waits — a database row and a scheduler entry — and it
survives a restart. That property is real. Delivering the answer *into* the script is real from
the first release after 2.13.0; on 2.13.0 it is the step that does not happen.

---

## What if the approval never comes?

The run stays `waiting`. There is no per-wait timeout on this path today; an operator cancels
it or resumes it with a rejection. `GET /platform/flows/runs?status=waiting` lists them
(`platform.admin`).

---

## Next

→ **[Tutorial 3: Scheduled Intelligence](./03-scheduled-execution.md)** — run a script on a
schedule instead of on demand. Everything in it works today.
