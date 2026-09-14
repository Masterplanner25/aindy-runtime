---
title: "Tutorial 3 — Scheduled Intelligence"
last_verified: "2026-09-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Tutorial 3 — Scheduled Intelligence

**Time:** ~7 minutes
**Difficulty:** Intermediate
**What you'll build:** A Nodus script that runs on a cron schedule, summarises what is in
memory, writes a daily briefing node, and emits an event a webhook can pick up — with nobody
at a keyboard.

---

## Goal

```
09:00 UTC every day
      │
      ▼
  scheduler fires the stored script
      │
      ├─ sys.v1.memory.read   /memory/<tenant>/**
      ├─ sys.v1.memory.write  /memory/<tenant>/briefings/decision
      └─ sys.v1.event.emit    daily.briefing.ready  → webhook fan-out
```

The scheduled job is a `nodus_scheduled_jobs` row; the runtime's APScheduler lane picks it up
on the cron tick, runs it as a normal Nodus execution under your identity, and records the
outcome on the row (`last_run_at`, `last_run_status`). Missed ticks — the server was down at
09:00 — follow the job's `misfire_policy` (`skip` by default).

---

## Step 1 — Write the briefing script

Create `daily_briefing.nd`:

```js
// Tutorial 3 — daily briefing over everything this user owns
let recent = sys("sys.v1.memory.read", {"path": "/memory/" + user_id + "/**", "limit": 50})
let nodes = []
if (recent["status"] == "success") { nodes = recent["data"]["nodes"] }
let node_count = len(nodes)

// Count by type
let decisions = 0i
let outcomes = 0i
let insights = 0i
let i = 0i
while (i < node_count) {
    let t = nodes[i]["node_type"]
    if (t == "decision") { decisions = decisions + 1i }
    if (t == "outcome")  { outcomes = outcomes + 1i }
    if (t == "insight")  { insights = insights + 1i }
    i = i + 1i
}

let summary = "No new memory nodes since last briefing."
if (node_count > 0i) {
    summary = ("Daily briefing: " + str(node_count) + " node(s) - " + str(decisions)
               + " decisions, " + str(outcomes) + " outcomes, " + str(insights) + " insights.")
    // Same path every day, so downstream consumers always find the latest briefing here
    sys("sys.v1.memory.write", {
        "path": "/memory/" + user_id + "/briefings/decision",
        "content": summary,
        "tags": ["daily-briefing", "auto-generated"],
        "node_type": "decision"
    })
}

// Emit for webhooks and downstream automations — even on an empty day
sys("sys.v1.event.emit", {
    "event_type": "daily.briefing.ready",
    "payload": {"node_count": node_count, "decisions": decisions,
                "outcomes": outcomes, "insights": insights, "summary": summary}
})

set_state("briefing", summary)
set_state("node_count", node_count)
set_state("done", true)
```

If you did Tutorials 1 and 2, `/memory/<tenant>/**` — everything you own — already holds tasks, insights and a pending
node, so the first run has something to say.

---

## Step 2 — Test it manually first

Never schedule a script you have not run. Create `tutorial_03.py`:

```python
import os
from aindy_sdk import AINDYClient
import base64, json
def tenant_from_jwt(token: str) -> str:            # memory paths are /memory/{tenant}/…; tenant = JWT sub
    seg = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))["sub"]

client = AINDYClient(
    base_url=os.environ.get("AINDY_BASE_URL", "http://localhost:8000"),
    api_key=os.environ.get("AINDY_API_KEY", "replace_me"),
)
TENANT = tenant_from_jwt(client.api_key)

with open("daily_briefing.nd", encoding="utf-8") as f:
    client.post("/platform/nodus/upload", {"name": "daily_briefing", "content": f.read(), "overwrite": True})

print("Manual run...")
result = client.nodus.run_script(script_name="daily_briefing")["data"]
print(f"  status:        {result['status']}   nodus: {result['nodus_status']}")
print(f"  memory writes: {result['memory_writes_count']}")
print(f"  events:        {result['events_emitted']}")
out = result["output_state"]
print(f"  briefing:      {out['briefing']}")
print(f"  node_count:    {out['node_count']}")
```

