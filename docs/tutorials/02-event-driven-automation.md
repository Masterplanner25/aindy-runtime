---
title: "Tutorial 2 — Event-Driven Automation"
last_verified: "2026-09-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Tutorial 2 — Event-Driven Automation

**Time:** ~8 minutes
**Difficulty:** Intermediate
**What you'll build:** A Nodus script that suspends its run until a human approves, then
finishes the work with the approval payload — no polling, no thread, no process held open.

---

## Goal

```
Script runs → loads tasks → writes "pending" → SUSPENDS the run
      (you approve)
Script runs AGAIN from the top → sees the approval → writes the insight → emits
```

Read that second line carefully. This is the thing the previous version of this tutorial got
wrong, and it is the whole model: **a guest script does not pause mid-line and continue. It
sets two state keys, exits, and when the event arrives the runtime runs the same script
again with the payload in state.** Your script has to be written in two phases that branch
on whether the payload is there. Nothing is held open in between — the run is a row in
`flow_runs` with `status = 'waiting'`.

---

## How WAIT / RESUME actually works

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
   runtime: payload → state["event"] → state["nodus_received_events"]["review.approved"]
   runtime: re-enqueues the run; the nodus.execute node runs the script again
```

Two facts decide how you resume it, and both were checked against source for this version:

- **The event bus carries no payload.** `sys.v1.event.emit` (what `client.events.emit`
  sends) can *wake* a waiting run, but the resume callback is zero-argument
  (`build_flow_resume_callback`) — the re-run script would find nothing in
  `nodus_received_events`, re-request the wait, and sit there. Payload injection happens only
  in `route_event`, which only `POST /platform/flows/runs/{run_id}/resume` calls.
- **The wait is correlation-keyed to the run's own `trace_id`.** An emit from a separate
  request carries *its* trace id, and a wait whose id is set and differs is skipped. The
  resume route sidesteps this by not sending one.

So the tutorial resumes through the route. It needs `platform.admin` — the admin JWT from the
prerequisites has it.

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

    set_state("task_count", task_count)          // survives into phase 2
    set_state("nodus_wait_requested", true)      // ← the suspend
    set_state("nodus_wait_event_type", "review.approved")
} else {
    // ── Phase 2: resumed with the approval payload ────────────────────────
    let approval = received["review.approved"]
    let reviewer = approval["reviewer"]
    let note = approval["note"]
    let task_count = get_state("task_count")

    if (approval["approved"] == true) {
        sys("sys.v1.memory.write", {
            "path": "/memory/" + user_id + "/insights/decision",
            "content": "Sprint-12 tasks approved by " + reviewer + ". Note: " + note,
            "tags": ["approved", "sprint-12", reviewer],
            "node_type": "decision"
        })
        sys("sys.v1.event.emit", {
            "event_type": "sprint.review.completed",
            "payload": {"reviewer": reviewer, "task_count": task_count, "approved": true}
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

State set in phase 1 (`task_count`) is still there in phase 2 — flow state persists across
the wait. `emit(...)` and `event.wait(...)` do not exist in the guest; the only exits are
`sys()` and `set_state()`.

---

## Step 2 — Upload and start it

Create `tutorial_02.py`:

```python
import os, time
from aindy_sdk import AINDYClient
from tutorial_01 import tenant_from_jwt

client = AINDYClient(
    base_url=os.environ.get("AINDY_BASE_URL", "http://localhost:8000"),
    api_key=os.environ.get("AINDY_API_KEY", "replace_me"),   # admin JWT — Step 5 needs platform.admin
)
TENANT = tenant_from_jwt(client.api_key)      # from Tutorial 1 — memory paths start /memory/{tenant}/

# Seed tasks (Tutorial 1 wrote these; harmless to repeat)
for content, tags in [
    ("Implement syscall versioning", ["engineering", "sprint-12"]),
    ("Write SDK unit tests",         ["engineering", "sprint-12"]),
    ("Publish documentation site",   ["docs", "sprint-12"]),
]:
    client.memory.write(f"/memory/{TENANT}/tasks/outcome", content, tags=tags, node_type="outcome")

print("Uploading script...")
with open("wait_resume.nd", encoding="utf-8") as f:
    client.nodus.upload_script("wait_resume", f.read(), overwrite=True)

print("Starting script (phase 1 — will suspend)...")
result = client.nodus.run_script(script_name="wait_resume", input={"sprint": "sprint-12"})
run_id = result["run_id"]
print(f"  Run id:       {run_id}")
print(f"  Flow status:  {result['status']}")          # WAITING
print(f"  Nodus status: {result['nodus_status']}")    # WAIT
```

**Expected output:**

```
Uploading script...
Starting script (phase 1 — will suspend)...
  Run id:       9f3c…
  Flow status:  WAITING
  Nodus status: WAIT
```

`upload_script` is `POST /platform/nodus/upload`; `run_script` is `POST /platform/nodus/run`
(both `flow.execute`). The run is now a `flow_runs` row with `status = 'waiting'` and
`waiting_for = 'review.approved'`, and the scheduler holds a wait entry for it. If the server
restarts, `flow_run_rehydration` re-registers it — nothing is lost.

---

## Step 3 — Verify it is waiting

```python
print("\nFlow run:")
run = client.get(f"/platform/flows/runs/{run_id}")["flow_run_get_result"]   # platform.admin
print(f"  status:      {run['status']}")          # waiting
print(f"  waiting_for: {run['waiting_for']}")     # review.approved

print("\nPending node written in phase 1:")
for node in client.memory.read(f"/memory/{TENANT}/pending/**")["data"]["nodes"]:
    print(f"  • {node['content']}   tags: {', '.join(node['tags'])}")
```

**Expected output:**

```
Flow run:
  status:      waiting
  waiting_for: review.approved

