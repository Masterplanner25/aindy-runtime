---
title: "Kernel Capability Audit"
api_version: "1.0"
last_verified: "2026-06-07"
status: current
owner: "platform-team"
---

# Kernel Capability Audit

Derived from a full code inspection of `AINDY/kernel/`, `AINDY/core/execution_pipeline/`,
`AINDY/runtime/`, `AINDY/memory/`, `AINDY/agents/`, and `AINDY/platform_layer/` on
2026-06-07. Every claim is grounded in a file path or class name; no hype added.

---

## A. Thesis

**The A.I.N.D.Y. kernel enables trusted, observable, tenant-isolated execution of agentic
workloads — flows, scripts, and agents — through a versioned syscall ABI, a persistent
scheduler with cooperative WAIT/RESUME semantics, and an automatically-accumulating memory
layer, so that applications can be built on top without reimplementing execution
infrastructure.**

---

## B. Kernel Capability Map

| Linux kernel concept | A.I.N.D.Y. equivalent | Evidence |
|---|---|---|
| System call table | `SYSCALL_REGISTRY` (`VersionedSyscallRegistry`) | `kernel/syscall_registry.py:950` — 17 built-in `sys.v{N}.domain.action` entries |
| Kernel entry point / trap gate | `SyscallDispatcher.dispatch()` | `kernel/syscall_dispatcher.py:258` — 10-step dispatch pipeline; never raises except `SyscallContractViolation` |
| Process context / PCB | `ExecutionUnit` + `SyscallContext` | `kernel/syscall_registry.py:54`, `db/models/execution_unit.py` |
| Process scheduler | `SchedulerEngine` (3-tier priority queue) | `kernel/scheduler/core.py`, `engine.py` — high/normal/low lanes, round-robin per tenant |
| IPC / inter-process events | `EventBus` + Redis pub/sub | `kernel/event_bus.py:104` — cross-instance WAIT/RESUME propagation |
| Memory management / address space | `MemoryAddressSpace` (MAS) | `memory/memory_address_space.py:9` — `/memory/{tenant}/{ns}/{type}/{id}` hierarchy |
| Resource limits / cgroups | `ResourceManager` | `kernel/resource_manager.py:253` — wall time, syscall count, tenant concurrency enforced via Redis |
| Process isolation / namespaces | `TenantContext` | `kernel/tenant_context.py:45` — frozen dataclass, memory path assertion, cross-tenant guard |
| Signal / interrupt mechanism | `ExecutionWaitSignal`, `publish_event()` | `core/execution_gate.py`, `kernel/event_bus.py:516` — cooperative WAIT raised by handlers, caught by `ExecutionPipeline` |
| Device driver ABI / module loading | Platform extension registry | `platform_layer/registry.py:93–234` — 40+ registration hooks, three trust tiers |
| Fault protection / page fault handler | `CircuitBreaker` | `kernel/circuit_breaker.py:22` — CLOSED/OPEN/HALF_OPEN for OpenAI and DeepSeek |
| Kernel audit log | `SystemEvent` table | `core/system_event_types.py` — 40+ typed events from execution to memory to autonomy decisions |
| Sandbox / seccomp | `NodusRuntimeAdapter` + `sandbox_runner.py` | `runtime/nodus_security.py:75` blocks import/exec/eval; `platform_layer/sandbox_runner.py:20` — 3-tier container assurance classes |
| Capability model (capabilities(7)) | `SyscallContext.capabilities` + scoped tokens | `kernel/syscall_dispatcher.py:411` — per-dispatch capability gate; `agents/capability_service.py` — mint/validate scoped tokens |
| Atomic operations / idempotency | `EffectRecord` + `EXACTLY_ONCE` gate | `kernel/syscall_dispatcher.py:486–538` — SHA256 input hash, pending/success/failed lifecycle |

---

## C. Confirmed Capabilities

### C1 — Versioned syscall ABI

17 built-in syscalls across v1 and v2 (`kernel/syscall_registry.py:954–1244`):

- `sys.v1.memory.{read,write,search,list,tree,trace}`
- `sys.v1.flow.{run,execute_intent}`
- `sys.v1.event.emit`
- `sys.v1.nodus.execute`
- `sys.v1.job.submit`
- `sys.v1.agent.{execute,count_runs,list_recent_durations,list_recent_runs,ensure_initial_run}`
- `sys.v2.memory.read` (structured field filters)

Every call returns the same envelope:
`{status, data, trace_id, execution_unit_id, syscall, version, duration_ms, error, warning}`.
ABI version fallback is implemented in `kernel/syscall_versioning.py`.

