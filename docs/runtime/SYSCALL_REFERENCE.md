---
title: "Syscall Reference"
api_version: "1.0"
last_verified: "2026-07-05"
status: current
owner: "platform-team"
---

# Syscall Reference

Quick reference for all syscalls registered in `SYSCALL_REGISTRY`. All calls return the
standard response envelope:

```json
{
  "status":            "success" | "error",
  "data":              {},
  "trace_id":          "...",
  "execution_unit_id": "...",
  "syscall":           "sys.v1.domain.action",
  "version":           "v1",
  "duration_ms":       42,
  "error":             null,
  "warning":           null
}
```

`data` contains the handler's output on success. `error` is a string on failure, null
on success. `warning` is set when the syscall is deprecated.

For the dispatcher pipeline, ABI versioning, and registration guide see
[SYSCALL_SYSTEM.md](./SYSCALL_SYSTEM.md).

For calling syscalls from Nodus scripts see [NODUS_DEVELOPER_GUIDE.md](./NODUS_DEVELOPER_GUIDE.md).

The live list of registered syscalls (with stability flags) is also available at
`GET /platform/syscalls` at runtime.

---

## Domain: `memory`

### `sys.v1.memory.read`

Recall memory nodes for the calling user. Combines semantic search and MAS path lookup.

**Stability:** stable

**Payload (all optional):**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `path` | string | — | MAS path or wildcard (overrides tag/query if exact). |
| `query` | string | — | Semantic search string. |
| `tags` | list[string] | — | Tag filter. |
| `limit` | int | 5 | Max results. |
| `node_type` | string | — | Filter by node type. |

**Returns:** `{nodes: [...], count: N}`

---

### `sys.v1.memory.write`

Persist a new memory node for the calling user.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `content` | string | yes | Node text. |
| `tags` | list[string] | no | Classification tags. |
| `node_type` | string | no | Default: `"execution"`. |
| `significance` | float | no | Relevance weight 0.0–1.0. Default: 0.5. |
| `path` | string | no | MAS path. Auto-generated if omitted. |
| `namespace` | string | no | Optional namespace segment. |
| `addr_type` | string | no | Optional sub-category segment. |

**Returns:** `{node: {...}, path: "/memory/..."}`

---

### `sys.v1.memory.search`

Semantic search over the user's memory nodes.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `query` | string | yes | Search string. |
| `limit` | int | no | Max results. Default: 5. |
| `path` | string | no | MAS path prefix to scope the search. |

**Returns:** `{nodes: [...], count: N}`

---

### `sys.v1.memory.list`

List nodes at a MAS path, one level or recursive.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `path` | string | yes | MAS prefix. Use `/*` for one level, `/**` for recursive. |
| `limit` | int | no | Max results. Default: 50. |

**Returns:** `{nodes: [...], count: N, path: "..."}`

**Example paths:** `/memory/user-123/notes/*`, `/memory/user-123/**`

---

### `sys.v1.memory.tree`

Return a hierarchical tree of nodes under a path.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `path` | string | yes | MAS prefix. |
| `limit` | int | no | Max nodes before building tree. Default: 200. |

**Returns:** `{tree: {...}, node_count: N, path: "..."}`

---

### `sys.v1.memory.trace`

Follow the causal chain from a node at a path.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `path` | string | yes | Exact MAS path to start from. |
| `depth` | int | no | Max hops to follow. Default: 5. |

**Returns:** `{chain: [...], depth: N, path: "..."}`

---

### `sys.v2.memory.read`

Enhanced recall with structured field filters. Extends v1.

**Stability:** stable

**Payload:** All v1 keys, plus:

| Key | Type | Description |
|-----|------|-------------|
| `filters` | dict | Post-recall field filters. Supported: `memory_type` (string), `node_type` (string), `min_impact` (float). |

**Returns:** `{nodes: [...], count: N, version: "v2"}`

---

## Domain: `flow`

### `sys.v1.flow.run`

Execute a registered flow by name.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `flow_name` | string | yes | Must be registered in `FLOW_REGISTRY`. |
| `initial_state` | dict | no | Passed to `PersistentFlowRunner.start()`. |
| `workflow_type` | string | no | Default: same as `flow_name`. |

