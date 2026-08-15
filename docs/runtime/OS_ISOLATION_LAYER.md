---
title: "OS Isolation Layer"
last_verified: "2026-08-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# OS Isolation Layer

The OS Isolation Layer provides tenant isolation, resource quota enforcement, and priority-based execution scheduling for A.I.N.D.Y. execution units (AgentRuns, flow runs). It sits between the syscall dispatcher and raw handler execution.

> **Verified against source 2026-08-13** (DOCS-STALE-1). The **architecture** is intact: the
> layering, the non-fatal integration pattern, the WAIT/RESUME *mechanics*, the distributed
> broadcast and the FlowRun atomic claim all hold as described. **Almost everything a reader
> would copy out of it did not.**
>
> - **One quota column does not exist.** `cpu_time_ms` is **`wall_time_ms`**, and it measures
>   wall time, not CPU time. §§3, 5 and 7 all named it wrongly.
> - **`TenantContext` has different fields** — three of the four documented attributes are not on
>   the dataclass, and the two that matter most for isolation were missing.
> - **`priority` is a string, not `1–10`.** §§3 and 5 said integer; §4's own table said
>   `"high" | "normal" | "low"`. The document contradicted itself, and §4 was right.
> - **The "Cross-Instance Limitation" is obsolete** — the Redis-backed counters it calls
>   *"required"* have shipped, so its warning now *understates* the guarantee. See §3.
> - **`sys.v1.event.wait` is not a registered syscall** (§4). WAIT is a Nodus builtin.
> - **`AINDY_REDIS_URL` is not read by anything** (§9). The variable is `REDIS_URL`.
> - **§7's response shape and auth are both wrong** — none of the six documented keys is real.
>
> - **`record_usage`'s example key was silently dropped** (§3) — `"cpu_time_ms"` is not read, so
>   the copied call recorded zero.
>
> The last three are the ones that cost real time: each fails *silently* into a plausible
> default rather than erroring.

---

## 1. Overview

```
SyscallDispatcher.dispatch()
    │
    ├─ tenant isolation (user_id check)   — syscall_dispatcher.py:398
    ├─ quota check → ResourceManager.check_quota()   — :418
    │
    ▼
Handler executes
    │
    ├─ usage record → ResourceManager.record_usage()   — :655
    └─ ...
```

*The step numbers this diagram used (5, 6, 11) no longer match the dispatcher's own inline
comments, which now number a different sequence — line references are used instead, verified
2026-08-13.*

The OS layer is **non-fatal by design** — all `ResourceManager` and `SchedulerEngine` calls are wrapped in `try/except`. A broken quota system never kills a real execution.

---

## 2. TenantContext

Defined in `kernel/tenant_context.py`.

```python
@dataclass
class TenantContext:
    """Immutable tenant isolation context."""
    tenant_id:        str        # the tenant's unique identifier (== user_id)
    user_id:          str        # authenticated user ID within the tenant
    namespace:        str        # canonical prefix: "tenant:{tenant_id}"
    capability_scope: list       # explicit list of granted capabilities
```

> **Corrected 2026-08-13 — three of the four documented fields were wrong.** The old text listed
> `quota_group`, `priority` and `metadata`. None is an attribute of `TenantContext`; `quota_group`
> and `priority` are columns on `ExecutionUnit` (§5), not on this dataclass. What the doc omitted
> is more important than what it got wrong: **`capability_scope` and `namespace`** are the fields
> that make this a *isolation* primitive rather than a quota tag.

Every execution unit carries a `TenantContext`. It is resolved from `SyscallContext.user_id` at dispatch time.

---

## 3. ResourceManager

Manages per-execution-unit quotas and usage tracking.

```python
from kernel.resource_manager import get_resource_manager

rm = get_resource_manager()

# Check if an execution unit is within quota
ok, reason = rm.check_quota(execution_unit_id)
# ok=False → reason is a human-readable string returned in the error envelope

# Record actual usage after handler completes
rm.record_usage(execution_unit_id, {
    "syscall_count": 1,
    "wall_time_ms": 42,      # NOT "cpu_time_ms" — see below
    "memory_bytes": 0,
})
```

> **Corrected 2026-08-13, and this example was actively harmful.** It passed `"cpu_time_ms"`.
> `record_usage` reads `usage.get("wall_time_ms", 0)`, so the unrecognised key is **silently
> dropped and zero is recorded** — no error, no warning, just a quota counter that never moves.
> Deltas are added, except `memory_bytes`, which is a high-water mark.

### Quota Fields (on ExecutionUnit model)

