---
title: "Tutorial 1 — Memory-Driven Task Analyzer"
last_verified: "2026-09-21"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Tutorial 1 — Memory-Driven Task Analyzer

**Time:** ~8 minutes
**Difficulty:** Beginner
**What you'll build:** A pipeline that writes tasks to memory, reads them back, runs a Nodus
analysis script inside the runtime, and writes the insight back — every step through the
syscall layer.

---

## Goal

```
Write tasks → Read them back → Analyze (Nodus script) → Write insight → Verify → Emit
```

By the end you'll have a populated memory namespace the next two tutorials build on.

Every response you get back is the syscall envelope:

```python
{"status": "success" | "error", "data": {...}, "trace_id": "...", "version": "v1",
 "duration_ms": 4, "error": None}
```

---

## Step 1 — Connect

Create `tutorial_01.py`:

```python
import os
from aindy_sdk import AINDYClient

client = AINDYClient(
    base_url=os.environ.get("AINDY_BASE_URL", "http://localhost:8000"),
    api_key=os.environ.get("AINDY_API_KEY", "replace_me"),   # JWT or aindy_… key
)

# Memory paths are /memory/{tenant}/{namespace}/{type}/{id}, and the tenant is YOUR user
# id — a path under anyone else's raises TENANT_VIOLATION. The JWT's `sub` claim is that id.
import base64, json
def tenant_from_jwt(token: str) -> str:
    seg = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))["sub"]

TENANT = tenant_from_jwt(client.api_key)
print(f"Connected as tenant {TENANT}.")
```

```bash
python tutorial_01.py
# Connected as tenant 3f2a9c…
```

(With an `aindy_…` API key instead of a JWT, take the tenant from the `user_id` on any node
you already own, or from the `path` the runtime returns on your first write.) Nothing is sent
yet — the client is lazy. `NetworkError` on the first real call means the
server is not up; `AuthenticationError` means the token is wrong or expired.

---

## Step 2 — Write three tasks to memory

```python
tasks = [
    ("Implement the syscall versioning layer",       ["engineering", "sprint-12", "completed"]),
    ("Write SDK unit tests — 47 passing, zero deps",  ["engineering", "sprint-12", "completed"]),
    ("Publish documentation site structure",         ["docs", "sprint-12", "in-progress"]),
]

print("Writing tasks...")
written_ids = []
for content, tags in tasks:
    result = client.memory.write(
        path=f"/memory/{TENANT}/tasks/outcome",
        content=content,
        tags=tags,
        node_type="outcome",
    )
    node_id = result["data"]["node"]["id"]
    written_ids.append(node_id)
    print(f"  ✓ {content[:50]:50s} → {node_id[:8]}…")

print(f"\nWrote {len(written_ids)} tasks.")
```

`client.memory.write` dispatches `sys.v1.memory.write`; `data` is `{"node": {...}, "path": "…"}`.
The path grammar is `/memory/{tenant}/{namespace}/{type}/{id}`. You give the first four
segments and the runtime appends a generated id, so three writes to
`/memory/<tenant>/tasks/outcome` produce three distinct nodes under it. Here `tasks` is the
namespace and `outcome` the type — there is no free-form nesting below the type; a fifth
segment is read as an explicit node id.

---

## Step 3 — Read them back

```python
print("\nReading tasks from memory...")
read = client.memory.read(path=f"/memory/{TENANT}/tasks/**", limit=20)

nodes = read["data"]["nodes"]
print(f"Found {read['data']['count']} node(s) — took {read['duration_ms']}ms\n")
for node in nodes:
    print(f"  [{node['node_type']}] {node['content']}")
    print(f"           tags: {', '.join(node.get('tags', []))}\n")
```

`/**` reads every node under the prefix, recursively. `/*` reads *direct children of that
parent path* — and nodes live one level below their type, so the one-level form of this read
is `/memory/{TENANT}/tasks/outcome/*`, not `…/tasks/*` (which matches nothing). `data` is
`{"nodes": [...], "count": N}`.

---

## Step 4 — Write the analysis script

The analysis runs *inside the runtime* as a Nodus script. Create `analyze.nd`:

```js
// Tutorial 1 — count completed vs in-progress tasks
let result = sys("sys.v1.memory.read", {"path": "/memory/" + user_id + "/tasks/**", "limit": 20})
if (result["status"] != "success") {
    set_state("error", result["error"])
} else {
    let nodes = result["data"]["nodes"]
    let completed = 0i
    let in_progress = 0i
    let i = 0i
    while (i < len(nodes)) {
        let tags = nodes[i]["tags"]
        let j = 0i
        while (j < len(tags)) {
            if (tags[j] == "completed") { completed = completed + 1i }
            if (tags[j] == "in-progress") { in_progress = in_progress + 1i }
            j = j + 1i
        }
        i = i + 1i
    }
    let summary = ("Analyzed " + str(len(nodes)) + " tasks: " + str(completed)
                   + " completed, " + str(in_progress) + " in progress.")
    set_state("summary", summary)
    set_state("completed_count", completed)
    set_state("in_progress_count", in_progress)
}
```

Three things to know about the guest:

- `sys(name, payload)` is the **only** way out. It returns the same envelope the SDK does and
  never throws — check `["status"]`. The script can call nothing else the runtime has not
  injected (`docs/runtime/NODUS_DEVELOPER_GUIDE.md` §1.1).
- `set_state(key, value)` is how the script hands results back; they arrive as `output_state`.
- `if (...)` and `while (...)` **require parentheses** on nodus 5. `0i` is an integer literal;
  `str()` and `len()` are builtins. A statement ends at the newline — wrap a multi-line
  expression in parentheses.

---

## Step 5 — Run the analysis

