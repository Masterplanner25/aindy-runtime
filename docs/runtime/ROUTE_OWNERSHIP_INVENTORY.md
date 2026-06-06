---
title: "Route Ownership Inventory"
last_verified: "2026-06-06"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Route Ownership Inventory

This document classifies every router in `AINDY/routes/` as core runtime, platform
operator, or app-layer extraction candidate. Its purpose is to make ownership
intentional rather than accidental — route existence does not imply mature runtime
ownership.

This answers Open Question 10 from `OPEN_QUESTIONS.md`.

---

## Classification Key

| Class | Meaning |
|---|---|
| **core** | Execution/readiness/auth critical. Must stay in runtime. |
| **operator** | Admin/observability surface. Correctly runtime-owned today; extraction would lose tight coupling to live runtime state. |
| **candidate** | Application-layer primitive. Could migrate to `aindy-apps-monolith`; runtime ownership is accidental rather than essential. |
| **extracted** | Migrated to `aindy-apps-monolith`; runtime file is a deprecated reference copy only. |

---

## Summary Table

| Router file | Registered prefix | Class | Note |
|---|---|---|---|
| `health_router.py` | `/health`, `/ready`, `/readiness`, `/client` | **core** | Readiness/liveness; orchestrator-critical |
| `auth_router.py` | `/auth` | **core** | Session/token lifecycle; execution-critical |
| `version_router.py` | `/api` | **core** | Version + compatibility; client boot dependency |
| `flow_router.py` | `/platform/flows` | **core** | Flow run visibility and resume control |
| `watcher_router.py` | `/watcher` | **operator** | External agent feed; operator-managed |
| `observability_router.py` | `/platform/observability` | **operator** | Scheduler/LLM/queue diagnostics; coupled to live runtime state |
| `db_verify_router.py` | `/platform/db` | **operator** | Schema inspection diagnostic |
| `platform_router.py` (composite) | `/platform/flows`, `/platform/nodes`, `/platform/webhooks`, `/platform/keys`, `/platform/nodus`, `/platform/ops`, `/platform/queue` | **operator** | Platform administration; tight syscall/flow/key coupling |
| `agent_router.py` | `/apps/agent` | **extracted** | Canonical owner: `apps/agent/routes/agent_router.py` (2026-06-06) |
| `memory_router.py` | `/apps/memory` | **candidate** | Platform primitive; Nodus execution coupling is the key blocker |
| `memory_metrics_router.py` | `/apps/memory/metrics` | **extracted** | Canonical owner: `apps/memory/routes/memory_metrics_router.py` (2026-06-06) |
| `memory_trace_router.py` | `/apps/memory/traces` | **extracted** | Canonical owner: `apps/memory/routes/memory_trace_router.py` (2026-06-06) |
| `coordination_router.py` | `/apps/coordination` | **candidate** | Multi-agent feature; needs service-layer wrapper before extraction |

---

## Core Runtime (keep, non-negotiable)

### `health_router.py`
Endpoints: `GET /health`, `GET /health/detail`, `GET /health/deep`, `GET /health/domains`,
`GET /health/sandbox`, `GET /ready`, `GET /readiness`, `POST /client/error`,
`POST /client/vitals`

Rationale: Readiness/liveness semantics are load-bearing for orchestrators, load
balancers, and SDK consumers. Deep health (`/health/deep`) checks DB, Redis, Mongo,
scheduler, flow engine, syscall registry, and worker state. Not extractable.

### `auth_router.py`
Endpoints: `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`,
`POST /auth/admin/invalidate-sessions/{user_id}`

Rationale: JWT issuance and session lifecycle are prerequisites for all authenticated
routes. Every downstream repo (SDK, SPA, monolith) depends on these endpoints by
stable contract.

### `version_router.py`
Endpoints: `GET /api/version`

Rationale: Returns runtime identity, compatibility metadata, boot profile, plugin
inventory, and sandbox posture. Used by SDK on boot, by the SPA's `bootIdentity()`,
and by `test_cross_repo_compatibility.py`. Stable surface.

### `flow_router.py` (mounted under `/platform/flows`)
Endpoints: `GET /platform/flows/runs`, `GET /platform/flows/runs/{id}`,
`GET /platform/flows/runs/{id}/history`, `POST /platform/flows/runs/{id}/resume`,
`GET /platform/flows/registry`