### C2 — Capability enforcement

Every `dispatch()` validates `entry.capability` against `context.capabilities` before
executing the handler (`syscall_dispatcher.py:411–419`). Capability denial returns an error
envelope — never raises. Tenant isolation (`user_id` required) runs on every call.
Per-tenant concurrency quota blocks over-limit executions fail-closed in production;
fails-open only in dev/test (`_quota_backend_failure_may_fail_open()`).

### C3 — Persistent scheduler with WAIT/RESUME

`SchedulerEngine` holds three in-memory priority queues. `register_wait()` stores a resume
callback keyed by `run_id` + event_type, with Redis backup via `RedisWaitRegistry`
(`kernel/scheduler/waits.py:10`). `publish_event()` → `EventBus.publish()` broadcasts to all
instances via Redis pub/sub; each instance calls local `notify_event(broadcast=False)`.
DB-level claim (`UPDATE WHERE status='waiting'`) ensures exactly-one-instance execution.
Startup rehydrates all `waiting` FlowRuns so no flow is lost across restarts.

### C4 — Distributed event bus

`EventBus` wraps Redis pub/sub with graceful degradation: Redis unavailable → local-only
behaviour preserved, `publish()` returns `False` without propagating exceptions
(`kernel/event_bus.py:193`). Pre-rehydration buffer holds up to 1000 events fired before
`_waiting` is populated.

### C5 — Tenant memory with MAS

Memory nodes live in PostgreSQL with `Vector(1536)` via pgvector. Path structure
`/memory/{tenant}/{namespace}/{type}/{id}` enforces tenant isolation at the query layer.
Queries support exact paths, one-level wildcards (`/*`), and recursive (`/**`) patterns.
`sys.v1.memory.trace` follows the causal chain. Background embedding pipeline +
`memory_ingest_worker` handles async vectorisation.

### C6 — Automatic memory capture

`memory/memory_capture_engine.py` (v5 loop) captures significant events automatically — no
manual memory calls required in flows. Significance scoring, deduplication, type
classification, and auto-linking happen without application code involvement. Significance
rules are app-layer configurable via the extension registry.

### C7 — Idempotency gate (EXACTLY_ONCE)

`EffectRecord` table holds SHA256(payload) hashes. Duplicate calls with the same
scope+action return cached results without re-executing. Stale pending records (>15 min)
are reset in-band for retry-after-crash (`STALE_PENDING_THRESHOLD_SECONDS = 900`).
Concurrent racing calls degrade gracefully to AT_LEAST_ONCE with a logged warning.

### C8 — Full observability

40+ `SystemEventTypes` emitted on every lifecycle transition. OTel spans wrap every
`dispatch()` call (`kernel/syscall_dispatcher.py:543–588`). Prometheus metrics:
`aindy_active_executions_total`, `execution_duration_seconds`, `execution_total`,
`ai_circuit_breaker_state`, `quota_redis_mode`. `/health/deep` checks syscall_registry,
scheduler, event bus, and domain health.

### C9 — Extension ABI (plugin system)

Registry offers 40 registration hooks: routers, syscalls, flows, jobs, event handlers,
scheduled jobs, agent tools, planner backends, trigger evaluators, capability definitions,
memory policies, startup hooks, and more (`platform_layer/registry.py:93–234`). Three
trust tiers: `OWNER_RUNTIME_BUILTIN`, `OWNER_FIRST_PARTY_APP`, `OWNER_EXTERNAL_THIRD_PARTY`.
Each bootstrap module has an audit record tracking capabilities used/denied.
`extension_abi.py` defines versioned ABI surfaces for manifest, webhook, flow, agent-tool,
and planner-backend registration.

### C10 — Nodus script execution

`NodusRuntimeAdapter` runs `.nodus`/`.nd` scripts with injected `DeferredMemoryBuiltins`.
`nodus_security.py:75` validates script source before execution: blocks `import`,
`from ... import`, `__import__`, `eval`, `exec`. Memory writes are deferred and committed
atomically after the script finishes. `WorkerWaitSignal` propagates WAIT semantics back
to the flow engine.

### C11 — Multi-agent coordination

`agents/agent_coordinator.py` — `decide_execution_mode()` routes to local vs delegated
execution. Each agent run requires: (a) `approved` status, (b) a scoped capability token
with allowed capabilities explicitly enumerated. `stuck_run_watchdog.py` detects orphaned
runs. `AutonomousController` evaluates triggers and records decisions.

### C12 — Async job pipeline