Pending node written in phase 1:
  • Pending review: 3 tasks loaded for sprint-12   tags: pending, awaiting-approval
```

`GET /platform/flows/runs/{run_id}` returns the run row — `status`, `waiting_for`,
`current_node`, `state`, `trace_id` — under `flow_run_get_result`. (`client.execution.get`
also exists and accepts a flow run id, but it reads the *execution unit*, whose status follows
the request pipeline rather than the flow; the run row is the authoritative view of a wait.)

---

## Step 4 — Subscribe a webhook (optional)

If you have something listening, subscribe it to the events phase 2 will emit. Prefix
wildcards (`sprint.review.*`) are supported. Needs `webhook.manage`.

```python
try:
    sub = client.post("/platform/webhooks", {
        "event_type":   "sprint.review.*",
        "callback_url": "http://localhost:9999/hook",
        "secret":       "tutorial-secret",
    })
    print(f"\n  ✓ Webhook subscribed — id {sub.get('id', '?')}")
except Exception as e:
    print(f"\n  (webhook skipped: {e})")
```

---

## Step 5 — Approve it

This is the moment. Resume the run *with a payload* through the flows route:

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
print(f"  resume accepted: {resumed.get('status', resumed)}")
```

The route checks the run is `waiting` and that `waiting_for` matches `event_type` (400
otherwise, 404 if the run is not yours), injects the payload into the run's state, and
publishes the event so the scheduler re-enqueues the run. The second execution happens
asynchronously — so wait for it.

---

## Step 6 — Watch phase 2 complete

```python
print("\nWaiting for phase 2...")
for _ in range(20):
    run = client.get(f"/platform/flows/runs/{run_id}")["flow_run_get_result"]
    if run["status"] in ("completed", "failed"):
        break
    time.sleep(0.5)
print(f"  status:  {run['status']}")
print(f"  outcome: {run['state'].get('outcome')}")   # set_state("outcome", ...) in phase 2

print("\nInsights:")
for node in client.memory.read(f"/memory/{TENANT}/insights/**")["data"]["nodes"]:
    print(f"  • {node['content']}")
    print(f"    tags: {', '.join(node['tags'])}")
```

**Expected output:**

```
Waiting for phase 2...
  status:  completed
  outcome: approved

Insights:
  • Sprint-12 tasks approved by shawn. Note: All tasks meet the sprint exit criteria. Ship it.
    tags: approved, sprint-12, shawn
```

(If you ran Tutorial 1, its insight is listed too.)

---

## Step 7 — Read the causal trace

Every syscall the script made, the wait, and the resume are `SystemEvent` rows linked by
`EventEdge`. The run's `trace_id` came back in Step 2:

```python
graph = client.get(f"/platform/observability/execution_graph/{result['trace_id']}")
for ev in graph.get("nodes", []):
    print(f"  {ev.get('timestamp', '?')[:19]}  {ev.get('source', '?'):12s} {ev.get('type', '?')}")
print(f"  ({len(graph.get('edges', []))} causal edges)")
```

The graph is `{"nodes": [...], "edges": [...]}` — each node a `SystemEvent` with `id`, `type`,
`source`, `timestamp`, `payload`. You will see the memory read and write from phase 1,
`flow.waiting`, a `nodus.event.wait_resumed` marker, and phase 2's write and emit — with the
gap between them being however long you took to approve.

---

## Complete script

```python
import os, time
from aindy_sdk import AINDYClient
from tutorial_01 import tenant_from_jwt

client = AINDYClient(base_url=os.environ["AINDY_BASE_URL"], api_key=os.environ["AINDY_API_KEY"])
TENANT = tenant_from_jwt(client.api_key)

for content in ["Syscall versioning", "SDK tests", "Docs site"]:
    client.memory.write(f"/memory/{TENANT}/tasks/outcome", content, tags=["sprint-12"], node_type="outcome")

with open("wait_resume.nd", encoding="utf-8") as f:
    client.nodus.upload_script("wait_resume", f.read(), overwrite=True)

run_id = client.nodus.run_script(script_name="wait_resume")["run_id"]
print(f"Suspended — run {run_id}")

client.post(f"/platform/flows/runs/{run_id}/resume", {
    "event_type": "review.approved",
    "payload": {"reviewer": "shawn", "approved": True, "note": "Ship it."},
})

for _ in range(20):
    if client.get(f"/platform/flows/runs/{run_id}")["flow_run_get_result"]["status"] in ("completed", "failed"):
        break
    time.sleep(0.5)

for node in client.memory.read(f"/memory/{TENANT}/insights/**")["data"]["nodes"]:
    print(f"Insight: {node['content']}")
```

---

## What you just built

```
tutorial_02.py                          wait_resume.nd
      │  run_script()                        │ phase 1: read, write pending
      │ ───────────────────────────────►     │ set nodus_wait_* → exit
      │                                      ▼ FlowRun.status = waiting
      │  POST …/runs/{id}/resume             ·   (a row; nothing running)
      │ ───────────────────────────────►     ·
      │                                      ▼ re-enqueued
      │                                      │ phase 2: sees payload,
      │  memory.read → sees insight          │ writes insight, emits
      │ ◄───────────────────────────────     │
```

The waiting run costs nothing while it waits — it is a database row and a scheduler entry,
and it survives a restart. That is the property this pattern buys you.

---

## What if the approval never comes?

The run stays `waiting`. There is no per-wait timeout on this path today; an operator cancels
it or resumes it with a rejection. `GET /platform/flows/runs?status=waiting` lists them
(`platform.admin`).

---

## Next

→ **[Tutorial 3: Scheduled Intelligence](./03-scheduled-execution.md)** — run a script on a
schedule instead of on demand.
