---
title: "Syscall Reference"
api_version: "1.0"
last_verified: "2026-09-03"
status: current
owner: "platform-team"
---

# Syscall Reference

> **Capability is not the same thing as an API-key scope.** Every entry below lists the
> **capability** the dispatcher enforces (`SyscallContext.capabilities`). A Platform API key
> instead carries **scopes** (`flow.execute`, `memory.read`, …), and
> `/platform/syscall` derives the grant from the requested syscall's own capability against a
> governed scope map — see `_resolve_dispatch_capabilities` in `platform_ops_router.py`.
> The two names coincide for most syscalls and deliberately differ for some: `sys.v1.flow.run`
> requires capability `flow.run` but is granted by the **`flow.execute`** scope. Do not assume
> a capability name is a valid scope; the scope list is in `AINDY/auth/api_key_auth.py` and in
> the README.
>
> Capability values below are generated from the registry, not hand-maintained.

Quick reference for all syscalls registered in `SYSCALL_REGISTRY`. All calls return the
standard response envelope:

```json
{
  "status":            "success" | "partial" | "unknown" | "error",
  "outcome":           dict | None,   # per-unit detail when partial/unknown
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

**★ `status` gained `partial` and `unknown` (`EFFECT-PARTIAL-1`).** A batched effect where some
units applied and some did not is neither `success` nor `error` — forcing it into one is a lie or
a waste. **A consumer must treat any status that is not `success` as not-success and reconcile;
never branch on `== "error"`.** `outcome` carries the per-unit detail (`None` otherwise). Nothing
emits the new values yet: a handler opts in via `AINDY.kernel.syscall_outcome`.

`data` contains the handler's output on success. `error` is a string on failure, null
on success. `warning` is set when the syscall is deprecated.

For the dispatcher pipeline, ABI versioning, and registration guide see
[SYSCALL_SYSTEM.md](./SYSCALL_SYSTEM.md).

For calling syscalls from Nodus scripts see [NODUS_DEVELOPER_GUIDE.md](./NODUS_DEVELOPER_GUIDE.md).

The live list of registered syscalls (with stability flags) is also available at
`GET /platform/syscalls` at runtime.

---

> **Stability values corrected 2026-08-13.** Four entries below claimed `stable` while
> `AINDY/kernel/syscall_registry.py` registers them `stable=False`:
> `sys.v1.memory.list`, `sys.v1.memory.tree`, `sys.v1.memory.trace`, `sys.v2.memory.read`.
> The registry is the source of truth — it is what `GET /platform/syscalls` returns and what
> `aindy-apps-monolith`'s published `docs/api/API_REFERENCE.md` mirrors ("Status: experimental").
> This document was the only one of the three that disagreed.
>
> **`stable` here means advertised maturity, not name permanence.** A syscall can be
> experimental *and* protected against renaming: `memory.list`, `memory.tree` and
> `memory.trace` are all three of experimental, dispatched by the shipped SDK, and covered by
> the rename guard in `tests/unit/test_cross_repo_compatibility.py`.

## Domain: `memory`

### `sys.v1.memory.read`

Recall memory nodes for the calling user. Combines semantic search and MAS path lookup.

**Capability:** `memory.read`

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

**Capability:** `memory.write`

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

### `sys.v1.memory.delete`

Hard-delete a memory node owned by the calling user.

**Stability:** stable

**Capability:** `memory.delete` (dedicated scope — a `memory.write`-scoped key does **not** grant delete).

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `node_id` | string | yes | UUID of the node to delete. |

**Returns:** `{deleted: bool, node_id: "..."}`

**Semantics:** Tenant-scoped and idempotent. Deleting a missing node, or a node owned
by another tenant, returns `{deleted: false, ...}` without error and without revealing
whether the node exists. Hard delete — the database cascades (`ON DELETE CASCADE`) to the
node's history, trace memberships, causal edges, and links. **Irreversible.**

---

### `sys.v1.memory.link`

Link two memory nodes owned by the calling user.

**Stability:** experimental — **not** in the SDK rename guard. The graph surface
(`link` / `traverse` / `expand`) is still moving; treat the name as unpinned.

**Capability:** `memory.link` (dedicated — a `memory.write`-scoped grant does **not** confer it).
Writing a node and wiring the graph between nodes are different powers, the same reasoning that
gives `memory.delete` its own capability.

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `source_id` | string | yes | UUID of the node the link starts from. |
| `target_id` | string | yes | UUID of the node the link points to. |
| `link_type` | string | no | Default: `"related"`. |
| `weight` | float | no | Default: 0.5. |

**Returns:** `{link: {...}}`

**Semantics:** Tenant-scoped. Both endpoints are resolved against the caller's own nodes before
the write, and a node belonging to another tenant is reported **identically to one that does not
exist** — the call is not an existence oracle for other tenants' ids. Declared `EXACTLY_ONCE`:
`create_link` inserts a row, so a retry would otherwise build a *second* edge between the same
pair.

> **★ Not reachable via `POST /platform/syscall`.** `memory.link` is deliberately absent from
> the governed dispatch surface, so that route yields an empty grant and the dispatcher denies
> the call. Use `POST /memory/links`. Publishing an experimental syscall to SDK callers is the
> half that cannot be withdrawn; when it is published it will get a scope of its own rather than
> riding on `memory.write`, which would undo the capability split at the scope layer.

---

### `sys.v1.memory.search`

Semantic search over the user's memory nodes.

**Capability:** `memory.read`

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

**Capability:** `memory.read`

**Stability:** experimental — *corrected 2026-08-13; this said `stable`.* Registered `stable=False` in `syscall_registry.py`, which is what `GET /platform/syscalls` advertises and what the apps API reference publishes.

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

**Capability:** `memory.read`

**Stability:** experimental — *corrected 2026-08-13; this said `stable`.* Registered `stable=False` in `syscall_registry.py`, which is what `GET /platform/syscalls` advertises and what the apps API reference publishes.

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `path` | string | yes | MAS prefix. |
| `limit` | int | no | Max nodes before building tree. Default: 200. |

**Returns:** `{tree: {...}, node_count: N, path: "..."}`

---

### `sys.v1.memory.trace`

Follow the causal chain from a node at a path.

**Capability:** `memory.read`

**Stability:** experimental — *corrected 2026-08-13; this said `stable`.* Registered `stable=False` in `syscall_registry.py`, which is what `GET /platform/syscalls` advertises and what the apps API reference publishes.

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `path` | string | yes | Exact MAS path to start from. |
| `depth` | int | no | Max hops to follow. Default: 5. |

**Returns:** `{chain: [...], depth: N, path: "..."}`

---

### `sys.v2.memory.read`

Enhanced recall with structured field filters. Extends v1.

**Capability:** `memory.read`

**Stability:** experimental — *corrected 2026-08-13; this said `stable`.* Registered `stable=False` in `syscall_registry.py`, which is what `GET /platform/syscalls` advertises and what the apps API reference publishes.

**Payload:** All v1 keys, plus:

| Key | Type | Description |
|-----|------|-------------|
| `filters` | dict | Post-recall field filters. Supported: `memory_type` (string), `node_type` (string), `min_impact` (float). |

**Returns:** `{nodes: [...], count: N, version: "v2"}`

---

## Domain: `flow`

### `sys.v1.flow.run`

Execute a registered flow by name.

**Capability:** `flow.run`

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

**Capability:** `flow.execute`

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

**Capability:** `nodus.execute`

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

**Capability:** `event.emit`

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

**Capability:** `job.submit`

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

**Capability:** `agent.execute`

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

**Capability:** `agent.read`

**Stability:** experimental

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `status` | string or list[string] | no | Filter by status value(s). |

**Returns:** `{count: N}`

---

### `sys.v1.agent.list_recent_durations`

List recent `AgentRun` timing fields for duration calculations.

**Capability:** `agent.read`

**Stability:** experimental

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `window_hours` | int | no | Lookback window in hours. Default: 1. |

**Returns:** `{durations: [{started_at, completed_at}, ...], count: N}`

---

### `sys.v1.agent.list_recent_runs`

List recent `AgentRun` rows for a user as plain dicts.

**Capability:** `agent.read`

**Stability:** experimental

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `limit` | int | no | Max rows. Default: 10. |

**Returns:** `{runs: [{...}, ...]}`

---

### `sys.v1.agent.ensure_initial_run`

Find or create the initial signup `AgentRun` sentinel for a user.

**Capability:** `agent.write`

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

## Domain: `observability`

### `sys.v1.observability.support_metrics`

Tenant-scoped aggregate rollup of observability + execution behavior, for the
app-side Infinity support layer (INFINITY-RUNTIME-1 item 3). Read-only. Filters
request metrics, agent-run / async-job distributions, and Infinity loop-event
counts to the caller's tenant; includes a coarse platform-health signal.

**Capability:** `execution.read`

**Stability:** stable

**Payload:**

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `window_hours` | int | no | Lookback window; clamped to `[1, 168]` (default 24). |

**Returns:** `{generated_at, window_hours, observability: {requests: {total, errors, error_rate_pct, avg_latency_ms}, platform_health_status}, execution: {agent_runs: {total, by_status}, async_jobs: {total, by_status}}, infinity_events: {recall_used, score_computed, next_action_chosen, total}}`

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
| `memory.delete` | `sys.v1.memory.delete` | `memory.delete` (dedicated — **not** granted by `memory.write`) |
| `memory.link` | `sys.v1.memory.link` | **none — off this surface.** No scope authorizes it here; the dispatch route denies it. Use `POST /memory/links`. |
| `flow.run` | `sys.v1.flow.run` | `flow.execute` |
| `event.emit` | `sys.v1.event.emit` | `event.emit` |
| `execution.read` | `sys.v1.execution.get`, `sys.v1.observability.support_metrics` | `execution.read` |

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