`sys.v1.job.submit` → `submit_async_job()` — task queue with `max_attempts`, source
label, per-user and global concurrency caps. `AutomationLog` provides an audit trail.
APScheduler maintenance jobs run cleanup of stale logs and expired EffectRecords.

### C13 — Circuit breaker for AI providers

`CircuitBreaker` (CLOSED → OPEN → HALF_OPEN) wraps OpenAI and DeepSeek calls
(`kernel/circuit_breaker.py:22`). On `CircuitOpenError`, `SyscallDispatcher` maps to
`HTTP_503:...` in the error envelope. Prometheus metric `ai_circuit_breaker_state`
tracks state by provider name.

---

## D. Emerging / Partial Capabilities

### D1 — Strong sandbox (aspirational)

`sandbox_runner.py` defines three assurance classes including `strong_sandbox_vm` (VM-grade
isolation with `STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES`), but on Windows/macOS the
strongest available class is `container-grade`. The `RUNNER_STRONG_SANDBOX_VM` path
requires Linux and specific host configuration. Verification is currently worker-self-report,
not kernel-observable.

### D2 — Autonomy / trigger evaluation

`autonomous_controller.py` has `evaluate_trigger()`, `record_decision()`, and
`_autonomy_decision_model()`, but `AutonomyDecision` is a symbol registered by the monolith
plugin (`get_symbol("AutonomyDecision")`). In standalone runtime mode it falls back to a
stub. The trigger evaluation framework exists; the decision model is app-layer dependent.

### D3 — Flow strategy selection (learned strategies)

`sys.v1.flow.execute_intent` implies strategy selection, and `_flow_strategies` is
registered via the extension registry. The `OPER-DEFER-001` debt note records that
`/platform/flows/strategies` is not yet served.

### D4 — Cross-instance per-EU quota

`ResourceManager` has a Redis backend for shared tenant concurrency counters, but per-EU
wall-time and syscall-count tracking is process-local by design (`resource_manager.py:57`).
An EU that migrated between instances could escape its wall-time budget.

### D5 — Native Rust memory scorer

`runtime/memory/native_scorer.py` references a Rust scorer compiled via Maturin as an
optional performance path. The Python fallback runs when the native binary is absent. Not
part of the standard build.

### D6 — Deterministic replay

`REPLAY-1` in TECH_DEBT.md: `Clock` injection into ~12 `datetime.now()` call sites is
deferred. Deterministic replay of a specific execution from the stored event log is not
currently possible.

---

## E. Missing Kernel Capabilities

### E1 — Billing and metering

`BILLING-1` through `BILLING-5` are open and deferred. No kernel-level metering of
syscall cost, LLM token consumption, or plan enforcement exists. The resource manager
tracks counts but has no cost model attached.

### E2 — Distributed per-EU resource enforcement

Wall-time and syscall-count limits are enforced per-process. `SYSMAX-3` notes memory
bytes are not enforceable without OS integration.

### E3 — Credential / secret management

No kernel-level secrets store. LLM API keys come from environment variables. No rotation,
scoped key injection per tenant, or audit trail for secret access.

### E4 — Network egress control

Nodus scripts are blocked from `socket`/`requests`/`urllib` via source inspection. Agent
tools carry `egress_scope` fields but enforcement is advisory, not kernel-enforced. No
kernel-level network policy exists.

### E5 — Per-tenant LLM rate limiting

The circuit breaker handles provider failures, not per-tenant rate budgets. There is no
kernel-level LLM call rate limiting per tenant per minute.

### E6 — Memory garbage collection

No TTL-based or significance-threshold eviction of `memory_nodes` is enforced by the
kernel. Old nodes accumulate unless the application layer explicitly removes them.

---

## F. Boundary Risks

### F1 — App-layer model in kernel-adjacent code

`autonomous_controller.py:_autonomy_decision_model()` calls `get_symbol("AutonomyDecision")`
— a symbol registered by the monolith plugin. Persistence behavior of the autonomous
controller changes based on what the app layer registers.

### F2 — Syscall handlers reach into runtime directly

`_handle_agent_execute` imports `execute_run`; `_handle_flow_run` imports
`PersistentFlowRunner`; `_handle_nodus_execute` imports `_run_nodus_via_flow_direct`. These
are lazy imports (correct pattern), but the kernel's syscall table directly orchestrates
runtime-layer concerns. Handlers containing business logic about approval states belong
above the kernel.

### F3 — ExecutionPipeline bridges HTTP and kernel

