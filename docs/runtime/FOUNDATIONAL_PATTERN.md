---
title: "Foundational Pattern: The Infinity Algorithm"
api_version: "1.0"
last_verified: "2026-05-31"
status: current
owner: "platform-team"
---

# Foundational Pattern: The Infinity Algorithm

## What This Document Is

The Infinity Algorithm is the foundational design principle behind A.I.N.D.Y. and
the broader Infinity Algorithm ecosystem. This document records its presence in
the runtime architecture — not as philosophy, but as a structural fact audited
against the codebase.

The runtime does not declare this pattern by name anywhere in code. It exists
as an invariant relationship across distributed components. This document is
the single anchor that names it, maps it, and explains design choices that
would otherwise appear arbitrary.

---

## The Algorithm

The Infinity Algorithm is an execution model that describes how a system
transitions between states over time:

```
S(t+1) = R(C(T(S(t), I(t))))
```

Where:

| Symbol | Responsibility | Plain meaning |
|---|---|---|
| `S(t)` | Current state | The full runtime state at this moment |
| `I(t)` | Inputs | Everything that enters the system at time t |
| `T` | Transformation | What processes those inputs against state |
| `C` | Constraint | What prevents invalid state transitions |
| `R` | Recurrence | What causes the cycle to run again |
| `S(t+1)` | Next state | The new runtime state after one cycle completes |

The algorithm is not a loop in the traditional sense. It is a composition of
responsibilities. State never stays static — it is always being evaluated,
transformed, constrained, and fed back.

---

## The Runtime Mapping

The runtime implements all six responsibilities. None are missing. They are
distributed across components by design, not coincidence.

### Input — `I(t)`

Everything that initiates a cycle:

- HTTP requests through the FastAPI route layer
- Events received by the `EventBus` Redis pub/sub subscriber
- Flows and agents in `pending` or `approved` state entering the scheduler
- Memory retrieved by `MemoryOrchestrator.get_context()` — past execution
  outcomes injected as context for the current cycle
- Time-based and event-based triggers in the `SchedulerEngine` wait registry
- Jobs dequeued from the `DistributedQueue`
- External signals through `watcher_service` and `external_call_service`

Memory is an input. That is not incidental. Past execution state, ranked
and filtered by the memory scoring system, enters every agent execution
as `similar_past_outcomes`, `relevant_failures`, and `successful_patterns`.
The system selects its own inputs from its own history.

### Transformation — `T`

Everything that processes inputs and changes state:

- `SyscallDispatcher.dispatch()` — the single entry point for all capability
  calls. Routes every action through a 10-step validation and execution gate
  before reaching any handler.
- `ExecutionPipeline.run()` — middleware that manages `ExecutionUnit`
  lifecycle, claims and releases resources, captures memory signals, and emits
  lifecycle events.
- `PersistentFlowRunner` — DAG executor that walks the flow node graph,
  advancing the `FlowRun` state machine node by node.
- `NodusExecutionService` — compiles agent objectives into Nodus execution
  contexts, runs scripts through the Nodus language runtime, collects deferred
  memory writes.
- `AgentCoordinator.decide_execution_mode()` — routes agent runs between local
  execution, delegation to another agent, or collaborative multi-agent dispatch.
- `MemoryOrchestrator` — hybrid retrieval combining vector similarity, tag
  filtering, and MAS path queries, ranked by the memory scoring formula.

Transformation is not centralized. It is applied by whichever path is
appropriate: route → pipeline → dispatcher → handler; agent run → Nodus
execution; flow run → DAG executor → dispatcher. All paths share
`SyscallDispatcher` as the common gate.

### Constraint — `C`

Everything that prevents invalid state transitions:

- **Capability enforcement**: Every syscall dispatch checks that the caller's
  `SyscallContext.capabilities` includes the required capability. Hard deny.
- **Tenant isolation**: Every dispatch requires a `user_id`. Extension calls
  must match the context tenant. Hard deny on mismatch.
- **Resource quota**: `ResourceManager` enforces per-tenant concurrency
  (≤ 5 simultaneous executions), per-EU wall-clock time (≤ 300 s / 5 min,
  configurable via `AINDY_QUOTA_CPU_MS`; note: the field is named `cpu_time_ms`
  but measures monotonic wall-clock time including I/O wait), and syscall count
  (≤ 100). Fails closed in production when Redis is unavailable.
- **Idempotency gate**: `EXACTLY_ONCE` handlers check `EffectRecord` before
  executing. Cached results are returned for completed actions. Live concurrent
  calls on the same action_id degrade gracefully to `AT_LEAST_ONCE`.
- **Schema validation**: Input and output are validated against registered ABI
  schemas at dispatch time. Stable syscalls fail hard on output mismatch.
