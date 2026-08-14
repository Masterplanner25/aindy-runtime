---
title: "Execution Contract"
last_verified: "2026-08-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Execution Contract

## Purpose

> ## Read this first — verified against source 2026-08-13
>
> **This is a design target written before the repo split, and most of what it describes is not
> in this repository.** It was imported wholesale by `0d5d382 Initial runtime repo extraction`
> (2026-05-17) with a `last_verified` of 2026-05-02 — a date that predates this repo's existence.
> It is kept because the five-stage shape is still the intended model and the *aspiration* is a
> useful record. It is **not** a description of the runtime as built.
>
> Concretely, checked against source:
>
> | Claim | Reality |
> |---|---|
> | `ExecutionRequest`, `ExecutionRunner`, `ExecutionRecord`, `ExecutionOrchestrator` | **None exist**, under those names or any other. The built equivalents are `ExecutionPipeline` (`core/execution_pipeline/pipeline.py`) and `ExecutionUnit` (the DB row). |
> | `EXECUTION_ACCEPTED`, `EXECUTION_PERSISTED`, `EXECUTION_ORCHESTRATED` | **Not emitted.** `SystemEventTypes` defines `execution.started`, `execution.completed`, `execution.failed`, `execution.waiting`, `execution.step.completed` — and that enum is frozen-hash tested, so it is the authority. |
> | Task, Genesis, ARM subsystems | **Not in this repo.** `AINDY/domain/` and `AINDY/modules/` do not exist; `domain.task_services`, `domain.genesis_ai`, `domain.masterplan_factory`, `modules.deepseek.*` are all `aindy-apps-monolith`. |
> | *"Compile-time enforcement now exists via `tools/execution_contract_linter.py`, `.github/workflows/lint.yml`, `.pre-commit-config.yaml`"* | **None of the three exists here.** See the Status note. |
> | The `/apps/agent` ownership paragraph | **Inverted.** See §Agent. |
> | *"successful register returns a usable JWT immediately"* | **Reversed by 2.0.0.** See §Identity Boot Activation. |
>
> What *is* real and load-bearing: the `Input -> Execution -> Persist -> Orchestrator ->
> Observability` shape; `core/execution_helper.py::execute_with_pipeline`; the outbound
> `external.call.*` event sequence; and the two Related Contracts at the foot of this file.
>
> For what the runtime actually guarantees today, read `EXECUTION_INVARIANTS.md` — that document
> is source-verified and is the one to trust on conflict.

---

This document defines the single canonical execution contract for all user-driven and system-driven execution in A.I.N.Y.D.

It replaces the current fragmented execution patterns across:

- Agent
- Task
- Memory
- Genesis
- Watcher
- ARM

The required shape is:

`Input -> Execution -> Persist -> Orchestrator -> Observability`

`Observability` includes durable `SystemEvent` emission for both internal execution lifecycle and outbound external interactions.

Anything outside that shape is legacy behavior and should be treated as non-canonical.

## Current Entry Points

### Agent

- `POST /apps/agent/run`
- `POST /apps/agent/runs/{run_id}/approve`
- `POST /apps/agent/runs/{run_id}/replay`
- Runtime: `agents.agent_runtime`
- Canonical executor entrypoint: `runtime.nodus_execution_service.execute_agent_run_via_nodus()`
- Compatibility wrapper: `runtime.nodus_adapter.NodusAgentAdapter.execute_with_flow()`

Current behavior:

- Closest subsystem to a canonical contract
- Has explicit run records, approval, step persistence, lifecycle events, and canonical runtime entry through `nodus_execution_service`
> **Corrected 2026-08-13 — these three lines had ownership exactly backwards.**
>
> `/apps/agent/*` is **not** runtime-owned. `APP_ROUTERS` in `AINDY/routes/__init__.py` contains
> only `memory_router` and `coordination_router`; that file's own comment records that
> `agent_router` **moved to the plugin layer**. `AINDY/routes/agent_router.py` still exists, but
> its module docstring opens *"Deprecated: agent HTTP surface — now plugin-owned … retained for
> reference only. Do not import the router from here."*
>
> And it is `apps/agent/routes/agent_router.py` in `aindy-apps-monolith` — called here "only a
> transitional compatibility re-export" — that is **the canonical implementation**, registered
> via `register_router()` at bootstrap.
>
> A bare runtime does not serve `/apps/agent/*` at all. The routes answer on any plugin-loaded
> deployment, which is why the error stayed invisible. Same correction as
> `PUBLIC_RUNTIME_SURFACES.md` (2026-08-06).

