---
title: "Agent Runtime"
last_verified: "2026-08-05"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Agent Runtime


This document describes the agent runtime subsystem in `AINDY/agents/`. It
covers the execution contract, public API surface, capability enforcement model,
recovery behavior, and runtime-owned orchestration guardrails. For the app-layer Agentics feature (gap analysis,
completion roadmap, Nodus integration plan) see
[docs/apps/AGENTICS.md](../apps/AGENTICS.md).

Repository ownership:

- this document belongs to `aindy-runtime`
- app-enrichment planning belongs in `docs/apps/AGENTICS.md`
- the broader documentation split map lives in
  [Runtime Docset Boundary](./RUNTIME_DOCSET_BOUNDARY.md)

The authoritative repo-split import boundary for app code lives in
[Runtime Public API Contract](./PUBLIC_API_CONTRACT.md). Treat that document as
the source of truth for which `AINDY.*` modules apps may import.

---

## 1. What the Agent Runtime Is

The agent runtime is a domain-agnostic execution subsystem in `AINDY/agents/`.
It owns:

- plan generation through a runtime-owned planner backend contract
- the approval trust gate
- per-run capability token minting
- orchestration guardrails for run creation, delegation, replay, and autonomous
  submission
- deterministic step execution via `PersistentFlowRunner`
- per-step retry with configurable high-risk no-retry policy
- run lifecycle persistence (`AgentRun`, `AgentStep`, `AgentEvent`)
- stuck-run recovery at startup
- replay from a prior run's plan

The runtime does not own domain logic. Tool implementations that call tasks,
memory, ARM, or the Infinity Loop live in `apps/` and are invoked through the
registered tool registry. The agent HTTP exposure is also runtime-owned now:
`AINDY/routes/agent_router.py` serves the `/apps/agent/*` surface while keeping
tool implementations app-owned behind registries.

Boundary note:

- agent execution semantics may be runtime-owned
- richer agent product behavior, app-domain logic, and presentation-level
  agent experience are not automatically runtime-owned merely because the
  runtime exposes part of the subsystem

Baseline runtime behavior is intentionally generic:
- generic planner prompt
- runtime-selected planner backend
- runtime-owned memory tools (`memory.recall`, `memory.write`)
- trigger evaluation with no domain assumptions
- no-op completion hook
- empty suggestion output unless a plugin registers a suggestion provider

The supported runtime-only deployment surface for that baseline is defined in
[Runtime-Only Deployment](./RUNTIME_ONLY_DEPLOYMENT.md).

Claim note:

- runtime-owned agent support should be interpreted through the current
  trusted-internal posture
- this doc does not imply a stronger third-party agent platform or extension
  posture than the governing security docs allow

App-enriched behavior is optional:
- KPI-aware planner prompt enrichment
- richer suggestion generation from analytics or persisted loop state
- post-run Infinity orchestration
- additional app-owned tools such as task, ARM, search, and masterplan actions

Current classification:
- baseline runtime contract: generic planner context, runtime memory tools, default trigger evaluator, empty suggestions, no-op completion hook
- optional plugin/app enrichment: KPI-aware planner context, suggestion providers, Infinity-style completion hooks, extra app-owned tools
- ambiguous and should be refactored: domain-agnostic memory-context prompt enrichment is currently bundled into the app planner extension; KPI suggestion heuristics are duplicated across provider and syscall paths; post-run analytics enrichment currently mutates generic run results through a broad completion-hook slot

---

## 2. Execution Lifecycle

```
POST /agent/run  (create)
│
├─ agent_runtime.create_agent_run()
│   └─ runtime-selected planner backend generates a structured plan
│
├─ POST /agent/run/{id}/approve  (trust gate)
│   └─ agent_runtime.approve_agent_run()
│       └─ capability tokens minted for this run
│
├─ agent_runtime.execute_agent_run()
│   ├─ NodusAgentAdapter wraps PersistentFlowRunner
│   ├─ per-step: check capability token, execute tool, persist AgentStep
│   ├─ per-step retry: transient failures retry; high-risk steps do not retry
│   └─ emit AgentEvent for each step outcome
│
└─ post-execution
    ├─ memory capture (memory_capture_engine)
    └─ optional plugin completion hook such as Infinity orchestration
```