- **Circuit breaker**: `CircuitBreaker` protects LLM API calls (OpenAI,
  DeepSeek) with a three-failure threshold and 60-second recovery window.
  `OPEN` state rejects all calls until the probe succeeds.
- **Retry policy**: Type-specific retry limits with exponential backoff —
  flow nodes (3 attempts), agent low/medium risk (3 attempts), agent high
  risk (1 attempt, `EXACTLY_ONCE`), async jobs (1 attempt), Nodus scheduled
  (3 attempts). Non-retryable errors (401, 403, 404, permission) short-circuit.
- **State machine guards**: `FlowRun` and `AgentRun` use atomic DB claims
  (`UPDATE WHERE status=X`) as the single gatekeeper for state transitions.
  Multiple instances competing for the same transition: exactly one wins.

Constraints are enforced at every syscall dispatch, not once at entry.
The system re-validates with every capability call within a single execution.

### Recurrence — `R`

Everything that causes the cycle to run again:

**Clock-driven** (APScheduler, started at server startup):

- `scheduler_heartbeat_tick` fires every **1 second** and calls
  `SchedulerEngine.schedule()`, which dequeues items from the priority lanes
  (high → normal → low, round-robin per tenant) and dispatches their callbacks.
  This is the primary recurrence clock.
- Twelve additional jobs handle maintenance recurrence: embedding backfill
  (1 min), deferred job retry (1 min), delayed job promotion (30s), timed-out
  wait expiry (60s and 5 min), stuck-run recovery (5 min), queue health
  (60s), stale log cleanup (1 hr), effect record TTL cleanup (24 hr).

**Event-driven** (immediate, latency-bound by Redis RTT):

- `EventBus` subscriber loop receives Redis pub/sub messages from any instance
  and calls `SchedulerEngine.notify_event()`, which re-enqueues all waiting
  runs that match the event type and correlation_id.
- `ResourceManager.mark_completed()` publishes `resource_available` when a
  tenant's concurrency count drops from at-limit to below-limit, immediately
  re-enqueuing any runs waiting on that event.
- `SchedulerEngine.tick_time_waits()` fires time-based wait entries whose
  `trigger_at` has elapsed.

**Process-loop-driven** (WorkerLoop):

- `WorkerLoop._single_thread_loop()` perpetually dequeues from the
  `DistributedQueue` with a 5-second idle timeout. Runs in a separate OS
  process. Multiple concurrency threads configurable.

**Recovery-driven** (startup and periodic):

- On server startup, `rehydrate_waiting_flow_runs()` reconstructs
  `PersistentFlowRunner` callbacks for every `FlowRun.status=waiting` row
  and re-registers them with the `SchedulerEngine`. A restart does not lose
  waiting flows.
- `WorkerLoop._run_stale_recovery()` re-enqueues jobs whose workers crashed
  before acknowledgment, every 60 seconds.

**Memory-driven** (per-execution):

- `MemoryFeedbackEngine` and `MemoryLearningEngine` update memory node scores
  after every execution. Updated scores change retrieval ranking. Changed
  ranking changes which memories enter the next execution as input.
  This is recurrence through the feedback layer.

Recurrence operates simultaneously through all five of these cycles. The 1-second
clock, the event bus, the worker loop, the recovery sweep, and the memory update
are independent and do not coordinate. Together they ensure the runtime never
stops evaluating state.

### Output — `O`

Everything that leaves the runtime as observable or persisted state:

- Syscall response envelope: `{status, data, trace_id, execution_unit_id,
  syscall, version, duration_ms, error, warning}`
- `SystemEvent` records in PostgreSQL: lifecycle events (execution.started,
  execution.waiting, execution.completed, execution.failed), syscall_executed,
  memory_write, watchdog.scan.completed
- Memory nodes in PostgreSQL with pgvector embeddings
- `AgentRun` and `FlowRun` result payloads and terminal status
- Prometheus metrics: execution counts and durations, active executions,
  scheduler queue depths, circuit breaker state, quota Redis mode
- OpenTelemetry spans: one per syscall dispatch
- Redis pub/sub broadcast of events to all instances
- `JobLog` audit records

### Feedback — `F`

Everything that makes completed execution influence future execution:

The memory scoring formula is the primary feedback mechanism. It determines
retrieval ranking, and retrieval ranking determines which past outcomes enter
the next cycle as input:

```
score = (
    impact_score × 0.35
    + recency    × 0.20
    + frequency  × 0.15
    + signal_frequency × 0.20
    + type_weight × 0.10
) × type_weight
```

Where:
- `impact_score` is computed at write time from downstream effect count and
  causal depth in the event graph, plus a failure bonus