**Expected output:**

```
Manual run...
  status:        SUCCESS   nodus: success
  memory writes: 1
  events:        1
  briefing:      Daily briefing: 5 node(s) - 2 decisions, 3 outcomes, 0 insights.
  node_count:    5
```

Your counts will differ with what the earlier tutorials left in memory. `memory_writes_count`
and `events_emitted` come from the execution record and are the cheap way to confirm the
script did what you think.

---

## Step 3 — Schedule it

```python
print("\nScheduling (09:00 UTC daily)...")
job = client.post("/platform/nodus/schedule", {
    "script_name": "daily_briefing",   # the stored script from Step 2
    "cron":        "0 9 * * *",        # 5-field cron, UTC
    "job_name":    "daily_briefing",   # human label; the id is what you delete by
    "input":       {},
})
job_id = job["id"]
print(f"  ✓ job {job_id}")
print(f"    name:     {job['job_name']}")
print(f"    cron:     {job['cron_expression']}")
print(f"    next run: {job['next_run_at']}")
```

**Expected output:**

```
Scheduling (09:00 UTC daily)...
  ✓ job 6b1f…
    name:     daily_briefing
    cron:     0 9 * * *
    next run: None
```

`POST /platform/nodus/schedule` (scope `flow.execute`). The response is the job record,
**unwrapped** (unlike `nodus/run`). `next_run_at` reads `None` right after creation and — as
observed live — stays `None` after a restart too; the row is `is_active: true` and the
scheduler will fire it, but the field is not populated from the cron. Treat it as informational. You can pass `script` (inline source)
instead of `script_name`. Optional fields: `error_policy` (`fail` default), `max_retries`
(1–10, default 3). The cron is validated with `CronTrigger.from_crontab()` before the row is
written, so a bad expression is a 422 now, not a silent no-op at 09:00.

---

## Step 4 — Verify the schedule

```python
print("\nScheduled jobs:")
listing = client.get("/platform/nodus/schedule")
for j in listing["jobs"]:
    flag = "✓ active" if j["is_active"] else "✗ inactive"
    print(f"  [{flag}] {j['job_name']}  ({j['id'][:8]}…)")
    print(f"             cron:     {j['cron_expression']}")
    print(f"             next run: {j['next_run_at']}")
    print(f"             last run: {j['last_run_at'] or 'never'}  {j['last_run_status'] or ''}")
```

**Expected output:**

```
Scheduled jobs:
  [✓ active] daily_briefing  (6b1f…)
             cron:     0 9 * * *
             next run: None
             last run: never
```

The listing is `{"count": N, "jobs": [...]}`, unwrapped, scoped to your user.

---

## Step 5 — Subscribe a webhook to the briefing event

```python
print("\nSubscribing webhook to daily.briefing.ready...")
try:
    sub = client.post("/platform/webhooks", {
        "event_type":   "daily.briefing.ready",
        "callback_url": "https://your-system.example/hooks/briefing",
        "secret":       "your-webhook-secret",
        "owner_class":  "first-party-app",   # the default, external-third-party, requires a provenance declaration
    })
    print(f"  ✓ subscription {sub['id']}")
except Exception as e:
    print(f"  (skipped: {e})")
```

Scope `webhook.manage`. The response is the subscription record, unwrapped. Every delivery carries `X-AINDY-Signature: sha256=<hmac>` computed
with the `secret`; a prefix wildcard (`daily.*`) subscribes to a family of events. A failing
endpoint does not fail the script that emitted the event — delivery is fan-out, not part of
the syscall.

---

## Step 6 — Force a run now

You do not have to wait until 09:00 to see the whole chain fire:

```python
print("\nForcing a run now...")
now = client.nodus.run_script(script_name="daily_briefing")["data"]
print(f"  status: {now['status']}   events emitted: {now['events_emitted']}")
```

This is the same execution the scheduler will perform — same script, same identity, same
syscalls — just triggered by you. If the webhook endpoint is real, it received a
`daily.briefing.ready` delivery just now.