| Column | Type | Description |
|--------|------|-------------|
| `tenant_id` | `String(128)`, nullable, indexed | Owning tenant |
| **`wall_time_ms`** | `Integer`, default 0 | Accumulated **wall** time |
| `memory_bytes` | `BigInteger`, default 0 | Peak memory usage |
| `syscall_count` | `Integer`, default 0 | Total syscalls dispatched |
| **`priority`** | **`String(16)`**, default `"normal"` | `"low"` \| `"normal"` \| `"high"` |
| `quota_group` | `String(64)`, nullable | Quota tier (e.g. `"default"`, `"premium"`) |

Composite index `ix_eu_tenant_priority` on `(tenant_id, priority)`.

> **Two corrections, 2026-08-13.**
>
> **There is no `cpu_time_ms` column.** It is **`wall_time_ms`**, and the distinction is real, not
> cosmetic: the ceiling constant is `MAX_WALL_TIME_MS`, sourced from `AINDY_QUOTA_CPU_MS` — an env
> var name the source explicitly keeps *"for operator compatibility"*. A long-running syscall that
> is mostly blocked on I/O consumes the quota just as fast as one that is CPU-bound.
>
> **`priority` is a string, not an integer.** Both this table and §5 said "1–10", while §4's
> Priority Levels table said `"high"` / `"normal"` / `"low"`. §4 was right — `ScheduledItem`
> validates `priority` against `PRIORITY_ORDER` and raises `ValueError` on anything else.

### Quota Ceilings

All four are env-tunable and were undocumented here:

| Constant | Env var | Default |
|---|---|---|
| `MAX_WALL_TIME_MS` | `AINDY_QUOTA_CPU_MS` | `300_000` (5 min) |
| `MAX_MEMORY_BYTES` | `AINDY_QUOTA_MEMORY_BYTES` | `268_435_456` (256 MB) |
| `MAX_SYSCALLS_PER_EXECUTION` | `AINDY_QUOTA_MAX_SYSCALLS` | `100` |
| `MAX_CONCURRENT_PER_TENANT` | `AINDY_QUOTA_MAX_CONCURRENT` | `5` |

See TECH_DEBT `SYSMAX-3` (memory bytes not OS-enforced) and `SYSMAX-4` (syscall and wall-time
caps are advisory).

### Quota Enforcement

When `check_quota()` returns `(False, reason)`, the dispatcher returns an error envelope immediately — no handler runs. This prevents runaway executions from consuming unbounded resources.

### Cross-Instance Behaviour

> **Rewritten 2026-08-13 — the gap this section described has been closed.** It said
> *"`can_execute()` / `mark_started()` / `mark_completed()` require Redis-backed atomic
> counters … treat `MAX_CONCURRENT_PER_TENANT` as a per-instance limit."* Those counters exist.
> An operator following the old advice would over-provision against a limit that is already
> global.

`ResourceManager` selects a backend at first use (`_get_backend()`):

- **`REDIS_URL` set and `TEST_MODE` off → `RedisResourceBackend`.** Per-tenant concurrency is a
  shared Redis counter (`increment_tenant_active` / `decrement_tenant_active`), so
  `MAX_CONCURRENT_PER_TENANT` is enforced **globally across instances**. Two Lua scripts keep it
  honest under concurrency: a decrement that floors at zero rather than going negative, and a
  set-if-greater for the peak-memory watermark. Tenant keys carry a TTL so a crashed instance
  cannot leak a permanently-held slot.
- **Otherwise → in-process only.** The original limitation text applies: each instance allows up
  to `MAX_CONCURRENT_PER_TENANT` independently.

There is no separate feature flag — **the presence of `REDIS_URL` is the switch**, and
`rm.is_redis_mode()` reports which path is live. Note the selection is cached in a module-level
singleton after the first call, so setting `REDIS_URL` after boot has no effect.

Wall time and memory quota are per-execution and correctly reflect usage within the process that
runs the execution, on either backend.

---

## 4. SchedulerEngine

Handles priority-based scheduling and WAIT/RESUME flow control.

```python
from kernel.scheduler_engine import get_scheduler_engine, ScheduledItem

se = get_scheduler_engine()

# Queue an execution unit
item = ScheduledItem(
    execution_unit_id="eu-abc",
    tenant_id="user-123",
    priority="normal",
    run_callback=lambda: runner.resume(run_id),
    run_id="run-uuid",
)
se.enqueue(item)
se.schedule()  # drain up to MAX_PER_SCHEDULE_CYCLE items

# Register a WAIT — flow engine calls this internally
se.register_wait(run_id, wait_for_event="task.completed", ...)

# Signal a waiting run to resume (via distributed path)
from kernel.event_bus import publish_event
publish_event("task.completed", correlation_id="chain-abc")
```