- The agent *runtime* — `agents/agent_runtime/`, `runtime/nodus_execution_service.py` — is
  runtime-owned; the HTTP surface in front of it is not
- The canonical executor entrypoint above (`execute_agent_run_via_nodus()`) is real and correct

### Task

- `POST /apps/tasks/create`
- `POST /apps/tasks/start`
- `POST /apps/tasks/pause`
- `POST /apps/tasks/complete`
- Runtime: `domain.task_services`

Current behavior:

- Route now enters a shared route-layer execution pipeline, but the underlying task domain work is still a direct service mutation path
- Persistence happens first
- Memory capture, social sync, ETA, and Infinity orchestration are follow-on side effects
- No first-class execution envelope or execution event record

### Memory

- `POST /apps/memory/execute`
- `POST /apps/memory/nodus/execute`
- `POST /apps/memory/recall`
- `POST /apps/memory/recall/v3`
- Runtime: shared route-boundary execution wrapper plus canonical Nodus runtime/result helpers for memory execution surfaces

Current behavior:

- Has recall, execution, writeback, metrics, and optional trace
- Route entry now goes through the shared route-layer execution pipeline for memory APIs
- `/memory/execute` is the active memory execution path
- `/memory/execute/complete` is deprecated compatibility surface and not the canonical pattern
- `/memory/nodus/execute` still has a route-specific outer envelope, but it now reuses the canonical Nodus runtime/result helpers and shared execution metadata while remaining restricted by source validation, allowed-operation registration, and optional scoped capability tokens for write operations

### Genesis

- `POST /apps/genesis/message`
- `POST /apps/genesis/synthesize`
- `POST /apps/genesis/lock`
- `POST /apps/genesis/{plan_id}/activate`
- Runtime: `domain.genesis_ai`, `domain.masterplan_factory`

Current behavior:

- Main work is done directly in route/service code
- Flow engine is only mirrored opportunistically for observability during message handling
- Lock and activate perform side effects after the primary write

### Watcher

- `POST /apps/watcher/signals`
- Runtime: `routes.watcher_router.receive_signals`

Current behavior:

- Ingest path is persist-first batch storage
- ETA and Infinity updates are fire-and-forget follow-ons
- No explicit execution envelope, no durable outcome record beyond stored signals

### ARM

- `POST /apps/arm/analyze`
- `POST /apps/arm/generate`
- Runtime: `modules.deepseek.deepseek_code_analyzer.DeepSeekCodeAnalyzer`

Current behavior:

- Route entry now goes through the shared route-layer execution pipeline, but analyzer execution remains domain-direct after the route boundary
- Domain persistence exists in `analysis_results` and `code_generations`
- No explicit shared orchestration contract around analyze/generate calls

## Canonical Pipeline

Every execution, regardless of domain, must obey this sequence:

### 1. Input

The system accepts a typed execution request with:

- `execution_id`
- `execution_type`
- `user_id`
- `trigger`
- `payload`
- `requested_by`
- `created_at`

Minimum contract:

```text
ExecutionRequest {
  execution_id
  execution_type
  user_id
  trigger
  payload
  requested_by
  correlation_id
}
```

Rules:

- Input must be validated before work starts
- Input must resolve to one domain execution type
- Every request must have a traceable owner or explicit system actor

### 2. Execution

Execution must happen through one runtime abstraction:

`ExecutionRunner.run(request) -> ExecutionResult`

The runner may delegate to a domain-specific executor, but only after the execution record exists.

Allowed domain executors:

- `agent`
- `task`
- `memory`
- `genesis`
- `watcher`
- `arm`

Rules:

- No domain executes directly from a route without going through the execution contract
- Execution must return structured output, not implicit success
- Execution must emit a terminal result: `success`, `failed`, `waiting`, or `rejected`

### 3. Persist

Before orchestration, the system must durably persist:

- execution record
- status
- normalized domain output
- error payload if failed
- timestamps

Minimum persisted shape:

```text
ExecutionRecord {
  execution_id
  execution_type
  user_id
  status
  input_payload
  output_payload
  error_payload
  started_at
  completed_at
  correlation_id
}
```

Rules:

- Persistence is not optional
- Domain tables are not enough by themselves
- A domain write without an execution record is not canonical execution

### 4. Orchestrator

After persistence, the orchestrator runs exactly once per execution result.