A run that is rejected at the trust gate writes a `REJECTED` AgentRun record
and stops. It does not execute any steps.

---

## 2.1 Planner Backend Contract

The planner layer is runtime-owned and provider-agnostic.

Current contract:

- planner prompt and tool catalog come from runtime/plugin registries
- planner backend selection is resolved by the runtime, not hard-coded in the
  planner core
- the selected backend receives a normalized planner request containing the
  objective, run type, user id, composed system prompt, and injected tool
  catalog

Selection order:

1. `settings.AINDY_AGENT_PLANNER_BACKEND`
2. `planner_backend` returned by the planner-context provider
3. the runtime default backend name

Built-in backends:

- `runtime_local`
  - deterministic runtime-local backend
  - selects from the injected tool catalog without requiring any external
    provider configuration
  - this is the runtime default, including runtime-only deployments
- `openai_chat_compat`
  - compatibility adapter that preserves the previous external model behavior
    through the runtime-owned planner contract
- `disabled`
  - deterministic no-planning mode with an explicit failure reason

Configuration:

- `AINDY_AGENT_PLANNER_BACKEND`
- `AINDY_AGENT_PLANNER_MODEL`
- `AINDY_AGENT_PLANNER_TEMPERATURE`

Important boundary:

- the planner core no longer hard-codes vendor or model names
- external model dependency, when used, now lives in a backend adapter rather
  than in the core planning contract
- `AINDY_AGENT_PLANNER_MODEL` and `AINDY_AGENT_PLANNER_TEMPERATURE` apply only
  to provider-backed adapters such as `openai_chat_compat`

---

## 3. Public API Surface

*(Corrected 2026-08-05 — the table below previously used `*_agent_run` names that do not
exist on this surface.)*

There are **two** layers, and conflating them is the usual mistake:

**1. The execution package — `AINDY/agents/agent_runtime/`.** A package, not a module;
`AINDY/agents/agent_runtime.py` is a seven-line `__path__` shim so both import forms work.
Its `__all__` declares 15 public names:

| Function | Description |
|---|---|
| `create_run(...)` | Generate plan, persist an `AgentRun` in `pending_approval` |
| `approve_run(...)` | Validate plan, mint the capability token, CAS to `approved` |
| `reject_run(...)` | Terminate a run that was never approved |
| `execute_run(...)` | Execute the approved plan |
| `replay_run(...)` | Create a new run from a prior run's plan |
| `run_to_dict(run)` | Serialize an `AgentRun` for API responses |
| `get_run_events(...)` | Fetch the run's persisted event stream |
| `generate_plan(...)` | Planner entry point |
| `to_execution_response(...)` | Shape a run into the execution envelope |

Plus `chat_completion`, `emit_error_event`, `perform_external_call`, `LOCAL_AGENT_ID`,
`PLANNER_SYSTEM_PROMPT`, `logger`.

`run_to_dict` is the canonical serializer and **is** the public name — it is an alias of
`_run_to_dict` (`presentation.py:54`). Use `run_to_dict`; do not reach for the underscore
form.

**2. The route-facing API — `AINDY/agents/runtime_api.py`.** Keyword-only wrappers the HTTP
layer calls: `create_agent_run_runtime`, `approve_agent_run_runtime`,
`reject_agent_run_runtime`, `recover_agent_run_runtime`, `replay_agent_run_runtime`.
Recovery lives **only** here — the execution package exposes no recover function.

`run_to_dict` is the canonical serializer for `AgentRun` objects. It is used by
`AINDY/routes/agent_router.py` and `automation_flows.py`. Do not call `_run_to_dict` directly
— use `run_to_dict`.

---

## 4. Capability Enforcement

Each approved run receives a scoped `CapabilityToken` listing the tools it is
allowed to call. Enforcement happens at two points:

*(Corrected 2026-08-05: the three symbols this section previously named —
`validate_run_scope`, `check_tool_permission`, `CapabilityViolation` — do not exist.)*