### WAIT / RESUME Pattern

Nodus flows can pause execution and wait for an external signal:

1. Flow node calls the Nodus **builtin** `event.wait("approval.granted")`.

   > **Corrected 2026-08-13: `sys.v1.event.wait` is not a registered syscall.** Only
   > `sys.v1.event.emit` exists in `SYSCALL_REGISTRY`. WAIT is a Nodus builtin
   > (`AINDY/runtime/nodus_builtins.py`) that raises `WorkerWaitSignal` out of the worker; the
   > flow engine catches it and suspends. This is the same error already corrected in
   > `docs/tutorials/02-event-driven-automation.md` under DOCS-BUCKET-A-1 — it survived here.
2. `SchedulerEngine.register_wait(run_id, ...)` stores the callback in `_waiting`.
3. When the event fires, `publish_event(event_type)` is called.
4. `notify_event()` matches `_waiting` entries, deletes them under lock, and re-enqueues callbacks.
5. On resume, `PersistentFlowRunner.resume()` claims the FlowRun atomically before executing.

This enables human-in-the-loop and async integration patterns without blocking threads.

### Priority Levels

| Constant | Value |
|----------|-------|
| `PRIORITY_HIGH` | `"high"` |
| `PRIORITY_NORMAL` | `"normal"` |
| `PRIORITY_LOW` | `"low"` |

Round-robin fairness within each priority level prevents any single tenant from starving others.

---

## 5. ExecutionUnit Columns

> **Duplicate of §3, and it carried the same two errors** (`cpu_time_ms`, integer `priority`).
> Corrected 2026-08-13 by pointing at the single table rather than maintaining two.

See **§3 → Quota Fields** for the authoritative column list, types and defaults. In short:
`tenant_id`, **`wall_time_ms`**, `memory_bytes`, `syscall_count`, **`priority`** (a
`String(16)`, not an integer), `quota_group`.

---

## 6. Tenant Isolation Enforcement

The dispatcher enforces tenant isolation at Step 5:

```python
if not context.user_id:
    return error_envelope("TENANT_VIOLATION: syscall requires authenticated tenant context")
```

This ensures:
- No anonymous executions can reach any handler.
- Every syscall is attributable to a specific tenant.
- Cross-tenant data access is structurally impossible within the dispatcher path.

Route-level isolation (user_id scoping on queries) is enforced separately in DAO methods and API handlers.

---

## 7. OS Layer API

```
GET /platform/tenants/{tenant_id}/usage
```

Served by `AINDY/routes/platform/platform_ops_router.py:99` — **not** `routes/platform_router.py`.

**Auth: JWT only, and self-only.** There is no `enforce_api_key_scope` on this route, so the
`memory.read` scope the old text claimed is not required — and not sufficient either. The handler
compares the caller's `sub` against the path `tenant_id` and raises **403 `TENANT_VIOLATION`** on
mismatch, so an operator cannot read another tenant's usage through it. Rate limited **60/minute**.

Returns `ResourceManager.get_tenant_summary(tenant_id)` with the scheduler's `stats()` grafted on:

```json
{
    "tenant_id": "user-abc",
    "active_executions": 1,
    "execution_count": 12,
    "total_wall_time_ms": 1240,
    "peak_memory_bytes": 0,
    "total_syscalls": 42,
    "quota_limits": {
        "max_wall_time_ms": 300000,
        "max_memory_bytes": 268435456,
        "max_syscalls_per_execution": 100,
        "max_concurrent_executions": 5
    },
    "scheduler": { "...": "SchedulerEngine.stats()" }
}
```

> **Corrected 2026-08-13 — none of the six keys in the old example was right.** It showed
> `quota_group`, `syscall_count`, `cpu_time_ms`, `memory_bytes` and `priority`; the real payload
> aggregates **across all of a tenant's execution units** (`total_*`, `peak_*`) rather than
> reporting one active EU, and adds the `quota_limits` block and `scheduler` stats.

---

## 8. Non-Fatal Integration Pattern

All OS layer calls in the dispatcher use this pattern:

```python
try:
    rm = _get_rm()
    quota_ok, quota_reason = rm.check_quota(context.execution_unit_id)
    if not quota_ok:
        return self._error_envelope(name, context, quota_reason, ...)
except Exception as _rm_exc:
    logger.debug("[SyscallDispatcher] resource quota check skipped: %s", _rm_exc)
```