Responsibilities:

- score recalculation
- loop adjustment generation
- next action production
- secondary domain updates
- policy checks for follow-up work

Canonical interface:

`ExecutionOrchestrator.after_execution(record) -> OrchestrationResult`

Minimum orchestration result:

```text
OrchestrationResult {
  execution_id
  score_snapshot
  adjustment
  next_action
}
```

Rules:

- Orchestration is not best-effort glue
- If orchestration fails, the execution is not complete
- Every successful execution must produce a persisted orchestration outcome
- Every orchestrated execution must produce either:
  - `next_action`, or
  - explicit terminal marker `no_next_action_required`

### 5. Observability

Observability must be written last, but it must represent the entire lifecycle.

Required observability outputs:

- execution lifecycle event
- timing
- status transition history
- domain-specific metadata
- orchestration result reference
- outbound external-call lifecycle when execution touches third-party systems

Canonical event sequence:

```text
EXECUTION_ACCEPTED       ← aspirational; not defined in SystemEventTypes
EXECUTION_STARTED        ← real: "execution.started"
EXECUTION_PERSISTED      ← aspirational; not defined
EXECUTION_ORCHESTRATED   ← aspirational; not defined
EXECUTION_COMPLETED      ← real: "execution.completed"
```

*Verified 2026-08-13.* `AINDY/core/system_event_types.py` defines five execution events:
`execution.started`, `execution.completed`, `execution.failed`, `execution.waiting`,
`execution.step.completed`. Two of the five names above exist; three do not.

The enum is covered by a **frozen-hash baseline** (`tests/baselines/system_event_contract.json`),
so adding any of the missing three is a contract change requiring the baseline to be regenerated
in lockstep — not a free edit.

Note also `execution.waiting`, absent from both sequences below despite Invariant 5 listing
`waiting` as a terminal state. WAIT/RESUME is a first-class runtime path, not an omission in the
implementation.

Failure sequence:

```text
EXECUTION_ACCEPTED
EXECUTION_STARTED
EXECUTION_FAILED
```

Required outbound event sequence:

```text
external.call.started
external.call.completed
```

*Verified real 2026-08-13* — emitted by `platform_layer/external_call_service.py`
(`:79`, `:101`, `:122`), the FR-1 outbound boundary. This is the one part of the observability
section that is fully implemented.

Required outbound failure sequence:

```text
external.call.started
external.call.failed
error.external_call
```

Rules:

- No silent execution
- No untracked state mutation
- No route should return success before the lifecycle is observable
- No external interaction is allowed to occur without required outbound lifecycle events
- Failure to persist required external-call events is execution-fatal for that interaction

## Text Diagram

```text
Client or System Trigger
  -> ExecutionRequest validation
  -> ExecutionRecord created (status=accepted)
  -> Domain Executor runs
  -> Domain Result persisted to execution record
  -> ExecutionOrchestrator runs
  -> LoopAdjustment / next_action persisted
  -> Lifecycle events + metrics emitted
  -> Response returns execution_id, status, output, next_action
```

## Required Invariants

### Invariant 1: No silent execution

Every execution must create a durable execution record before domain work starts.

Disallowed:

- route directly calling service logic and returning output
- fire-and-forget domain actions without a persisted execution envelope

### Invariant 2: No side-effect-only flows

A flow is invalid if it only mutates side systems and has no canonical result object.

Disallowed:

- memory capture only
- score update only
- logging only
- watcher-triggered recalculation without an execution result

### Invariant 3: All execution produces traceable output

Every execution must return and persist:

- status
- domain output or error
- orchestration result
- next action or explicit terminal marker
- required outbound event metadata for third-party calls when present

### Invariant 4: Orchestrator is mandatory

Execution is incomplete until orchestration runs.

Disallowed:

- domain success with no score snapshot
- domain success with no loop output
- domain success with only optional best-effort orchestration

### Invariant 5: One terminal state per execution

An execution must end in one of:

- `success`
- `failed`
- `waiting`
- `rejected`

No other terminal semantics should exist in domain-specific code.

### Invariant 6: Domain persistence is separate from execution persistence

Writing `Task`, `AgentRun`, `AnalysisResult`, `GenesisSessionDB`, or `WatcherSignal` is not sufficient.

There must also be a canonical execution record.

### Invariant 7: External interactions are first-class execution facts

Any OpenAI, HTTP, watcher delivery, or other outbound third-party call triggered by execution must emit:

- `external.call.started`
- `external.call.completed` or `external.call.failed`
- `error.external_call` on failure

Minimum outbound metadata:

- `service_name`
- `endpoint`
- `model` when applicable
- `method`
- `status`
- `latency_ms`
- `error` when applicable

## Domain Examples

### Agent

Canonical path:

```text
POST /apps/agent/run
  -> ExecutionRequest(type=agent)
  -> persist execution envelope
  -> generate plan / approval gate / execute flow
  -> persist AgentRun + steps + execution record
  -> orchestrator computes score + loop adjustment
  -> observability emits lifecycle and step events
```

Expected output:

- run id
- final status
- plan
- result
- next action

### Task

Canonical path:

```text
POST /apps/tasks/complete
  -> ExecutionRequest(type=task.complete)
  -> persist execution envelope
  -> complete task
  -> persist Task mutation + execution result
  -> orchestrator computes score + loop adjustment
  -> observability emits completion event
```

Current gap:

- task completion writes task state first and treats orchestrator, memory, ETA, and social sync as side effects

### Memory

Canonical path:

```text
POST /apps/memory/execute
  -> ExecutionRequest(type=memory.workflow)
  -> persist execution envelope
  -> recall context + execute + write memory + feedback
  -> persist execution result and trace id
  -> orchestrator computes score + loop adjustment
  -> observability emits execution + trace events
```

Current gap:

- `/apps/memory/nodus/execute` is still a separate executor surface rather than a first-class flow/orchestrator path

### Genesis

Canonical path:

```text
POST /apps/genesis/message
  -> ExecutionRequest(type=genesis.message)
  -> persist execution envelope
  -> call genesis model + update session
  -> persist session state + execution result
  -> orchestrator computes score + loop adjustment
  -> observability emits lifecycle event
```

Current gap:

- synthesis and audit flows still remain direct domain executions, even though their outbound model calls are now durably evented

### Watcher

Canonical path:

```text
POST /apps/watcher/signals
  -> ExecutionRequest(type=watcher.ingest)
  -> persist execution envelope
  -> store batch + summarize ingest outcome
  -> orchestrator computes score + loop adjustment if trigger conditions met
  -> observability emits ingest metrics
```

Current gap:

- persistence exists for raw signals, but not for canonical execution lifecycle
- outbound signal delivery is evented, but watcher ingest itself is still not represented by a first-class execution record

### ARM

Canonical path:

```text
POST /apps/arm/analyze
  -> ExecutionRequest(type=arm.analyze)
  -> persist execution envelope
  -> run analysis
  -> persist AnalysisResult + execution result
  -> orchestrator computes score + loop adjustment
  -> observability emits timing and outcome
```

Current gap:

- analyze/generate are domain executions without a shared execution envelope, though outbound model calls are now durably evented

## Canonical Response Shape

Every execution endpoint should eventually converge on:

```text
{
  "execution_id": "...",
  "execution_type": "...",
  "status": "success|failed|waiting|rejected",
  "domain_result": {...},
  "orchestration": {
    "score_snapshot": {...},
    "adjustment": {...},
    "next_action": {...}
  },
  "observability": {
    "correlation_id": "...",
    "trace_id": "...",
    "event_count": 0
  }
}
```

## Migration Guidance

To move the codebase onto this contract:

1. Introduce a first-class `ExecutionRecord` model.
2. Make all execution routes create that record before domain work starts.
3. Move `InfinityOrchestrator.execute()` behind a generic `ExecutionOrchestrator`.
4. Replace best-effort side effects with ordered orchestration steps.
5. Remove operational reliance on `/memory/execute/complete` and keep `/memory/execute` as the sole canonical memory execution path.
6. Make routes return canonical execution payloads instead of domain-only payloads.

## Non-Canonical Paths To Eliminate

These patterns should be considered invalid over time:

- route -> service -> commit -> explicitly logged and observable side effects
- domain execution with no execution envelope
- observability-only flow engine mirrors
- memory loop completion as a separate manual API step
- watcher-triggered orchestration without a canonical execution record
- third-party calls that do work without `SystemEvent` coverage

## Bottom Line

The canonical system model is not "domain-specific route logic plus optional extras."

It is:

`Input -> Execution -> Persist -> Orchestrator -> Observability`

If a path does not satisfy all five stages, it is legacy and should be refactored until it does.

