---
title: "Syscall System"
last_verified: "2026-11-04"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Syscall System

Repository ownership:

- this document belongs to the future `aindy-runtime` repo
- app docs may describe app-owned syscall handlers, but the syscall ABI and
  dispatcher contract remain runtime-owned
- the broader documentation split map lives in
  [Runtime Docset Boundary](../archive/RUNTIME_DOCSET_BOUNDARY.md)

For repo-split purposes, `AINDY.kernel.syscall_dispatcher` and
`AINDY.kernel.syscall_registry` are approved public runtime imports under the
[Runtime Public API Contract](./PUBLIC_API_CONTRACT.md).

For stability tiers across the public runtime surface, including which syscall
families are stable versus experimental, use
[Public Runtime Surfaces](./PUBLIC_RUNTIME_SURFACES.md).

A.I.N.D.Y. syscalls are the single gated interface between Nodus scripts and host services. All cross-boundary calls — memory reads, flow executions, event emissions — route through the syscall dispatcher. No Nodus code touches a DB session or a service function directly.

---

## 1. Overview

**`SyscallDispatcher.dispatch()` is the single entry point for syscalls.** Both Nodus scripts (via the `sys()` global) and host-layer entry points (routes, agents, schedulers) use the same syscall interface. Entry-point functions like `run_flow()` and `execute_intent()` are syscall proxies — thin wrappers that build a `SyscallContext` and call `get_dispatcher().dispatch(...)`. Not every effect in the runtime is a syscall; see [What does not go through the dispatcher](#what-does-not-go-through-the-dispatcher).

```
Route / Agent / CLI / Scheduler
    │  run_flow() / execute_intent() / run_nodus_script_via_flow()
    │  [syscall proxy — lazy import of get_dispatcher()]
    ▼
SyscallDispatcher.dispatch()
    │  parse version · check capability · validate input · execute handler
    ▼
Registered handler
    │  e.g. _handle_flow_run(payload, ctx) → calls _run_flow_direct()
    ▼
PersistentFlowRunner → ExecutionPipeline → ExecutionDispatcher → Runtime
    ▼
Standard response envelope
    { status, data, version, warning, trace_id, duration_ms, error, failure_class }

---

Nodus script
    │  sys("sys.v1.memory.read", {"query": "auth"})
    ▼
SyscallDispatcher.dispatch()
    │  (same pipeline as above)
    ▼
Standard response envelope
```

The dispatcher returns the envelope on every path but one. It raises `SyscallContractViolation` when a handler returns a non-dict while the idempotency gate is engaged for the call. With no gate engaged, the same mistake is a `fatal` error envelope. This is a programming error, and code with a broad `except Exception` above `dispatch()` needs an explicit `except SyscallContractViolation: raise` before it.

### What does not go through the dispatcher

These paths get none of the dispatcher's checks. The list records known cases and is not a census of every route.

| Path | What it does instead |
|---|---|
| Anonymous / system calls (`user_id=None`) to the proxies | The dispatcher requires a `user_id`, so these invoke the `_*_direct()` implementation directly. This preserves system-internal and test-harness execution paths. |
| Agent approval (`approve_run()` → `execute_run`) | An atomic compare-and-swap, then execution on a daemon thread. It gets no `EffectRecord` idempotency from the dispatcher. |
| `POST /memory/nodes/search` | Calls `MemoryNodeDAO.find_similar` directly, scoped by the route to the authenticated user. |

---

## 2. Syscall Name Convention

```
sys.{version}.{domain}.{action}
```

Examples:
- `sys.v1.memory.read`
- `sys.v1.flow.run`
- `sys.v2.memory.read`  ← v2 evolution with added `filters` param

`parse_syscall_name("sys.v1.memory.read")` → `("v1", "memory.read")`

---

## 3. Response Envelope

All calls return the same shape:

```json
{
    "status":            "success" | "partial" | "unknown" | "error",
    "outcome":           dict | None,   # per-unit detail when partial/unknown
    "data":              {},
    "trace_id":          "run-123",
    "execution_unit_id": "run-123",
    "syscall":           "sys.v1.memory.read",
    "version":           "v1",
    "duration_ms":       42,
    "error":             null,
    "warning":           null
}
```

**★ `status` gained `partial` and `unknown` (`EFFECT-PARTIAL-1`).** A batched effect where some
units applied and some did not is neither `success` nor `error` — forcing it into one is a lie or
a waste. **A consumer must treat any status that is not `success` as not-success and reconcile;
never branch on `== "error"`.** `outcome` carries the per-unit detail (`None` otherwise).

A handler opts in via `AINDY.kernel.syscall_outcome`, by returning a `partial(...)` or `unknown(...)`
claim or raising `EffectOutcomeUnknown`. Both values are live: `sys.v1.flow.run` reports `partial`
when a run completes past failed fan-out branches, and `outbound_http` and `mcp_client` raise
`EffectOutcomeUnknown`.

**A replayed `success` differs slightly:** when the idempotency gate replays a completed effect,
the envelope has no `outcome` key and `warning` is always `null`, even for a deprecated syscall.

- `version` — always present; the parsed ABI version string.
- `warning` — set when the syscall is deprecated; null otherwise.
- `failure_class` — **`error` and `unknown` envelopes only** (`RETRY-CLASSIFY-1`, 2026-09-16). On
  an error, one of `transient | cancelled | permission | not_found | invalid | fatal`, declared
  by the path that refused (unknown syscall → `not_found`, capability/tenant/ended run →
  `permission`, malformed extension metadata or input validation → `invalid`, quota or a refused
  `AT_MOST_ONCE` → `transient`, cancelled run → `cancelled`, contract violation → `fatal`); a
  handler's own exception is classified by the fallback table (a tripped circuit breaker is
  `transient`). An `unknown` envelope carries `failure_class: unknown` (DEC-090). Absent on
  `success` and `partial`. Only `transient` may be retried — see `RETRY_POLICY.md`.