`core/execution_pipeline/pipeline.py` wraps all route handlers, carrying lifecycle
management, memory capture, and event emission simultaneously. It is not a kernel module
but is called by all routes. If the DB session is corrupt mid-pipeline, events fail
silently behind the `_emission_failed` guard.

### F4 — Memory capture behavior is undefined without a policy

`memory_capture_engine.py` calls `get_memory_policy()` and `get_memory_significance_rule()`
from the extension registry. In a multi-tenant SaaS context this is correct, but it means
the kernel's auto-capture behavior is undefined without a registered policy.

### F5 — Agent state mutated as side effect of execute

`execute_run()` calls `register_or_update_agent()` (DB write) before deciding execution
mode. This is lifecycle state mutation happening as a side effect of an "execution" call,
not as an explicit state transition through the kernel.

### F6 — Subprocess callback CWD hazard

`runtime_callback_host.py` spawns subprocess workers with `cwd` pointing to the package
root — in Docker this resolves to a read-only site-packages directory. Third-party
extension callbacks have a restricted execution environment that differs from in-process
first-party code. Documented and mitigated in `AINDY/config.py`; see CLAUDE.md.

---

## G. Strategic Interpretation

A.I.N.D.Y. is all five commonly-named categories, but in a specific composition:

- **Runtime** — `PersistentFlowRunner`, `SchedulerEngine`, `ExecutionPipeline`, and the
  Nodus VM form a complete multi-step execution runtime with persistence and recovery.
  This is the most complete layer.

- **Control plane** — `SyscallDispatcher`, `ResourceManager`, `TenantContext`,
  `CircuitBreaker`, and `EffectRecord` form a governance layer that all execution routes
  through. Nothing executes outside this envelope by design.

- **AI operating layer** — the MAS, automatic memory capture, embedding pipeline, causal
  trace, and vector recall constitute infrastructure that learns from execution. The kernel
  remembers what happened across runs without application code managing it explicitly.

- **Agent framework** — the agent approval flow, scoped capability tokens, multi-agent
  coordinator, tool registry, and Nodus security layer constitute a first-class agent
  hosting environment. Agents are one execution path on top of the runtime.

- **App framework** — the platform extension registry with 40 hooks, the SPA, and the
  admin bootstrap constitute an application hosting surface. This layer sits above the
  kernel, not within it.

**The correct reading, in order of architectural weight:**

> A.I.N.D.Y. is an AI-native operating layer: a runtime that provides kernel-level
> services (syscalls, scheduling, memory, isolation, observability) to agentic workloads,
> and an extension ABI through which domain applications register their flows, tools,
> agents, and UI without touching the kernel directly.

**For Masterplan Infinite Weave:** the kernel is strong enough to host complex multi-step
AI workflows today. The extension registry, syscall ABI, and Nodus execution path mean
that new capabilities can be added without modifying kernel code. The gaps in §E —
billing metering, distributed per-EU resource enforcement, and network egress policy —
are what need to exist before multi-tenant SaaS deployment is safe.

---

## H. Final Answer

**What does A.I.N.D.Y.'s kernel enable?**

The kernel enables trusted agentic execution at runtime scale. Specifically, today:

1. **Coding agents, research agents, customer-service agents** — `sys.v1.agent.execute` +
   tool registry + Nodus security + capability tokens. Approval gate, orchestration, and
   multi-agent delegation work today.

2. **Workflow automations** — `sys.v1.flow.run` + `PersistentFlowRunner` + WAIT/RESUME.
   DAG-based flows with persistent state, suspend/resume on events, and DB-backed crash
   recovery work today.

3. **Memory-native apps** — MAS, auto-capture, semantic search, and causal trace
   syscalls. Applications get a persistent operational knowledge graph without building
   their own memory layer.

4. **AI-native business systems** — execution events → automatic memory capture →
   scoring → recall → planner context injection. The system accumulates operational
   intelligence across executions and makes it available to subsequent agents.

5. **Multi-agent orchestration** — `AgentCoordinator.decide_execution_mode()`,
   `AutonomousController` trigger evaluation, and the distributed event bus. Primitives
   are present; the autonomy decision model requires the monolith plugin to be non-stub.

6. **Nodus-powered applications** — `sys.v1.nodus.execute`, Nodus VM, deferred memory
   builtins, and flow-backed orchestration. Scripts run inside the kernel's trust envelope
   with full syscall access and memory context injection.

What the kernel does **not yet** enable without additional work: multi-tenant SaaS with
billing enforcement, strong sandbox for hostile third-party code on non-Linux hosts, and
deterministic replay for debugging.
