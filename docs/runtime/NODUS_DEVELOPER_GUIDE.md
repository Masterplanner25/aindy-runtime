---
title: "Nodus Developer Guide"
api_version: "1.0"
last_verified: "2026-06-07"
status: current
owner: "platform-team"
---

# Nodus Developer Guide

This guide is for developers writing Nodus scripts that run inside A.I.N.D.Y. It covers the
runtime-injected context, every available built-in function, WAIT/RESUME semantics, error
handling, and practical examples.

For the full Nodus language syntax, types, and stdlib reference see the `nodus-lang` package
documentation. A.I.N.D.Y. pins **nodus-lang == 4.0.3**.

---

## 1. Execution model

Nodus scripts run in a subprocess launched by `nodus_worker.py`. The subprocess:

1. Receives the script source, initial state, memory context, and input payload via stdin.
2. Compiles and runs the script with a set of injected globals and host functions.
3. Writes a JSON result envelope to stdout: `{status, output_state, memory_writes, emitted_events, error}`.

Scripts are **not** long-running processes. Each invocation runs to completion (or to a WAIT
suspension) and exits. State is carried forward between invocations via the flow's `output_state`.

---

## 2. Injected globals

Every script receives the following read-only globals from the flow context. Access them
directly as variables — no import needed.

| Global | Type | Description |
|--------|------|-------------|
| `state` | map | Current flow state (key-value pairs from prior nodes). Mutable via `set_state`/`get_state`. |
| `memory_context` | map | Snapshot of memory keys pre-loaded for this execution. |
| `input_payload` | map | Input values passed when the flow was submitted. |
| `user_id` | string | Authenticated tenant ID for this execution. |
| `execution_unit_id` | string | Unique ID for this execution unit (matches `AgentRun.id` or flow run ID). |
| `trace_id` | string | Observability trace ID propagated from the caller. |

```nd
print("Running as user: " + user_id)
print("Execution: " + execution_unit_id)

let topic = input_payload["topic"]
let prior_result = get_state("prior_result")
```

---

## 3. Built-in functions

These functions are injected into every script by the runtime. No import needed.

### 3.1 State

| Function | Returns | Description |
|----------|---------|-------------|
| `set_state(key, value)` | nil | Write a value into the flow's output state. Persisted when the script exits. |
| `get_state(key)` | any | Read a value from the current state map. Returns nil if the key is absent. |

```nd
set_state("analysis_result", "processed")
let prev = get_state("prior_step_output")
```

### 3.2 Memory

| Function | Signature | Description |
|----------|-----------|-------------|
| `remember` | `remember(content, tags?, node_type?, significance?)` | Write a memory node for the current user. `tags` is a list of strings; `node_type` defaults to `"execution"`; `significance` is 0.0–1.0 (default 0.5). Returns a node dict. |
| `recall` | `recall(query, tags?, limit?)` | Retrieve memory nodes via semantic search. `limit` defaults to 5. Returns a list of node dicts. |
| `recall_from` | `recall_from(agent_id, query, tags, limit)` | Recall nodes associated with a specific agent. |
| `recall_all` | `recall_all(query, tags, limit)` | Recall across all agents for this user. |
| `share` | `share(node_id)` | Mark a memory node as shared. Returns a result dict. |
| `suggest` | `suggest(query, limit?, filter?)` | Get suggested content from memory. |
| `record_outcome` | `record_outcome(outcome_id, success)` | Record an execution outcome signal. |

```nd
// Write a memory node
let node = remember("User prefers concise summaries", ["preference", "ux"])

// Read back related memories
let nodes = recall("user preferences", ["preference"], 3)
let first = nodes[0]
print(first["content"])
```

Memory writes are **deferred** — they are collected during script execution and committed to
the database after the script finishes successfully. A script that fails mid-execution will
not persist partial writes.

### Agent tools — `call_tool` (RTR-1 Phase 2a)

| Function | Signature | Description |
|----------|-----------|-------------|
| `call_tool` | `call_tool(tool_name, args)` | Execute a registered AINDY agent tool with capability-token enforcement. Returns `{success, result, error}`. |

```nd
let r = call_tool("send_email", { to: "x@example.com", subject: "hi" })
if r["success"] {
    print(r["result"])
} else {
    print(r["error"])
}
```