1. **Plan-level** — `capability_service.get_plan_required_capabilities()` resolves what the
   plan needs; `validate_token()` / `check_execution_capability()` verify the run's token.
2. **Per tool call** — `AINDY/agents/tool_registry.py` `execute_tool` calls
   `capability_service.check_tool_capability(token, run_id, user_id, tool_name)` before the
   tool runs.

**The check returns a dict, it does not raise.** `check_tool_capability` yields
`{"ok": bool, "error": ...}`, and `execute_tool` converts a false `ok` into a failed tool
result. There is no `CapabilityViolation` exception class anywhere in the tree — code that
catches one is catching nothing.

The capability token is stored on the `AgentRun` record and does not change
after approval. Modifying the token post-approval is not permitted.

---

## 5. Per-Step Retry Policy

The runtime uses `AINDY/runtime/RETRY_POLICY.md` for all retry decisions.
The agent-specific rules are:

- **Transient failures** (network timeout, downstream 5xx): retry up to 3 times
  with exponential backoff.
- **High-risk steps** (tool metadata `high_risk: true`): no retry regardless of
  failure type. The step fails immediately and the run halts.
- **Capability violations**: no retry. Treated as a fatal configuration error.
- **Plan exhausted**: if all steps complete successfully the run transitions to
  `COMPLETED`.

Each step outcome is persisted as an `AgentStep` row before the retry decision
is made, so the full attempt history is always visible.

---

## 6. Recovery and Replay

### Startup recovery

`scan_and_recover_stuck_runs()` is called in `main.py lifespan()` after
`load_plugins()`. It queries for any `AgentRun` rows in `RUNNING` state and
calls `recover_agent_run()` on each. This handles server crashes during
execution.

A recovered run resumes from the last persisted `AgentStep` — it does not
re-execute completed steps.

### Manual recovery

`POST /agent/run/{id}/recover` calls `recover_agent_run()` directly. Returns
`409 Conflict` if the run is already in a terminal state.

### Replay

`POST /agent/run/{id}/replay` calls `replay_agent_run()`. This creates a new
`AgentRun` with status `PENDING` using the original run's plan verbatim. The
new run must go through the normal approve → execute path. The new run stores
`replayed_from_run_id` pointing to the source run.

### Runtime guardrails

The runtime enforces orchestration guardrails in infrastructure rather than
relying on route or UI behavior:

- trace-scoped run creation is bounded, and duplicate active objectives on the
  same trace are rejected
- delegation chains have a maximum depth
- each parent run has a maximum child-run fan-out
- delegation to an agent already present in the ancestor chain is rejected as a
  loop
- replay chains have a maximum depth
- autonomous async submissions with the same runtime submission key are
  suppressed while an active duplicate is already queued or running

These checks do not provide sandboxing. They only bound recursive or
self-amplifying behavior inside the existing in-process runtime.

---

## 7. AgentRun State Machine

*(Rewritten 2026-08-05. The previous diagram named `PENDING`, `RUNNING`, `REJECTED` and
`STUCK`; the enum has none of those. It also omitted five states that exist.)*

The authoritative enum is `AgentRunStatus` in `AINDY/kernel/condition_codes.py` — **ten**
states:

```
pending_approval → approved → executing → completed
                                        → failed
                                        → verify_failed
                            → awaiting_delegation → delegated
                 → executing → waiting → executing        (event WAIT/RESUME)
any non-terminal → cancelled                              (sys.v1.agent.cancel)
completed        → (new pending_approval via replay)
```

| State | Notes |
|---|---|
| `pending_approval` | initial; the approve CAS fires only from here |
| `approved` | token minted; execution dispatched to a background thread |
| `awaiting_delegation` / `delegated` | multi-agent handshake (RTR-4, opt-in) |
| `executing` | running |
| `waiting` | suspended on an event wait |
| `completed` / `failed` | ordinary terminal outcomes |
| `cancelled` | operator-driven terminal state, `sys.v1.agent.cancel` (AGENT-HARDEN-1) |
| `verify_failed` | plan completed but the verifier rejected the result; triggers effect rollback (AGENT-HARDEN-6) |