The `try/except` ensures that if `ResourceManager` is unavailable (e.g., test environment without OS layer tables), execution continues rather than failing. Quota enforcement is a best-effort guarantee, not a hard blocker in degraded environments.

---

## 9. Distributed Event Bus

All resume events go through a single public API function that guarantees distributed delivery:

```python
from kernel.event_bus import publish_event

# Emit to all instances — the only correct way to fire a resume event
publish_event("task.completed", correlation_id="chain-abc")
```

**Execution path:**
1. `publish_event()` calls `SchedulerEngine.notify_event(broadcast=True)`.
2. Local `_waiting` scan runs immediately; matched callbacks are enqueued.
3. Event is published to a Redis pub/sub channel (`aindy:scheduler_events`).
4. All other instances receive the broadcast and call `notify_event(broadcast=False)` on their local scheduler.
5. `broadcast=False` suppresses re-publication, preventing infinite loops.

**Duplicate execution prevention:**
- `_waiting` entries are deleted under lock before any enqueue (within-instance guard).
- `PersistentFlowRunner.resume()` claims the `FlowRun` atomically: `UPDATE WHERE status='waiting'`. Only the winner proceeds; all others get `rowcount=0` and return `SKIPPED`.

**Fault tolerance:**
- Redis unavailable → local delivery only (no exception propagates).
- Subscriber thread reconnects with exponential backoff (1 s → 30 s cap).
- `AINDY_EVENT_BUS_ENABLED=false` disables the bus entirely for single-instance deployments.

**Configuration:**

| Variable | Default | Description |
|----------|---------|-------------|
| **`REDIS_URL`** | `redis://localhost:6379/0` | Redis connection URL. *Corrected 2026-08-13: this row said `AINDY_REDIS_URL`, which nothing reads — an operator setting it would silently get the localhost fallback.* The same variable selects `ResourceManager`'s Redis backend (§3) |
| `AINDY_EVENT_BUS_CHANNEL` | `aindy:scheduler_events` | Pub/sub channel name |
| `AINDY_EVENT_BUS_ENABLED` | `true` | Set to `false` for local-only mode |

## 10. FlowRun Execution Guarantee

The FlowRun claim is the single gatekeeper for execution ordering across all instances:

```
Event fires on any instance
  → publish_event(event_type)
  → notify_event() wakes matching _waiting callbacks on this instance
  → Redis broadcast wakes _waiting callbacks on all other instances
  → All instances race to claim: UPDATE flow_runs SET status='executing' WHERE status='waiting'
  → Winner (rowcount=1): EU resume → flow execution
  → Losers (rowcount=0): immediate return — no side effects
```

Callback ordering within the winning instance:

1. **FlowRun atomic claim** — `UPDATE WHERE status='waiting'`
2. **EU status transition** — `waiting → resumed → executing` (only if claim won)
3. **Flow execution** — `PersistentFlowRunner.resume()` (only if claim won)

The EU callback registered by `rehydrate_waiting_eus()` includes an ownership guard: if the FlowRun is no longer `"waiting"` when it fires, the EU callback skips — avoiding bookkeeping side effects on the losing instance.

## 11. Key Files

| File | Role |
|------|------|
| `kernel/tenant_context.py` | `TenantContext`, core OS layer primitives |
| `kernel/resource_manager.py` | `ResourceManager`, quota check + usage recording |
| `kernel/scheduler/` | **The real implementation** — 9 modules (`engine`, `waits`, `dispatch`, `cross_instance`, `persistence`, `recovery`, `common`, `core`). `ScheduledItem` and the `PRIORITY_*` constants live in `scheduler/common.py` |
| `kernel/scheduler_engine.py` | A one-line shim: `from AINDY.kernel.scheduler import *`. Kept as the import path; not where the code is |
| `kernel/event_bus.py` | Redis pub/sub distributed event bus; `publish_event()` public API |
| `kernel/syscall_dispatcher.py` | OS layer integration points (Steps 5, 6, 11) |
| `core/flow_run_rehydration.py` | Startup rehydration of FlowRun WAIT callbacks |
| `core/wait_rehydration.py` | Startup rehydration of EU WAIT callbacks |
| `AINDY/routes/platform/platform_ops_router.py` | `GET /platform/tenants/{id}/usage` (`:99`). *Corrected 2026-08-13: this row said `routes/platform_router.py`.* |
| *(none)* | *Corrected 2026-08-13:* this table claimed `tests/unit/test_os_layer.py` and `tests/unit/test_event_bus.py` (26 tests). Neither has ever existed. The only event-bus unit test is `tests/unit/test_event_bus_redis_url.py`, which covers URL parsing alone. |