---

## Step 7 — Read the briefing back

```python
print("\nBriefings in memory:")
for node in client.memory.read(f"/memory/{TENANT}/briefings/**", limit=5)["data"]["nodes"]:
    print(f"  • {node['content']}")
```

**Expected output:**

```
Briefings in memory:
  • Daily briefing: 5 node(s) - 2 decisions, 3 outcomes, 0 insights.
  • Daily briefing: 5 node(s) - 2 decisions, 3 outcomes, 0 insights.
```

Two entries — the manual run and the forced run. Each scheduled tick adds one. (The briefing
counts *itself* from the second run on, since `/memory/<tenant>/**` is everything you own. Read
`/memory/<tenant>/tasks/**` instead if that bothers you.)

---

## Step 8 — Change or cancel

Schedules are replaced, not edited: delete by **id** and create again.

```python
client.delete(f"/platform/nodus/schedule/{job_id}")            # 204
job = client.post("/platform/nodus/schedule", {
    "script_name": "daily_briefing",
    "cron":        "0 9,18 * * *",                             # 09:00 and 18:00 UTC
    "job_name":    "daily_briefing",
})
print(f"Rescheduled as {job['id']} — {job['cron_expression']}")

# To stop it for good:
# client.delete(f"/platform/nodus/schedule/{job['id']}")
```

`DELETE /platform/nodus/schedule/{job_id}` takes the row id, never the `job_name` — names are
not unique.

---

## Complete script

```python
import os
from aindy_sdk import AINDYClient
import base64, json
def tenant_from_jwt(token: str) -> str:            # memory paths are /memory/{tenant}/…; tenant = JWT sub
    seg = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))["sub"]

client = AINDYClient(base_url=os.environ["AINDY_BASE_URL"], api_key=os.environ["AINDY_API_KEY"])
TENANT = tenant_from_jwt(client.api_key)

with open("daily_briefing.nd", encoding="utf-8") as f:
    client.post("/platform/nodus/upload", {"name": "daily_briefing", "content": f.read(), "overwrite": True})

test = client.nodus.run_script(script_name="daily_briefing")["data"]
print("manual run:", test["status"], "-", test["output_state"]["briefing"])

job = client.post("/platform/nodus/schedule", {
    "script_name": "daily_briefing", "cron": "0 9 * * *", "job_name": "daily_briefing",
})
print("scheduled:", job["id"], job["cron_expression"], "next", job["next_run_at"])

client.post("/platform/webhooks", {
    "event_type": "daily.briefing.ready",
    "callback_url": "https://your-system.example/hooks/briefing",
    "secret": "your-webhook-secret",
    "owner_class": "first-party-app",
})
print("webhook subscribed. Done — it runs without you now.")
```

---

## Cron reference

Five fields, **UTC**: `minute hour day-of-month month day-of-week`.

| Expression | Fires |
|---|---|
| `0 9 * * *` | 09:00 every day |
| `0 9 * * 1-5` | 09:00 Monday–Friday |
| `0 */6 * * *` | every six hours |
| `30 8 1 * *` | 08:30 on the 1st of each month |
| `0 9,18 * * *` | 09:00 and 18:00 |

---

## What you now have, across all three

```
Tutorial 1   write → read → analyze (script) → write insight → emit
Tutorial 2   script suspends the run → human approves through the resume route → script finishes
Tutorial 3   the same kind of script on a cron, with a webhook on its event
```

Every one of those arrows went through `SyscallDispatcher`, under your identity, with a
capability check and a `SystemEvent` — the scheduled run included. Nothing in these three
needed an app plugin, a custom node, or anything the runtime does not ship.

## Next steps

- `docs/runtime/NODUS_DEVELOPER_GUIDE.md` — everything a guest script can and cannot do
- `docs/runtime/SYSCALL_REFERENCE.md` — every `sys.v1.*` call and the scope each needs
- `docs/runtime/SANDBOX_CONTRACT.md` — what the guest boundary guarantees, and what it does not