- `error` — set on failure; null on success.
- `data` — handler output on success; `{}` on error.

---

## 4. Dispatcher Pipeline

Steps executed in order for every call:

| Step | Action | Fatal? |
|------|--------|--------|
| 0 | **Resolve trace context** — inherit or establish `trace_id` / `eu_id` via ContextVars | never fatal |
| 1 | Parse `sys.{version}.{action}` from name | error if malformed |
| 2 | Resolve version; optional fallback (`SYSCALL_VERSION_FALLBACK`) | error if unresolvable |
| 3 | Look up entry in `SYSCALL_REGISTRY` | error if not found |
| 4 | Enforce exact capability membership from `SyscallContext.capabilities` | error if denied |
| 5 | Enforce non-empty `user_id` | error if absent |
| 6 | Compare any payload `user_id` with the caller (DEC-103) | error on mismatch, unless `AINDY_PAYLOAD_IDENTITY_DISPATCH=observe` (count only) |
| 7 | Check extension-call tenant metadata | error if missing or a different tenant |
| 8 | Resource quota check via `ResourceManager` | error if over quota; quota backend failures fail open only in development/test and fail closed otherwise |
| 9 | Input validation against `entry.input_schema` | error if invalid |
| 10 | Deprecation check — set `warning` in envelope | non-fatal, continues |
| 11 | Idempotency gate, when it engages | may replay a recorded result or refuse without running the handler |
| 12 | Refuse if the current run is in a terminal status (DEC-056) | error if ended; an unreadable status counts as not ended (fails open) |
| 13 | Execute handler | error if handler raises; `EffectOutcomeUnknown` becomes an `unknown` envelope |
| 14 | Check handler output type; resolve any outcome claim | see [Output Validation](#output-validation) |
| 15 | Output validation against `entry.output_schema` | error for stable syscalls; warning-only for experimental syscalls; skipped for `unknown` |
| 16 | Complete the effect record, if the gate engaged | non-fatal; a ledger failure degrades to `AT_LEAST_ONCE` |
| 17 | Record syscall usage in `ResourceManager` | non-fatal |
| 18 | Emit `SYSCALL_EXECUTED` system event | non-fatal |
| 19 | Return the envelope; reset ContextVar tokens | — |

Step 0 is the trace propagation gate — see §12 for detail.

### The idempotency gate

The gate engages only when all of these hold:

- the syscall declares `EXACTLY_ONCE` or `AT_MOST_ONCE`, or a continued run has durable effects active;
- the caller named an execution unit, and that unit's id is a usable scope (`_gate_scope_engaged`);
- `AINDY_SYSCALL_IDEMPOTENCY` is on, durable effects are active, or the syscall declares `AT_MOST_ONCE`.

A declared guarantee alone therefore proves nothing about a given call. If gate machinery fails or
a concurrent call holds the slot, `EXACTLY_ONCE` degrades to `AT_LEAST_ONCE` and `AT_MOST_ONCE`
refuses. See [`IDEMPOTENCY_CONTRACT.md`](./IDEMPOTENCY_CONTRACT.md).

### Tenant identity: what the dispatcher does and does not enforce

Steps 5–7 establish **who is calling**. They do not establish **which rows the caller may touch**.

| Enforced by | Guarantee |
|---|---|
| Dispatcher | The call has an authenticated `user_id`. |
| Dispatcher | A payload cannot name a different `user_id`, unless an operator sets the `observe` opt-out. |
| Dispatcher | An extension call's tenant metadata matches the caller. |
| Each handler | Queries are filtered to `context.user_id`, and memory paths stay under the tenant's root. The dispatcher cannot check this. |

A new handler that forgets to filter by `context.user_id` passes every dispatcher check. The tenant
is also the user: `tenant_id` is set from `user_id`, so this is not multi-user-per-organisation
isolation.

---

## 5. Registry

### SyscallEntry

Defined in `kernel/syscall_registry.py`.

```python
class SyscallEntry:
    handler:        Callable[[dict, SyscallContext], dict]
    capability:     str          # required capability name
    description:    str
    input_schema:   dict | None  # lightweight schema, NOT JSON Schema (§7)
    output_schema:  dict | None  # lightweight schema, NOT JSON Schema (§7)
    stable:         bool         # False = experimental; defaults to True
    deprecated:     bool
    deprecated_since: str | None # e.g. "v2"
    replacement:    str | None   # e.g. "sys.v2.memory.read"
    compensate:     Callable | None  # undo hook; None = the effect is irreversible
    execution_guarantee: str     # defaults to "AT_LEAST_ONCE"
```

**`sys.v1.agent.undo` does not currently reverse real effects (`UNDO-COMPENSATORS-1`).** It selects
effects by `EffectRecord.execution_id`, and nothing in production sets that column, so a run's
effects are never found: undo returns an empty summary, not a list of `irreversible` effects. A
consumer's `compensate=` hook is not reached either. Separately, no syscall in the runtime's own
registry declares a compensator yet.

### VersionedSyscallRegistry

`SYSCALL_REGISTRY` is a `VersionedSyscallRegistry(MutableMapping)` that supports two access patterns simultaneously:

```python
# Flat access (backward-compatible)
entry = SYSCALL_REGISTRY["sys.v1.memory.read"]

# Versioned view
all_v1 = SYSCALL_REGISTRY.get_version("v1")  # {"memory.read": SyscallEntry, ...}
available = SYSCALL_REGISTRY.versions()       # ["v1", "v2"]
versioned = SYSCALL_REGISTRY.versioned        # {"v1": {...}, "v2": {...}}
```

### Registering a Syscall

```python
from kernel.syscall_registry import register_syscall

register_syscall(
    name="sys.v1.myservice.dostuff",
    handler=my_handler,
    capability="myservice.dostuff",
    description="Does the stuff",
    input_schema={
        "required": ["target"],
        "properties": {
            "target": {"type": "string"},
            "options": {"type": "object"},
        },
    },
    output_schema={
        "required": ["result"],
        "properties": {"result": {"type": "string"}},
    },
    stable=True,
    deprecated=False,
)
```

---

## 6. ABI Versioning

### Version Rules

- Every syscall declares a version in its name: `sys.v{N}.{domain}.{action}`.
- `v1` is the stable version prefix — the `v1` family will not be removed or renamed. This
  is distinct from per-entry stability: not every `sys.v1.*` syscall is individually stable.
  The authoritative per-entry marker is the `stable` field in `GET /platform/syscalls`.
  `v2` exists as an example evolution (`sys.v2.memory.read`).
- Breaking changes **must** use a new version prefix. Adding optional params is non-breaking.
- Removing a field, changing a type, or changing semantics = breaking = new version.

### Version Fallback

`SYSCALL_VERSION_FALLBACK = False` (default).

- `False` — unknown version returns error immediately.
- `True` — unknown version falls back to `LATEST_STABLE_VERSION` (`v1`) and logs a warning.

Override via env variable or test fixture.

### Deprecation

Set `deprecated=True` + `deprecated_since` + `replacement` when retiring:

```python
register_syscall(
    name="sys.v1.memory.read",
    ...
    deprecated=True,
    deprecated_since="v2",
    replacement="sys.v2.memory.read",
)
```

Deprecated syscalls still execute. The dispatcher sets `warning` in the response envelope and logs a warning. Clients should migrate to the replacement.

---

## 7. Input / Output Validation

Defined in `kernel/syscall_versioning.py`.

### Schema Format

```python
schema = {
    "required": ["field_a"],       # list of required field names
    "properties": {
        "field_a": {"type": "string"},
        "field_b": {"type": "integer"},
        "field_c": {"type": "boolean"},
        "field_d": {"type": "list"},
        "field_e": {"type": "dict"},
    },
}
```

| Accepted type names | Python check |
|---|---|
| `string`, `str` | `str` |
| `int`, `integer` | `int` |
| `float`, `number` | `float` |
| `bool`, `boolean` | `bool` |
| `list`, `array` | `list` |
| `dict`, `object` | `dict` |

### What the validator checks

**This is not JSON Schema.** `validate_payload` reads only `required` and each property's `type`, at the top level.

| Input | Result | Why |
|---|---|---|
| Key not listed in `properties` | Accepted | Unknown keys are not checked. `additionalProperties` is ignored. |
| Wrong type inside a nested object | Accepted | Only top-level fields are checked. |
| Value outside an `enum` | Accepted | `enum` and other constraint keywords are ignored. |
| A `type` name not in the table | Accepted | The check is skipped silently, so a typo disables it. |
| `True` in an `int` field | Accepted | `bool` is a subclass of `int`. |
| `1` in a `float` or `number` field | **Rejected** | `int` is not a `float`. |
| `None` in an optional field with a `type` | **Rejected** | Absent is allowed, but an explicit `None` fails the type check. |
| `None` in a required field | **Rejected** | Treated as missing. |

A handler that needs more than this has to validate its own payload.

### Input Validation (fatal)

Called before the handler. Missing required fields or wrong types → error envelope returned, handler never runs.

### Output Validation

Called after the handler.

- Stable syscall schema mismatch → error envelope returned.
- Experimental syscall schema mismatch → warning logged, result still returned.
- Handler return type mismatch → refused regardless of stability.

The handler return contract is stricter than schema validation: handlers must
return a plain `dict`. A non-dict return is a syscall handler contract violation. It raises
`SyscallContractViolation` when the idempotency gate is engaged for the call, and otherwise
becomes a `fatal` error envelope.

Validation runs after any outcome claim is resolved, so a handler's reserved outcome key is never
seen by its schema. An `unknown` outcome skips output validation, because it has no result to check.

---

## 8. SyscallContext

```python
@dataclass
class SyscallContext:
    execution_unit_id: str          # AgentRun/flow run ID
    user_id:           str          # authenticated tenant identity
    capabilities:      list[str]    # granted capability names
    trace_id:          str          # propagated observability trace ID
    memory_context:    list         # pre-loaded memory nodes (Nodus scripts)
    metadata:          dict         # optional — workflow_type, flow_name, etc.
```

`trace_id` and `execution_unit_id` are automatically propagated through nested
`dispatch()` calls via ContextVars (see §12). Callers never need to manually
thread them through handler code.

Helper builders (from `kernel/syscall_dispatcher.py`):

```python
# From a flow node context dict
ctx = make_syscall_ctx_from_flow(context, capabilities=["memory.read"])

# From an agent tool call
ctx = make_syscall_ctx_from_tool(user_id="user-123", run_id="run-456")

# From a parent context — explicit propagation with capability scoping
ctx = child_context(parent_context, capabilities=["memory.read"])
```

---

## 9. Built-in Syscalls (v1)

Core syscalls registered in `kernel/syscall_registry.py`; domain handlers registered at startup via `register_all_domain_handlers()` in `kernel/syscall_handlers.py`.

The runtime's own registry holds these 23 entries. Read `GET /platform/syscalls` for what a running deployment has, including anything plugins add. Every entry declares an input schema; `event.emit` and `flow.run` declare no output schema.

| Syscall | Capability | Stable | Guarantee | Description |
|---------|-----------|--------|-----------|-------------|
| `sys.v1.memory.read` | `memory.read` | yes | at-least-once | Recall memory nodes by query/tags/path |
| `sys.v1.memory.write` | `memory.write` | yes | exactly-once | Persist a new memory node with optional path |
| `sys.v1.memory.link` | `memory.link` | no | exactly-once | Link two memory nodes owned by the caller |
| `sys.v1.memory.delete` | `memory.delete` | yes | at-least-once | Hard-delete a memory node owned by the caller |
| `sys.v1.memory.search` | `memory.read` | yes | at-least-once | Semantic similarity search |
| `sys.v1.memory.list` | `memory.read` | no | at-least-once | List nodes at a MAS path (one level) |
| `sys.v1.memory.tree` | `memory.read` | no | at-least-once | Hierarchical tree from a MAS path |
| `sys.v1.memory.trace` | `memory.read` | no | at-least-once | Causal chain from a node path |
| `sys.v1.flow.run` | `flow.run` | yes | exactly-once | Execute a registered Nodus flow |
| `sys.v1.flow.execute_intent` | `flow.execute` | yes | exactly-once | Intent-based flow execution — selects strategy, compiles plan, runs flow |
| `sys.v1.nodus.execute` | `nodus.execute` | yes | exactly-once | Run a Nodus script via the standard `NODUS_SCRIPT_FLOW` pipeline |
| `sys.v1.job.submit` | `job.submit` | yes | exactly-once | Submit an async job via `AsyncJobService` (wraps `submit_async_job()`) |
| `sys.v1.agent.execute` | `agent.execute` | yes | at-least-once | Execute an agent run via `execute_run()` (external agent wrapper) |
| `sys.v1.agent.count_runs` | `agent.read` | no | at-least-once | Count AgentRun rows for a user, optionally filtered by status |
| `sys.v1.agent.list_recent_runs` | `agent.read` | no | at-least-once | Recent AgentRun rows as serialized dicts |
| `sys.v1.agent.ensure_initial_run` | `agent.write` | no | at-least-once | Find or create the signup sentinel AgentRun |
| `sys.v1.agent.cancel` | `agent.cancel` | yes | at-least-once | Cooperatively cancel a non-terminal AgentRun |
| `sys.v1.agent.undo` | `agent.undo` | yes | exactly-once | Reverse a completed AgentRun's reversible effects; surface irreversible ones |
| `sys.v1.agent.simulate` | `agent.simulate` | yes | at-least-once | Dry-run an AgentRun with tools shadowed; persists the report |
| `sys.v1.event.emit` | `event.emit` | yes | exactly-once | Emit a system event |
| `sys.v1.execution.get` | `execution.read` | yes | at-least-once | Status and resource metrics for an execution unit |
| `sys.v1.observability.support_metrics` | `execution.read` | yes | at-least-once | Support-layer observability metrics |
| `sys.v2.memory.read` | `memory.read` | no | at-least-once | v2 evolution — adds `filters` dict (memory_type, node_type, min_impact) |

Domain handlers registered at startup via `register_all_domain_handlers()` in `kernel/syscall_handlers.py`. Execution entry-point handlers (`flow.execute_intent`, `nodus.execute`, `job.submit`, `agent.execute`) and runtime-owned helper syscalls are registered directly in `kernel/syscall_registry.py` alongside `flow.run`.

The runtime/helper agent syscalls (`agent.count_runs`, `agent.list_recent_runs`, `agent.ensure_initial_run`) are kernel-owned because identity boot paths and app-side code depend on them as part of core execution and persistence behavior. `agent.list_recent_durations` was REMOVED 2026-09-16 (DEC-022): its only consumer was the runtime's own system-state snapshot, which dispatched it with no tenant and so never received an answer (`SYSTEM-STATE-TENANT-1`); once that read moved to a direct query the syscall had no caller in any repo, and a tenant-scoped read that nothing calls is a surface, not a capability. Agent tools no longer route through a generic `sys.v1.agent.dispatch_tool` proxy; each tool dispatches directly to its owner syscall with an explicit capability context.

---

## 10. Execution Entry-Point Convergence

**All public execution functions are syscall proxies.** The real implementation lives in a `_*_direct()` private function. The proxy: (1) checks for `user_id`, (2) builds a `SyscallContext`, (3) calls `get_dispatcher().dispatch()`, (4) unwraps the envelope. The handler delegates back to `_direct()`.

### Proxy pattern

```python
# Public entry point — proxy
def run_flow(flow_name: str, state: dict, db: Session = None, user_id: str = None) -> dict:
    if not user_id:
        return _run_flow_direct(flow_name, state or {}, db, user_id)   # anonymous
    from kernel.syscall_dispatcher import _EU_ID_CTX, _TRACE_ID_CTX, get_dispatcher, SyscallContext
    # Inherit active trace when called from within a running syscall chain;
    # generate a fresh root trace when called from a route or scheduler.
    trace_id = _TRACE_ID_CTX.get() or str(uuid4())
    eu_id    = _EU_ID_CTX.get()    or trace_id
    ctx = SyscallContext(execution_unit_id=eu_id, user_id=str(user_id),
                         capabilities=["flow.run"], trace_id=trace_id,
                         metadata={"_db": db})
    result = get_dispatcher().dispatch("sys.v1.flow.run", {...}, ctx)
    if result["status"] == "error":
        raise RuntimeError(...)
    return result["data"]["flow_result"]

# Real implementation — called by the syscall handler
def _run_flow_direct(flow_name, state, db, user_id) -> dict:
    ...
    runner = PersistentFlowRunner(flow=flow, db=db, user_id=user_id, ...)
    return runner.start(initial_state=state, flow_name=flow_name)
```

### DB session threading

Routes pass a managed SQLAlchemy session to `run_flow()`. The proxy forwards it as `context.metadata["_db"]`.

Runtime-owned handler contract:

- If `context.metadata["_db"]` is present, the caller owns that SQLAlchemy session.
- The handler may use that session but must not commit, rollback, or close it.
- Caller-owned write handlers may `flush()` successful writes but must leave transaction ownership to the caller.
- If no session is provided, the handler opens and owns its own `SessionLocal()`.
- Handler-owned write sessions commit or rollback locally.

This preserves transaction boundaries across the syscall boundary while still
allowing syscall handlers to run safely in standalone contexts.

### Converged entry points

| Public function | Syscall | `_direct()` impl |
|---|---|---|
| `run_flow()` | `sys.v1.flow.run` | `_run_flow_direct()` |
| `execute_intent()` | `sys.v1.flow.execute_intent` | `_execute_intent_direct()` |
| `run_nodus_script_via_flow()` | `sys.v1.nodus.execute` | `_run_nodus_via_flow_direct()` |
| `submit_async_job()` (external wrapper) | `sys.v1.job.submit` | routes to `AsyncJobService` |
| `execute_run()` (external wrapper) | `sys.v1.agent.execute` | routes to `agents.agent_runtime` |

`submit_async_job()` and `execute_run()` already routed through `ExecutionDispatcher` internally; the new syscalls wrap them externally for quota tracking and observability on non-Nodus callers.

### Adding a new entry point

1. Put real logic in `_my_service_direct()`.
2. Register as `sys.v1.myservice.action` with a handler that calls `_direct()`.
3. Make the public function a proxy (user_id check → SyscallContext → dispatch).

---

## 11. Nodus Integration

Nodus scripts call syscalls via the `sys()` global:

```python
result = sys("sys.v1.memory.read", {"query": "authentication flow"})
if result["status"] == "success":
    nodes = result["data"]["nodes"]
```

The `sys` global is injected at runtime by `NodusInterpreter`. The syscall context is derived from the current flow's execution context. Because context propagation is ContextVar-based, nested `sys()` calls inside a Nodus script automatically share the script's root `trace_id`.

---

## 12. Trace Propagation

### Problem

Nested syscall calls previously generated a fresh `trace_id` and `execution_unit_id` on every dispatch, producing fragmented observability graphs and broken RippleTrace lineage. A single agent run touching `flow.run → memory.read → event.emit` appeared as three unrelated execution units.

### Mechanism

`dispatch()` resolves trace context before doing any work via two module-level `ContextVar`s:

```python
_TRACE_ID_CTX: ContextVar[str]  # default ""
_EU_ID_CTX:    ContextVar[str]  # default ""
```

**Root call** (ContextVars empty — first dispatch in a thread/task):

1. Use `context.trace_id` and `context.execution_unit_id`; generate UUIDs if either is empty.
2. Set both ContextVars.
3. Execute handler.
4. **Reset** both ContextVars in `finally` — the slot is clean for the next root call.

**Nested call** (ContextVars already set — dispatch called from within a handler):

1. Override the incoming context's `trace_id` / `eu_id` with the ContextVar values.
2. Execute handler using the inherited IDs.
3. Return — no ContextVar tokens to reset.

The result: every syscall in a chain — however deep — shares one `trace_id` and one `execution_unit_id`, giving a single coherent execution unit in RippleTrace and `AgentEvent` logs.

### ContextVar scope

Python's `ContextVar` is thread-local and asyncio-task-local:

- **Sync handlers / threads**: each thread starts with a copy of the context from its spawning thread; `set()`/`reset()` are scoped to that thread.
- **Async handlers**: each `asyncio.Task` runs in its own `Context` copy; propagation is automatic within a task, isolated between tasks.
- **Thread pool (VM execution)**: the Nodus VM runs in a daemon thread seeded with the calling thread's `Context`, so ContextVar values are inherited — `sys()` calls inside a Nodus script propagate the root trace correctly.

### `child_context()` — explicit propagation

For handlers that need to dispatch a nested syscall with a **different capability set**, use `child_context()` to build an explicitly forwarded context:

```python
from kernel.syscall_dispatcher import child_context, get_dispatcher

def _handle_complex_flow(payload, context):
    # Inherit trace/eu; narrow capabilities for the sub-call
    ctx = child_context(context, capabilities=["memory.read"])
    result = get_dispatcher().dispatch("sys.v1.memory.read", {"query": "..."}, ctx)
    ...
```

`child_context()` always copies `trace_id` and `execution_unit_id` from the parent. It is optional — the ContextVar mechanism works transparently even without it. Use `child_context()` for **documentation clarity** or **capability narrowing**.

### Before / after

**Before** (broken — each nested call got a fresh trace):

```
dispatch("sys.v1.flow.run",      ctx_A)  → trace_id="trace-abc", eu="eu-1"
  └─ handler calls dispatch("sys.v1.memory.read", fresh_ctx)
        → trace_id="trace-xyz"  ← DIFFERENT — lineage broken
```

**After** (fixed — entire chain shares root trace):

```
dispatch("sys.v1.flow.run",      ctx_A)  → trace_id="trace-abc", eu="eu-1"
  └─ handler calls dispatch("sys.v1.memory.read", fresh_ctx)
        → trace_id="trace-abc"  ← INHERITED via ContextVar
```

---

## 13. Introspection API

```
GET /platform/syscalls?version=v1
```

Auth: JWT or Platform API key.

Response:
```json
{
    "versions": ["v1", "v2"],
    "syscalls": {
        "v1": {
            "memory.read": {
                "name": "sys.v1.memory.read",
                "capability": "memory.read",
                "description": "...",
                "stable": true,
                "deprecated": false,
                "input_schema": {...},
                "output_schema": {...}
            }
        }
    },
    "total_count": <number of syscalls returned>
}
```

---

## 14. Key Files

| File | Role |
|------|------|
| `kernel/syscall_versioning.py` | `SyscallSpec`, `parse_syscall_name`, `validate_payload`, `resolve_version`, `ABI_VERSIONS` |
| `kernel/syscall_registry.py` | `SyscallEntry`, `VersionedSyscallRegistry`, `SYSCALL_REGISTRY`, `register_syscall`, `DEFAULT_NODUS_CAPABILITIES`, kernel-owned helper syscall handlers |
| `kernel/syscall_dispatcher.py` | `SyscallDispatcher`, `get_dispatcher`, `SyscallContext`, `child_context`, `_TRACE_ID_CTX`, `_EU_ID_CTX`, context builder helpers |
| `kernel/syscall_handlers.py` | Domain-handler compatibility facade; `register_all_domain_handlers()` |
| `routes/platform_router.py` | `GET /platform/syscalls` introspection endpoint; `POST /platform/syscall` dispatch |
| `tests/unit/test_syscall_contract.py`, `tests/unit/test_syscall_dispatch_contract.py`, `tests/api/test_platform_syscall_contract.py` | The real syscall suites. *Corrected 2026-08-13:* this table previously claimed `test_syscall_versioning.py` (64 tests) and `test_syscall_dispatcher.py`; neither has ever existed. |