```python
print("\nRunning analysis script...")
with open("analyze.nd", encoding="utf-8") as f:
    analysis = client.nodus.run_script(script=f.read(), input={"context": "sprint-12"})["data"]

print(f"  Status:       {analysis['status']}")          # flow status, e.g. SUCCESS
print(f"  Nodus status: {analysis['nodus_status']}")
print(f"  Run id:       {analysis['run_id']}")
out = analysis["output_state"]
print(f"  Output keys:  {sorted(out)}")
```

**Observed output:**

```
Running analysis script...
  Status:       SUCCESS
  Nodus status: success
  Run id:       3c7e…
  Output keys:  ['completed_count', 'in_progress_count', 'summary']
```

This is `POST /platform/nodus/run` (scope `flow.execute`). The route response is the pipeline
envelope — `{"status": "success", "data": {...}}` — and `data` is the Nodus execution record:
`status` (flow: `SUCCESS` / `WAITING` / `FAILED`), `nodus_status` (`success` / `WAIT` / …),
`run_id`, `trace_id`, `output_state`, `events`, `memory_writes`, `events_emitted`,
`memory_writes_count`, `error`, plus an `execution_record`. Every `sys()` call the script made went
through the dispatcher under your identity.

---

## Step 6 — Write the insight back to memory

```python
summary = out.get("summary", f"Analyzed {len(nodes)} tasks.")

print("\nWriting insight to memory...")
insight = client.memory.write(
    path=f"/memory/{TENANT}/insights/decision",
    content=summary,
    tags=["sprint-12", "retrospective", "auto-generated"],
    node_type="decision",
    extra={
        "source": "analyze.nd",
        "task_count": len(nodes),
        "completed_count": out.get("completed_count", 0),
        "in_progress_count": out.get("in_progress_count", 0),
    },
)
node = insight["data"]["node"]
print(f"  ✓ Insight written — id {node['id'][:8]}…  path {insight['data']['path']}")
```

`extra` is not in the v1 syscall's declared schema, but the handler **merges** it into the
node's `extra` (it used to replace it silently — `ROUTE-EFFECT-BYPASS-1`). Unknown top-level
keys are not rejected by the dispatcher.

---

## Step 7 — Verify the whole namespace

```python
print("\nEverything you own:")
everything = client.memory.read(path=f"/memory/{TENANT}/**", limit=50)
for node in everything["data"]["nodes"]:
    _, _, _, namespace, ntype, _ = node["path"].split("/")
    print(f"  {namespace}/{ntype:9s} {node['content'][:55]}")
```

**Expected output:**

```
Everything you own:
  tasks/outcome     Implement the syscall versioning layer
  tasks/outcome     Write SDK unit tests — 47 passing, zero deps
  tasks/outcome     Publish documentation site structure
  insights/decision Analyzed 3 tasks: 2 completed, 1 in progress.
```

There is also `client.memory.tree(path)` (`sys.v1.memory.tree`), which returns
`{"tree": {path: {"node": …, "children": [...]}}, "node_count": N}` — a nested map, not a
flat list. The recursive read above is simpler when all you want is the nodes.

---

## Step 8 — Emit a completion event

```python
print("\nEmitting completion event...")
ev = client.events.emit("sprint.analyzed",
                        {"sprint": "sprint-12", "task_count": len(nodes), "insight_id": node["id"]})
print(f"  ✓ {ev['status']} — trace {ev['trace_id']}")
print("\nDone. The memory-driven loop is working.")
```

`client.events.emit` dispatches `sys.v1.event.emit`, which writes a `SystemEvent` row, fans
out to any matching webhook subscription, and publishes on the event bus. Its `data` carries the
`event_id`; the envelope's `trace_id` is the request. (Needs aindy-sdk **≥ 1.0.1** — 1.0.0 sent
the wrong wire key and 422'd on every call; `docs/handoffs/SDK_HANDOFF_1_0_0_wire_mismatches.md`.)

---

## Complete script

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

for content, tags in [
    ("Implement the syscall versioning layer",       ["engineering", "sprint-12", "completed"]),
    ("Write SDK unit tests — 47 passing, zero deps",  ["engineering", "sprint-12", "completed"]),
    ("Publish documentation site structure",         ["docs", "sprint-12", "in-progress"]),
]:
    client.memory.write(f"/memory/{TENANT}/tasks/outcome", content, tags=tags, node_type="outcome")

nodes = client.memory.read(f"/memory/{TENANT}/tasks/**", limit=20)["data"]["nodes"]

with open("analyze.nd", encoding="utf-8") as f:
    out = client.nodus.run_script(script=f.read())["data"]["output_state"]

client.memory.write(f"/memory/{TENANT}/insights/decision", out["summary"],
                    tags=["sprint-12", "auto-generated"], node_type="decision")
client.events.emit("sprint.analyzed", {"sprint": "sprint-12", "task_count": len(nodes)})

print(f"Loop complete: {len(nodes)} tasks → 1 insight → 1 event")
```

---

## What you just built

```
/memory/<tenant>/tasks/outcome/<id>  ×3        ← Step 2
         │
         │  POST /platform/nodus/run  →  analyze.nd  →  sys("sys.v1.memory.read")
         ▼
/memory/<tenant>/insights/decision/<id>        ← Step 6
         │
         │  sys.v1.event.emit("sprint.analyzed")
         ▼
   SystemEvent row + webhook fan-out + bus publish
```

Every arrow crossed `SyscallDispatcher`: capability check, tenant scope, schema validation,
and an `EffectRecord` for the write syscalls. That is the point of the loop.

---

## Next

→ **[Tutorial 2: Event-Driven Automation](./02-event-driven-automation.md)** — a script that
suspends the run and waits for a human.