**Returns:** `{run_id: "...", status: "...", ...}`

---

### `sys.v1.flow.execute_intent`

Top-level intent execution with strategy selection.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `intent_data` | dict | yes | At minimum `{"workflow_type": "..."}`. |

**Returns:** `{intent_result: {...}}`

---

## Domain: `nodus`

### `sys.v1.nodus.execute`

Execute a Nodus script via flow-backed orchestration.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `script` | string | yes | Nodus source code. |
| `input_payload` | dict | no | Script input variables. |
| `error_policy` | string | no | `"halt"` (default) or `"continue"`. |
| `workflow_type` | string | no | Default: `"nodus_execute"`. |
| `trace_id` | string | no | Correlation ID. |
| `node_max_retries` | int | no | Per-node retry override. |

**Returns:** `{nodus_result: {...}}`

---

## Domain: `event`

### `sys.v1.event.emit`

Emit a `SystemEvent` on the A.I.N.D.Y. event bus.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `event_type` | string | yes | Event type string. e.g. `"operation.completed"`. |
| `payload` | dict | no | Merged into the event payload. |

**Returns:** `{event_id: "..."}`

---

## Domain: `job`

### `sys.v1.job.submit`

Submit a named async job to the automation pipeline.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `task_name` | string | yes | Name registered in `_JOB_REGISTRY`. |
| `payload` | dict | no | Forwarded to the job handler. |
| `source` | string | no | Label for the AutomationLog. Default: `"syscall"`. |
| `max_attempts` | int | no | Retry budget. Default: 1. |

**Returns:** `{log_id: "...", task_name: "...", source: "..."}`

---

## Domain: `agent`

### `sys.v1.agent.execute`

Execute an approved `AgentRun` via the deterministic runtime.

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `run_id` | string | yes | ID of an `AgentRun` with `status == "approved"`. |

**Returns:** `{run_result: {...}}`

---

### `sys.v1.agent.cancel`

Cooperatively cancel a non-terminal `AgentRun` to the terminal `cancelled` state
(operator kill switch, AGENT-HARDEN-1). Flips the run via an atomic compare-and-set
from any active status (`pending_approval` / `approved` / `executing` / `waiting` /
`delegated`); the change is committed so a run mid-execution on the VM-backed
segment chain observes it at the next **segment boundary** and halts before the
next tool call, and a parked (`waiting`) run never resumes. Runs already terminal
(`completed` / `failed` / `cancelled`) are an idempotent no-op.

**Capability:** `agent.cancel`

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `run_id` | string | yes | `AgentRun` id to cancel. |
| `reason` | string | no | Recorded in `error_message` and the `CANCELLED` event. |

**Returns:** `{cancelled: true|false, status, previous_status, run_id}` — `cancelled`
is `false` (with the current `status`) when the run was already terminal.

---

### `sys.v1.agent.undo`

Reverse a completed `AgentRun`'s reversible effects (compensating undo, AGENT-HARDEN-3).
Walks the run's successful `EffectRecord`s newest-first and invokes each owning
syscall's registered `compensate` hook. Effects whose syscall declares no
compensator are reported as **irreversible** (surfaced, never silently skipped);
compensator failures are reported as **failed**. Every attempt is written to the
append-only `effect_reversals` audit log. Undo ≠ replay: `replay` re-does a run,
`undo` reverses one.

**Capability:** `agent.undo`

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `run_id` | string | yes | `AgentRun` id whose effects to reverse. |

**Returns:** `{run_id, reversed: [action_type…], irreversible: [action_type…], failed: [{action_type, error}…]}`

---

### `sys.v1.agent.simulate`

Predicted-effect dry-run of an `AgentRun` (effect simulation, AGENT-HARDEN-4). Runs
the run's plan with the `call_tool` seam **shadowed** — every tool call returns a
predicted result and records a "would-write" intent instead of executing, so there
are **zero real side effects**. The report is persisted under
`run.result["simulation"]` for the apps `AgentApprovalInbox` and the run's status is
left unchanged (this is a preview, not an execution). A capability token is used so
the preview reflects real grants — the run's own token if present, otherwise a
freshly minted preview token for the plan.