`call_tool` is the capability-enforced bridge from the Nodus VM to AINDY's tool
registry (`execute_tool`). It is **fail-closed**: the run must carry a scoped
capability token (e.g. an agent run's `capability_token`), or the call is refused
before reaching the tool. With a token, the same `check_tool_capability` checks
apply as for the Python agent path (token validity/expiry/hash, `granted_tools`,
required capabilities ⊆ allowed).

> **Do not use `action tool "x"`** to reach AINDY tools. The native nodus-lang
> `action tool` / `tool_call` construct lowers to nodus's own built-in stub
> registry (4 self-tools, **no capability enforcement**) and **cannot be
> overridden**. Always call `call_tool(...)` for AINDY tools.

### 3.3 Memory stdlib

For more structured memory operations, import the bundled memory stdlib:

```nd
import "memory" as memory

let results = memory.recall_from("agent-abc", "authentication flow", ["auth"], 5)
let shared  = memory.recall_all("user onboarding", nil, 10)
memory.share("node-id-here")
```

`memory.recall_from`, `memory.recall_all`, and `memory.share` are backed by the same
`AINDYMemoryBridge` as the top-level built-ins.

### 3.4 Syscall dispatch

```
sys(syscall_name, payload)  →  response envelope
```

`sys` is the gateway to all A.I.N.D.Y. platform capabilities from inside a Nodus script.
It calls `SyscallDispatcher.dispatch()` synchronously and returns the standard response
envelope:

```nd
let result = sys("sys.v1.memory.read", {"query": "authentication flow", "limit": 3})

if result["status"] == "success" {
    let nodes = result["data"]["nodes"]
    print("Found: " + str(len(nodes)))
} else {
    print("Error: " + result["error"])
}
```

**The syscall never throws.** A failed dispatch returns `{status: "error", error: "...", data: nil}`.
Always check `result["status"]` before accessing `result["data"]`.

> **⚠ Use the bare `sys(...)` builtin — do NOT `import "std:sys"` (NODUS-SYS-SURFACE-1).**
> nodus-lang ships a `std:sys` stdlib module whose `sys.call(name, payload)` looks equivalent
> but resolves to nodus's *native* `syscall` builtin — an in-process, ephemeral 4-syscall stub
> with **no capability enforcement, quota, idempotency, or persistence**. It never reaches
> A.I.N.D.Y.'s dispatcher. Under aindy-runtime that path is **guarded to fail loud**: a script
> that calls `syscall(...)` (directly or via `import "std:sys"`) errors with
> *"std:sys is not routed to the AINDY syscall dispatcher … use the bare `sys("<name>", <payload>)`
> builtin"*. Only the bare `sys(...)` builtin documented above reaches the real dispatcher.

See [SYSCALL_REFERENCE.md](./SYSCALL_REFERENCE.md) for the full list of available syscalls,
their payloads, and their return shapes.

---

## 4. Suspending a flow (WAIT / RESUME)

A script can suspend the enclosing flow and wait for an external event before continuing.
The suspension is signalled by setting two state keys before the script returns:

```nd
// Signal the runtime to suspend this flow
set_state("nodus_wait_requested", true)
set_state("nodus_wait_event_type", "user.response.received")

// Execution stops here — the flow enters "waiting" status.
// When the event fires, the flow is re-enqueued and this script
// (or the next node) runs again with the event payload in state.
```

On resume, the incoming event payload is available in state under `nodus_received_events`:

```nd
// On the second execution (after resume):
let received = get_state("nodus_received_events")
let event_payload = received["user.response.received"]
print("User said: " + event_payload["text"])
```

The WAIT/RESUME cycle:
1. Script sets `nodus_wait_requested = true` and `nodus_wait_event_type = "event.name"`.
2. Runtime suspends the `FlowRun` (`status → waiting`).
3. Something calls `EventBus.publish("event.name")`.
4. The scheduler re-enqueues the flow.
5. The next execution receives the event in `state["nodus_received_events"]["event.name"]`.

---

## 5. Error semantics

### Stdlib errors

Stdlib functions that can fail return **err records** rather than throwing:

```nd
import "std:fs" as fs
let result = fs.read("data.json")
if type(result) == "error" {
    print("Failed: " + result["message"])
    // result["kind"] is "io_error", "parse_error", etc.
} else {
    print(result)
}
```

### Syscall errors

`sys()` wraps all failures in the response envelope — it never throws:

```nd
let r = sys("sys.v1.memory.write", {"content": ""})
if r["status"] != "success" {
    print("Syscall failed: " + r["error"])
}
```

### Unhandled exceptions

Any unhandled exception in the script causes the worker to exit with
`{status: "failure", error: "<exception message>"}`. The flow node is marked failed.
Use `try/catch` for recoverable errors; let genuine programming errors propagate so
they appear in the execution log.

### Per-node execution limits

Each flow node runs as a single execution unit (EU) with hard caps:

| Limit | Default | Env var override |
|-------|---------|-----------------|
| Syscalls per node | 100 | `AINDY_MAX_SYSCALLS_PER_EXECUTION` |
| Wall-clock time | 5 minutes | `AINDY_MAX_WALL_TIME_MS` |

A node that exceeds either limit is terminated mid-execution with
`RESOURCE_LIMIT_EXCEEDED` and no retry. The limits apply per node, not per flow —
a 10-node flow with 50 syscalls per node works fine.

**Design guideline:** If your logic requires more than ~60 syscalls or multiple
LLM round trips, split it across multiple nodes connected by a WAIT/RESUME
checkpoint. Each WAIT creates a new EU with a fresh quota. A single monolithic
node doing 101 syscalls fails at syscall 101; two nodes doing 50 each complete
normally.

---

## 6. Type quick-reference (nodus-lang 4.0.3)

| Value | `type()` result |
|-------|----------------|
| `42` | `"number"` (float) |
| `42i` | `"int"` |
| `"hello"` | `"string"` |
| `true` / `false` | `"bool"` |
| `nil` | `"nil"` |
| `[1, 2]` | `"list"` |
| `{"key": "val"}` | `"map"` (string keys) |
| `{field: val}` | `"record"` (bare-identifier keys) |
| err records | `"error"` |

Check stdlib return values with `type(result) == "error"` before accessing their fields.

---

## 7. Examples

### 7.1 Read, process, write back to memory

```nd
// Read recent memories about the user's project
let nodes = recall("project status", ["project"], 5)

let summary = ""
let i = 0
while i < len(nodes) {
    summary = summary + nodes[i]["content"] + "\n"
    i = i + 1i
}

// Store the aggregated summary
remember(summary, ["project", "summary"], "execution", 0.8)
set_state("summary", summary)
```

### 7.2 Dispatch a syscall and use the result

```nd
// Write a memory node via syscall (alternative to remember())
let write_result = sys("sys.v1.memory.write", {
    "content":   "Agent completed onboarding step 3",
    "tags":      ["onboarding", "agent"],
    "node_type": "execution",
    "significance": 0.7
})

if write_result["status"] == "success" {
    let path = write_result["data"]["path"]
    set_state("last_memory_path", path)
}
```

### 7.3 Submit a background job

```nd
let r = sys("sys.v1.job.submit", {
    "task_name": "generate_weekly_report",
    "payload":   {"user_id": user_id, "week": "2026-06"},
    "source":    "nodus_script"
})

if r["status"] == "success" {
    set_state("report_job_id", r["data"]["log_id"])
}
```

### 7.4 WAIT for user confirmation

```nd
// Step 1 — emit an event asking for confirmation, then suspend
let emit_r = sys("sys.v1.event.emit", {
    "event_type": "approval.requested",
    "payload":    {"message": "Approve deletion of stale memories?", "user_id": user_id}
})

set_state("nodus_wait_requested", true)
set_state("nodus_wait_event_type", "approval.granted")
// Script exits here — flow suspends
```

```nd
// Step 2 — after "approval.granted" fires, flow resumes; this script runs
let received = get_state("nodus_received_events")
let approval  = received["approval.granted"]
set_state("approved_by", approval["approver_id"])
```

---

## 8. Nodus version and upgrade notes

A.I.N.D.Y. pins **nodus-lang == 4.0.3** (NODUS-UPGRADE-1 closed 2026-06-11).

**Notable v4 breaking changes vs v3** (none affect A.I.N.D.Y. scripts):

| Change | v3 behavior | v4 behavior |
|--------|-------------|-------------|
| `type(float)` | `"number"` | `"float"` |
| `==` across type families | coerces (`0 == false` is true) | strict (`0 == false` is false) |
| `index_of` not-found | returns `-1` | returns `nil` |
| Float `/ 0` | throws | returns `inf`/`nan` |

**Embedding API change (v3 → v4):** `NodusRuntime.last_vm` (public attribute) was replaced
by `_get_active_vm()` (method). Use `_get_active_vm()` to access the post-execution VM.
The `_last_vm` property still works but emits a `DeprecationWarning`.

**`allowed_paths` default changed:** v4 defaults to `[os.getcwd()]` (CWD-jailed). v3
defaulted to `None` (unrestricted). Pass `allowed_paths=None` explicitly to restore
unrestricted access when needed.