- `recency` decays exponentially with a 21-day half-life
- `frequency` is a logarithmic function of `usage_count`
- `type_weight` is `failure=1.25`, `outcome=1.0`, `decision=0.95`, `insight=0.85`

After every execution:

- `MemoryFeedbackEngine` increments `usage_count`, `success_count`, and
  `failure_count` on every memory node that was retrieved for that execution.
- `MemoryLearningEngine` recomputes a running weighted `success_rate` and
  flags nodes with score < 0.3 as `low_value`.
- `MemoryCaptureEngine` evaluates the execution event for significance,
  deduplicates, classifies the node type, enriches tags, computes causal
  context, writes a new memory node if the significance threshold is met,
  and auto-links it to related existing nodes.

The loop closes in `ExecutionLoop.run_with_context()`, which orchestrates
the complete cycle in sequence: recall → execute → write → feedback → metrics.

`CircuitBreaker` and `RetryPolicy` are also feedback mechanisms: accumulated
failure counts determine whether LLM calls are accepted and whether retries
are attempted. `EffectRecord` prevents re-execution of actions whose outcomes
are already known.

---

## Why the Distribution Is Intentional

A naive implementation of this pattern would centralize it: one object that
holds state, applies transformations, checks constraints, schedules recurrence,
and reads feedback. The runtime does not do that, and the reason is load-bearing.

**Constraint enforcement must be universal.** If constraints lived in a central
loop object, any code path that bypassed the loop would bypass the constraints.
By placing constraint enforcement inside `SyscallDispatcher.dispatch()` — the
only legal entry point for any capability call — constraints are enforced
regardless of which transformation path invoked the call.

**Recurrence must survive failure.** A single recurrence clock that lives in
process memory is lost on crash. The runtime runs recurrence through four
independent clocks (APScheduler, EventBus, WorkerLoop, startup rehydration)
precisely so that a crash of any one does not stop the cycle. Startup
rehydration reconstructs the `SchedulerEngine._waiting` dict from the database,
not from the previous process's memory.

**Feedback must be asynchronous.** Memory writes, embedding generation, and
score updates cannot block the execution that generated them. The feedback
mechanisms commit to the database and queue embeddings for background
processing so that the execution path returns to the caller without waiting
for the full feedback cycle to complete.

**The kernel must be stateless.** `SyscallDispatcher` carries no execution
state — all state lives in handlers, the database, Redis, and ContextVars.
This allows multiple instances to share the same dispatcher behavior while
operating against shared Redis and PostgreSQL state.

---

## Design Choices Explained by the Pattern

Several runtime choices that would otherwise appear arbitrary follow directly
from the pattern:

**Why does `mark_completed()` publish an event?**
When a tenant's concurrency count drops below the limit, waiting flows need to
be re-enqueued immediately. Publishing `resource_available` through the EventBus
is how the constraint layer (quota enforcement) feeds back into the recurrence
layer (scheduler re-enqueue) without coupling `ResourceManager` to
`SchedulerEngine` directly.

**Why is the heartbeat 1 second?**
The scheduler dequeue is how all pending work — flow runs, agent continuations,
event-driven resumptions — gets dispatched. A 1-second cadence means the maximum
latency between a flow being enqueued and its execution beginning is bounded by
the tick interval. This is recurrence, not polling.

**Why does memory retrieval influence agent execution context?**
Past outcomes are inputs to the next transformation. The memory scoring formula
encodes the feedback law: frequently used, high-impact, recent, failure-type
memories rank highest. Agent plans are seeded with what the system has learned
about similar situations. This is `I(t)` being shaped by prior `S(t-n)`.

**Why does startup rehydrate waiting flows from the database?**
`FlowRun.status=waiting` rows persist across crashes. The `SchedulerEngine._waiting`
dict does not. Rehydration bridges the gap — it reconstructs the in-memory
recurrence registry from durable state so the cycle can continue after restart.

**Why is `EXACTLY_ONCE` enforced at dispatch time and not at the handler?**
Because the constraint must be universal. Any handler, from any execution path,
dispatched through any transformation component, must pass the same idempotency
gate. Placing it inside `SyscallDispatcher.dispatch()` means it cannot be
bypassed by any legitimate code path.

---

## What This Document Is Not

This document does not describe a specific feature, API, or implementation
detail. It describes the invariant structural relationship between the
runtime's major systems. Individual components will be refactored, renamed,
and extended. This relationship should remain stable.

It is not a specification for how to build new features. New features should
be built to fit the existing layers. This document explains why those layers
are shaped the way they are.

It is not a philosophical statement. It is an architectural fact, audited
against the codebase on 2026-05-31, with evidence cited throughout.
