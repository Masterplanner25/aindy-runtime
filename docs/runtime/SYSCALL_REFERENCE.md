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

## Scope requirements

When calling syscalls via the API (not from within a Nodus script), the API key must carry
the appropriate scope. The domain → scope mapping:

| Syscall prefix | Required scope |
|----------------|---------------|
| `sys.v1.memory.*` | `memory:write` |
| `sys.v1.flow.*` | `flow:execute` |
| `sys.v1.agent.*` | `agent:run` |
| `sys.v1.webhook.*` | `webhook:manage` |

`sys.v1.event.*`, `sys.v1.nodus.*`, `sys.v1.job.*`, and `sys.v1.execution.*` are enforced
at the capability level in the calling context's `SyscallContext.capabilities`. A platform
API key must additionally carry the `execution.read` scope for the dispatch route to grant
`execution.read`; JWT callers receive it in the default capability set.