**Capability:** `agent.simulate`

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `run_id` | string | yes | `AgentRun` id to simulate. |
| `virtual_tools` | dict | no | AGENT-HARDEN-4b — fake tool implementations (the simulated world): `{tool_name: {"result": <any>, "success"?: bool, "error"?: str}}`. A tool with a fake impl returns that scripted output (so downstream steps see realistic data); others get a deterministic placeholder. Still zero real execution. |

**Returns:** `{simulated: true, steps: [...], simulated_effects: [{tool, args, risk_level, capability_ok, predicted_result, source: "virtual"|"placeholder", executed: false}…], steps_total, effects_total}`

---

### `sys.v1.agent.count_runs`

Count `AgentRun` rows for a user.

**Stability:** experimental

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `status` | string or list[string] | no | Filter by status value(s). |

**Returns:** `{count: N}`

---

### `sys.v1.agent.list_recent_durations`

List recent `AgentRun` timing fields for duration calculations.

**Stability:** experimental

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `window_hours` | int | no | Lookback window in hours. Default: 1. |

**Returns:** `{durations: [{started_at, completed_at}, ...], count: N}`

---

### `sys.v1.agent.list_recent_runs`

List recent `AgentRun` rows for a user as plain dicts.

**Stability:** experimental

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `limit` | int | no | Max rows. Default: 10. |

**Returns:** `{runs: [{...}, ...]}`

---

### `sys.v1.agent.ensure_initial_run`

Find or create the initial signup `AgentRun` sentinel for a user.

**Stability:** experimental

**Payload:** (none required)

**Returns:** `{run_id: "...", created: true|false}`

---

## Domain: `execution`

### `sys.v1.execution.get`

Return status and resource metrics for a single execution unit. Read-only and
tenant-scoped — only resolves `ExecutionUnit` rows owned by the caller. Backs the
SDK's `client.execution.get()`.

**Capability:** `execution.read`

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `execution_id` | string | yes | ExecutionUnit id, source run id (agent_run / flow_run), or flow_run_id returned by a prior `flow.run` / `nodus.execute` / agent call. |

**Returns:** `{execution_id, type, status, syscall_count, wall_time_ms, memory_bytes, priority, quota_group, source_type, source_id, created_at, completed_at}`

---

## Scope requirements (`POST /platform/syscall`)

When dispatching a syscall over the API (not from within a Nodus script), the route grants
**exactly the requested syscall's own required capability** — least-privilege, one capability
per dispatch. Only capabilities on the public **dispatch surface** below are grantable this
way; every other syscall (`agent.*`, `job.submit`, `nodus.execute`, admin) is reached through
its own dedicated route, never `/platform/syscall`.

**JWT callers** (`/auth/login`) are trusted platform users and receive the requested
capability without a scope check. **Platform-API-key callers** must additionally carry one of
the authorizing scopes below (or `platform.admin`, which bypasses the scope gate):

| Syscall capability | Backing syscalls | Authorizing API-key scope(s) |
|---|---|---|
| `memory.read` | `sys.v1.memory.read` / `.search` / `.list` / `.tree` / `.trace` | `memory.read` **or** `memory.write` |
| `memory.write` | `sys.v1.memory.write` | `memory.write` |
| `flow.run` | `sys.v1.flow.run` | `flow.execute` |
| `event.emit` | `sys.v1.event.emit` | `event.emit` |
| `execution.read` | `sys.v1.execution.get` | `execution.read` |

Notes:
- `memory.write` scope implies read — a write-scoped key can also read/search.
- `flow.run` is authorized by the **`flow.execute`** scope — the same scope that gates the
  dedicated `POST /platform/flows/{name}/run` route, so a flow runs under one consistent grant
  regardless of entrypoint.
- `event.emit` is a first-class scope (added 2026-07-07). Emitting an event can resume waiting
  flow/agent runs, so it is a side-effecting grant, not read-only; API keys must opt in by
  carrying the scope. JWT callers receive it by default.
- A dispatch for an unknown or off-surface syscall is granted no capability; the dispatcher
  then returns its canonical `404 Unknown syscall` / `403 Permission denied`.