Status note:
> **Corrected 2026-08-13.** `core/execution_pipeline.py` is a **package**, not a module —
> `core/execution_pipeline/pipeline.py`. More importantly, **none of the three enforcement
> artefacts named below exists in this repository**: no `tools/execution_contract_linter.py`,
> no `.github/workflows/lint.yml`, no `.pre-commit-config.yaml`. Linting here is `Runtime Lint`
> (ruff) in `runtime-ci.yml`, which enforces style, not this contract.
>
> So the sentence *"Compile-time enforcement now exists"* is false for `aindy-runtime`. If the
> linter exists at all it is app-side; nothing in this repo checks that routes enter through
> `execute_with_pipeline`. The four bullets below describe the monolith at the time of writing.

- Route-level normalization improved materially after introduction of `core/execution_pipeline/` and `core/execution_helper.py` (`execute_with_pipeline` — real, still the entry helper).
- That change unifies request-scoped trace creation, best-effort lifecycle event emission, and response passthrough on several legacy route groups without yet introducing a single persisted `ExecutionRecord` model.
- Compile-time enforcement now exists via `tools/execution_contract_linter.py`, plus `.github/workflows/lint.yml` and `.pre-commit-config.yaml`.
- The linter currently enforces direct route entry through `execute_with_pipeline(...)` / `execute_with_pipeline_sync(...)` and flags direct memory/event execution patterns outside the pipeline.
- The repo is not yet fully clean under that rule set; the linter is the enforcement mechanism and the current violation list is a migration backlog, not proof of full convergence.

## Identity Boot Activation

> **Corrected 2026-08-13 — this whole section describes an app surface, and its central auth
> claim was reversed by the 2.0.0 breaking change.**
>
> `GET /apps/identity/boot` is not served by the runtime, and neither `identity.boot` nor
> `identity.created` is a `SystemEventTypes` value — there are zero references to either in
> `AINDY/`. The runtime's equivalent is `bootIdentity` against the platform version/boot-mode
> surface (see `UI_CONTRACT.md`), which is a different thing.
>
> The claim below that *"successful register returns a usable JWT immediately; no second auth
> call is required"* is **the exact behaviour 2.0.0 removed**. `POST /auth/register` is now
> `status_code=202` and its docstring reads *"Returns 202 with no token, identically for a new
> and an already-registered address"* — deliberate, to close the enumeration oracle. A client
> written against the paragraph below will break at upgrade; that is the documented breaking
> change, not a defect.

Authentication is not the whole activation path anymore.

After `POST /auth/login` returns a JWT, the frontend immediately calls:

`GET /apps/identity/boot`

This boot path is now the canonical identity activation contract:

`Auth -> Identity Boot -> Hydrated State -> User Execution`

Signup now uses the same activation path:

`Register -> Seed Identity State -> JWT Issued -> Identity Boot -> User Execution`

On successful `POST /auth/register`, the backend seeds the first system anchor:

- `User`
- initial `Memory` node
- initialized `Execution` placeholder
- baseline `Metrics`
- required lifecycle `SystemEvent`

Current implementation guarantees:

- JWT remains the auth gate; boot does not bypass auth.
- successful register returns a usable JWT immediately; no second auth call is required
- boot returns a DB-backed user execution snapshot:
  - recent Memory Bridge nodes
  - recent AgentRun rows
  - current score metrics
  - active FlowRun rows
  - derived `system_state`
- returned memory is tagged with `context = "identity_boot"` for explicit boot provenance
- boot emits required `SystemEvent(type="identity.boot")`
- signup initialization emits required `SystemEvent(type="identity.created")`
- Infinity orchestration now injects loop context derived from the same boot primitives:
  - `user_id`
  - recent memory
  - current metrics

Implication:

- the frontend should hydrate from identity boot first, then refresh domain panels opportunistically
- blank post-login dashboards are non-canonical behavior

---

## Related Contracts

- **Idempotency Contract** (`docs/runtime/IDEMPOTENCY_CONTRACT.md`) — defines how
  `EXACTLY_ONCE` syscalls are deduplicated via the `EffectRecord` table. The gate sits
  inside `SyscallDispatcher._dispatch()` and depends on the `ExecutionUnit` record that
  this contract requires to exist before work begins.
- **Retry Policy** (`docs/runtime/RETRY_POLICY.md`) — defines retry semantics and the
  `is_retryable_error()` classifier. The idempotency gate and retry policy interact:
  retries of `EXACTLY_ONCE` syscalls return the cached result rather than re-executing.