Rationale: Flow run management is core execution infrastructure. Resume control
(`/resume`) is the operator-facing handle on the WAIT/RESUME state machine.

---

## Platform Operator (keep in runtime, operator-facing)

These routers are correctly runtime-owned today. They are tightly coupled to live
runtime state (scheduler, circuit breakers, queue, syscall registry) in ways that
make extraction impractical without a formal service layer.

### `watcher_router.py`
Endpoints: `POST /watcher/signals`, `GET /watcher/signals`

Receives activity signals from the headless Watcher process. Uses API key auth
(`X-API-Key`). Delegates to `run_flow("watcher_signals_receive")` — the business
logic is in the flow, not the router.

### `observability_router.py`
Endpoints under `/platform/observability/`: `llm/status`, `rippletrace/status`,
`scheduler/status`, `requests`, `dashboard`, `execution_graph/{trace_id}`,
`queue/metrics`, `dead-letter`, `dead-letter/{id}`, `queue/dlq/drain`

Reads live state from circuit breakers, scheduler, flow engine, and queue directly.
Could theoretically be moved to an admin app, but would need every runtime subsystem
to expose a stable query API first.

### `db_verify_router.py`
Endpoints: `GET /platform/db/verify`

Post-migration schema inspection. Operator diagnostic only; not consumed by SDK or SPA.

### `platform_router.py` (composite — 8 sub-routers)

| Sub-router | Prefix | Summary |
|---|---|---|
| `flows_router` | `/platform/flows` | Flow template CRUD; `POST /platform/flows/run` |
| `nodes_router` | `/platform/nodes` | Node definition CRUD |
| `webhooks_router` | `/platform/webhooks` | Webhook subscription management |
| `keys_router` | `/platform/keys` | Platform API key lifecycle |
| `nodus_router` | `/platform/nodus` | Nodus script upload, list, run |
| `nodus_flow_router` | `/platform/nodus/flow` | Compile-and-run Nodus flows |
| `nodus_schedule_router` | `/platform/nodus/schedule` | Schedule Nodus tasks |
| `platform_ops_router` | `/platform/ops`, `/platform/memory/path` | Syscall dispatch, tenant usage, memory path query |
| `queue_router` | `/platform/queue` | Dead-letter queue inspection and drain |

`keys_router` (`/platform/keys`) is a stable SDK-facing surface (SDK contract lists
it). The others are operator/admin surfaces.

---

## App-Layer Extraction Candidates

These routers are under `/apps/` and represent application-layer primitives. Runtime
ownership is accidental — they exist here because the repo split happened before
a formal extraction plan was written.

**Extraction is not urgent.** The monolith's plugin registration pattern provides a
clean path: each candidate would move to `aindy-apps-monolith` and re-register its
router via `register_router()` at bootstrap time. URL paths do not need to change.

### `agent_router.py` — **EXTRACTED 2026-06-06**

Canonical implementation: `apps/agent/routes/agent_router.py` (aindy-apps-monolith).
Registered by `apps.agent.bootstrap._register_routers()` at plugin bootstrap time.
`AINDY/routes/agent_router.py` is a deprecated reference copy — retained for reference,
not registered, does not define a live API surface.

Endpoints (unchanged URLs): `POST /apps/agent/run`, `GET /apps/agent/runs`, `GET /apps/agent/runs/{id}`,
`POST /apps/agent/runs/{id}/approve`, `POST /apps/agent/runs/{id}/reject`,
`POST /apps/agent/runs/{id}/recover`, `POST /apps/agent/runs/{id}/replay`,
`GET /apps/agent/runs/{id}/steps`, `GET /apps/agent/runs/{id}/events`,
`GET /apps/agent/tools`, `GET /apps/agent/trust`, `GET /apps/agent/suggestions`,
`PUT /apps/agent/trust`

All 13 endpoints have matching `ROUTES.AGENT.*` constants and `agent.js` functions
(`recover`/`replay` added 2026-06-06). No SPA components consume recover/replay
yet — first use will drive the component work.

### `memory_router.py`
Endpoints: Full CRUD, semantic search, graph traversal, recall (v1/v3), federated
recall, agent namespace recall, feedback, performance metrics, Nodus execution
(`POST /apps/memory/nodus/execute`), memory-augmented execution loop.