**Terminal states are `completed`, `failed`, `cancelled`, `verify_failed`** — classify with
`is_agent_terminal()` / `AGENT_TERMINAL_STATUSES` rather than comparing literals. RTR-3 exists
because a hardcoded `status != "executing"` guard silently no-op'd recovery for runs parked in
any other non-terminal state.

---

## 8. Event Persistence

Current extracted-runtime status contract:
- persisted statuses are `pending_approval`, `approved`, `executing`,
  `delegated`, `completed`, `failed`, and `rejected`
- delegation guardrail violations surface as explicit failed runs rather than
  silent delegation no-ops
- replay and autonomous submission guardrails reject or suppress recursive
  amplification before a new execution is spawned

Every state transition emits an `AgentEvent` row via `emit_event()`. Events
are also broadcast to Redis pub/sub for cross-instance observability.

Key event types:

| Event | Trigger |
|---|---|
| `agent.run.created` | `create_agent_run()` completes |
| `agent.run.approved` | `approve_agent_run()` completes |
| `agent.run.rejected` | `reject_agent_run()` completes |
| `agent.step.completed` | each step finishes successfully |
| `agent.step.failed` | each step fails (all retry attempts exhausted) |
| `agent.run.completed` | final step succeeds |
| `agent.run.failed` | a non-retryable failure halts the run |
| `agent.run.recovered` | `recover_agent_run()` transitions STUCK → RUNNING |

The `AgentEvent` timeline is accessible at
`GET /agent/run/{id}/timeline`.

---

## 9. Boundary Rules

Hard rule:
- code under `AINDY/` must not directly import `apps.*`
- runtime may interact with plugins only through runtime-owned registries, interfaces, and contracts
- plugin implementations remain app-owned, but runtime must not import plugin modules directly

The agent runtime therefore uses explicit runtime-owned plugin contracts for:
- planner context providers
- planner backend providers
- run tool providers
- capability definition providers
- trigger evaluators
- agent completion hooks
- tool suggestion providers

Interpret these contracts narrowly:
- planner context provider: runtime guarantees a generic default provider; KPI-aware or analytics-aware context remains plugin-owned
- planner backend provider: runtime guarantees explicit backend selection and a
  compatibility adapter, but not a built-in provider-independent model runtime
- tool suggestion provider: runtime guarantees only an empty fallback; suggestion logic remains plugin-owned
- agent completion hook: runtime guarantees only a no-op fallback; post-run score/orchestration behavior remains plugin-owned
- run tool provider: runtime defaults expose only runtime memory tools; app tools remain optional plugin enrichments

Agent lifecycle persistence is runtime-owned:
- `AINDY/db/models/agent_run.py` defines `AgentRun`, `AgentStep`, and `AgentTrustSettings`
- `AINDY/db/models/agent_event.py` defines `AgentEvent`
- runtime code imports these models from `AINDY.db.models`, not from `apps.agent.models.*`

That statement still applies specifically to the runtime side. The runtime now
owns the user-facing agent HTTP surface and helper API layer. App plugins still
own agent tools, plugin registration, and app-specific extensions.

No-plugin behavior is fail-safe at the runtime boundary:
- runtime defaults provide a generic planner context
- runtime defaults register the built-in planner backends
- runtime defaults select `runtime_local` unless configuration overrides it
- runtime defaults provide the memory tool catalog
- no app suggestion provider -> empty suggestion list
- no app completion hook -> runtime no-op completion
- no additional run tool provider -> runtime-only tool list remains available
- no capability provider beyond runtime defaults -> only runtime default capabilities are granted

App plugins may replace or extend these defaults through registry registration,
but the runtime must continue to start and answer requests without assuming any
specific app such as analytics is present.

So today:
- platform boot is still registry-driven
- agent runtime persistence and execution code are aligned on runtime-owned models
- platform full operation is not yet independent from app-owned components
- runtime must not import plugins directly

See [PLUGIN_REGISTRY_PATTERN.md](../architecture/PLUGIN_REGISTRY_PATTERN.md)
for the registration model and
[CROSS_DOMAIN_COUPLING.md](../architecture/CROSS_DOMAIN_COUPLING.md) for the
coupling rules that apply to the Infinity Loop post-execution integration.