**Primary blocker:** `POST /apps/memory/nodus/execute` and `POST /apps/memory/execute`
call `execute_nodus_task_payload()` and import `NodusSecurityError` — Nodus execution
is deeply coupled to runtime internals. The memory CRUD and search endpoints are
independently extractable; the execution endpoints are not until Nodus exposes a
stable execution service interface.

**Recommended split:** extract memory CRUD/search/recall endpoints first; leave
`/nodus/execute` and `/execute` in runtime until the Nodus service interface is stable.

### `memory_metrics_router.py` — **EXTRACTED 2026-06-06**

Canonical implementation: `apps/memory/routes/memory_metrics_router.py` (aindy-apps-monolith).
Registered by `apps.memory.bootstrap._register_routers()`.
`AINDY/routes/memory_metrics_router.py` is a deprecated reference copy.

Endpoints (unchanged): `GET /apps/memory/metrics`, `GET /apps/memory/metrics/detail`,
`GET /apps/memory/metrics/dashboard`

### `memory_trace_router.py` — **EXTRACTED 2026-06-06**

Canonical implementation: `apps/memory/routes/memory_trace_router.py` (aindy-apps-monolith).
Registered by `apps.memory.bootstrap._register_routers()`.
`AINDY/routes/memory_trace_router.py` is a deprecated reference copy.

Endpoints (unchanged): `POST /apps/memory/traces`, `GET /apps/memory/traces`,
`GET /apps/memory/traces/{id}`, `GET /apps/memory/traces/{id}/nodes`,
`POST /apps/memory/traces/{id}/append`

### `coordination_router.py`
Endpoints: `GET /apps/coordination/agents`, `GET /apps/coordination/agents/status`,
`GET /apps/coordination/graph`, `POST /apps/coordination/agents/register`,
`POST /apps/coordination/agents/{id}/heartbeat`,
`DELETE /apps/coordination/agents/{id}`, `GET /apps/coordination/runs`,
`GET /apps/coordination/runs/{parent_id}/children`,
`GET /apps/coordination/messages/inbox`,
`POST /apps/coordination/messages/{id}/acknowledge`,
`GET /apps/coordination/memory/shared`,
`POST /apps/coordination/conflict/run`,
`POST /apps/coordination/conflict/memory`

**Blocker:** `AgentRegistry` model is in `AINDY/db/models/` (runtime-owned). Extraction
requires either moving the model to the apps layer or exposing it via a runtime service
interface that the app can call. The conflict detection endpoints also touch
`MemoryNodeModel`.

---

## Extraction Readiness Summary

| Candidate | Extraction readiness | Status |
|---|---|---|
| `agent_router.py` | — | **EXTRACTED 2026-06-06** — canonical router in `apps/agent/routes/agent_router.py`; registered via `apps.agent.bootstrap._register_routers()` |
| `memory_metrics_router.py` | — | **EXTRACTED 2026-06-06** — canonical router in `apps/memory/routes/memory_metrics_router.py`; registered via `apps.memory.bootstrap._register_routers()` |
| `memory_trace_router.py` | — | **EXTRACTED 2026-06-06** — canonical router in `apps/memory/routes/memory_trace_router.py`; registered via `apps.memory.bootstrap._register_routers()` |
| `memory_router.py` (CRUD/search) | Medium — split required | Nodus execution endpoints must stay until service interface exists |
| `memory_router.py` (execution) | Low — deeply coupled | `execute_nodus_task_payload()` / `NodusSecurityError` |
| `coordination_router.py` | Low — model ownership gap | `AgentRegistry` model in runtime DB layer |

---

## Rules for Future Route Additions

When adding a new router, ask:

1. **Does this route exist to enforce an execution contract, readiness truth, or
   authentication invariant?** → core runtime.
2. **Does this route read live runtime state** (scheduler, circuit breakers, queue,
   syscall registry) **that has no stable service interface?** → operator, keep in runtime.
3. **Does this route implement an application-level feature** (user memory, agent
   orchestration, coordination) **that has no execution-critical coupling to the
   runtime kernel?** → register via `aindy-apps-monolith` bootstrap instead.

Do not add routes under `/apps/` directly to `AINDY/routes/` unless there is a
specific reason they cannot live in the plugin registration layer.
