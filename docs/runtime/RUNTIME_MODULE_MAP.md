---
title: "Runtime Module Map"
last_verified: "2026-06-03"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Runtime Module Map

This document tags every directory and flat module in `AINDY/` as one of:

> **Partial re-verification 2026-08-13.** Three sections described directories that have
> since been removed (`AINDY/domain/`, `AINDY/modules/`, `AINDY/Tools/authorship/`) — in each
> case the map recommended removal and the removal happened. Those are annotated in place.
> `AINDY/grep.bat`, `AINDY/scripts/nodus/`, `AINDY/apscheduler/` and `AINDY/watcher/` were
> spot-checked and still exist. The rest of the inventory has **not** been re-verified against
> the tree — see TECH_DEBT `DOCS-STALE-1`.

- **CORE RUNTIME** — directly required for execution correctness, syscall dispatch,
  flow execution, wait/resume, tenant/capability enforcement, startup ordering,
  or readiness truth. Removing it would break a runtime guarantee, not a convenience.
- **PLATFORM SUPPORT** — necessary for production operability, security enforcement,
  or operational surfaces but one step removed from the execution nucleus. The
  runtime cannot function well without it, but it does not define execution truth.
- **LEGACY SPILLOVER** — present because it was extracted from the monolith alongside
  runtime code, not because it belongs in the execution substrate.
- **EXTRACTION CANDIDATE** — should migrate to `aindy-sdk`, `aindy-apps-monolith`,
  or a standalone package; or should be deleted if it has no runtime justification.

Use this alongside `RUNTIME_BOUNDARY.md`, which defines the ownership rules.
This document is the inventory; the boundary document is the policy.

---

## AINDY Package Root (flat files)

| File | Tag | Notes |
|---|---|---|
| `_version.py` | CORE RUNTIME | Single version source of truth |
| `main.py` | CORE RUNTIME | FastAPI app factory, lifespan context manager |
| `startup.py` | CORE RUNTIME | Boot sequencing; phase ordering is an execution invariant |
| `routing.py` | CORE RUNTIME | Route registration and SPA static mount |
| `runtime_only.py` | CORE RUNTIME | CLI entrypoint, lazy-app loader, boot selector |
| `middleware.py` | CORE RUNTIME | Trace ID injection, request logging, metrics middleware |
| `exception_handlers.py` | CORE RUNTIME | Global FastAPI exception → envelope normalization |
| `config.py` | CORE RUNTIME | Pydantic settings; all runtime config flows here |
| `runtime_plugins.json` | CORE RUNTIME | Runtime-owned plugin manifest |
| `version.json` | CORE RUNTIME | Version metadata consumed by `/api/version` |
| `system_manifest.json` | PLATFORM SUPPORT | Static system descriptor; not load-bearing for execution |
| `deepseek_config.json` | PLATFORM SUPPORT | DeepSeek model routing config |
| `cli.py` | PLATFORM SUPPORT | CLI entrypoint shim |
| `spa_fallback.py` | PLATFORM SUPPORT | SPA browser-navigation fallback for `/platform` routes |
| `worker.py` | PLATFORM SUPPORT | Background worker process launcher |
| `requirements.txt` | PLATFORM SUPPORT | Pip requirements file (install surface) |
| `grep.bat` | LEGACY SPILLOVER | Developer utility; should not be in the installed package |

---

## AINDY/kernel/

**Tag: CORE RUNTIME**

The execution nucleus. Every component here is load-bearing for runtime correctness.

| Module | Notes |
|---|---|
| `syscall_dispatcher.py` | The single entry point for all capability calls |
| `syscall_registry.py` | Registry of all valid syscalls; schema enforcement |
| `event_bus.py` | Redis pub/sub; wait/resume delivery backbone |
| `scheduler/` | Priority-queue executor; lane management (high/normal/low) |
| `scheduler_engine.py` | Public scheduler surface; flow enqueue/resume |
| `circuit_breaker.py` | Execution-circuit isolation |
| `resource_manager.py` | Per-tenant concurrency and per-EU quota enforcement |
| `tenant_context.py` | Tenant isolation in execution paths |
| `redis_wait_registry.py` | In-memory wait registration; cross-instance broadcast |

No extraction candidates. Reducing kernel surface is a Phase 3 goal, not removal.

---

## AINDY/core/

**Tag: CORE RUNTIME**

The execution pipeline middleware layer. Every route handler runs inside it.

| Module | Notes |
|---|---|
| `execution_pipeline/` | `ExecutionPipeline` — sets ContextVars, claims ExecutionUnit, records metrics, captures memory signals, emits SystemEvents |
| `dispatcher.py` | Syscall dispatch helpers used by the pipeline |
| `envelope.py` | Uniform `{status, data, trace_id, duration_ms, error}` response shape |
| `gate.py` | ExecutionGate — pre-execution guards |
| `guard.py` | Runtime guard enforcement |
| `flow_run_rehydration.py` | Re-registers `waiting` FlowRun rows on restart; wait/resume recovery path |
| `distributed_queue.py` | DistributedQueue — Redis-backed cross-instance work queue |
| `system_event_service.py` | SystemEvent record emission |
| `request_metric_writer.py` | Request-scoped metric capture |
| `resume_watchdog.py` | Watchdog for stalled wait/resume registrations |
| `retry_policy.py` | Configurable retry semantics for execution steps |

No extraction candidates here. These modules define core execution behavior.

---

## AINDY/runtime/

**Tag: CORE RUNTIME**

The flow engine and Nodus execution layer. This is what runs work.

| Module / Sub-package | Notes |
|---|---|
| `flow_engine/` | DAG executor; node scheduling, step sequencing, flow state machine |
| `memory/` | Memory loop wiring for flows; deferred-write coordination |
| `nodus_worker.py` | Compiles and runs `.nodus` scripts; injects memory builtins; propagates WAIT semantics |
| `nodus_runtime_adapter.py` | Bridges Nodus execution context to the flow engine |
| `nodus_builtins.py` | `DeferredMemoryBuiltins` — `recall`, `search`, `write` backed by memory_context |
| `nodus_compiler.py` | Nodus script compilation step |
| `flow_schedule_service.py` | Flow scheduling entry points |
| `security.py` | Execution-path security checks |
| `trace_service.py` | Execution trace capture |

No extraction candidates here. These are the execution substrate proper.

---

## AINDY/db/

**Tag: CORE RUNTIME**

Authoritative runtime persistence. The 27 runtime-owned ORM tables are execution truth.

| Module / Sub-package | Notes |
|---|---|
| `models/` | All runtime ORM models: FlowRun, ExecutionUnit, EffectRecord, AgentRun, MemoryNode, MemoryTrace, SystemEvent, ScheduledJob, … |
| `dao/` | Data access objects for runtime-owned tables |
| `database.py` | SQLAlchemy engine and SessionLocal factory |
| `schema_contract.py` | `SCHEMA_CONTRACT_VERSION` — schema change protocol |
| `schema_ops.py` | Schema ops CLI helpers |
| `mongo_setup.py` | MongoDB connection setup (memory supplementary store) |

No extraction candidates. Persistence models are the authoritative execution record.

---

## AINDY/agents/

**Tag: CORE RUNTIME**

Agent execution is an execution-critical path through the kernel.

| Module | Notes |
|---|---|
| `agent_runtime/execution.py` | `execute_run()` — primary agent execution entry point |
| `agent_runtime/approvals.py` | `approve_run()` — CAS-guarded `pending_approval → approved` transition |
| `agent_runtime/creation.py` | AgentRun creation and validation |
| `agent_runtime/planning.py` | Planner invocation |
| `agent_runtime/planner_backends.py` | Planner backend adapters |
| `agent_runtime/shared.py` | Shared helpers across agent runtime |
| `agent_runtime/presentation.py` | Response shaping for agent API | 
| `agent_runtime/replay.py` | Run replay paths |

`presentation.py` is borderline — it shapes API responses rather than driving execution.
If the agent route surface is ever narrowed, this module is the first candidate.

---

## AINDY/memory/

**Tag: CORE RUNTIME** (persistence/ingest core) — **PLATFORM SUPPORT** (scoring/MAS)

Memory correctness is required for execution (`memory.recall` in flows affects outputs).

| Module / Sub-package | Notes |
|---|---|
| `memory_persistence.py` | MemoryNode CRUD; pgvector upsert — CORE RUNTIME |
| `memory_ingest_service.py` | Write-path orchestration — CORE RUNTIME |
| `memory_ingest_worker.py` | Async embedding queue consumer — CORE RUNTIME |
| `embedding_service.py` | OpenAI embedding API calls — CORE RUNTIME |
| `embedding_jobs.py` | Embedding batch job management — CORE RUNTIME |
| `memory_address_space.py` | MAS path query (`/memory/{tenant}/{ns}/{type}/{id}`) — PLATFORM SUPPORT |
| `memory_scoring_service.py` | Retrieval ranking (impact score, usage, causal depth) — PLATFORM SUPPORT |
| `memory_capture_engine.py` | Post-execution memory signal capture — PLATFORM SUPPORT |
| `native/` | Rust native scorer bridge (optional perf path via Maturin) — PLATFORM SUPPORT |

---

## AINDY/nodus/

**Tag: CORE RUNTIME**

Nodus stdlib and runtime adapter. All flow execution goes through Nodus.

| Module / Sub-package | Notes |
|---|---|
| `runtime/` | Nodus runtime bindings for the execution context |
| `stdlib/memory.nd` | Nodus stdlib memory primitive |

No extraction candidates. Nodus coupling is a documented architectural decision.

---

## AINDY/platform_layer/

**Tag: PLATFORM SUPPORT** (majority) with execution-critical subset

This is the most heterogeneous package in the repo. Splitting it is a Phase 3 goal.
The execution-critical subset is marked below.

### Execution-critical subset (closer to CORE RUNTIME)

| Module | Notes |
|---|---|
| `deployment_contract.py` | Deployment profile semantics; enforces runtime-only vs full |
| `bootstrap_contract.py` | Boot contract enforcement |
| `bootstrap_graph.py` | Startup dependency graph |
| `extension_abi.py` | Extension ABI surface |
| `extension_boundary.py` | Extension isolation boundary |
| `extension_capabilities.py` | Extension capability grants |
| `extension_policy.py` | Extension execution policy |
| `extension_execution_model.py` | Extension execution model enforcement |
| `extension_worker.py` | Extension process worker |
| `sandbox_runner.py` | Sandbox profile selection and runner dispatch |
| `sandbox_certification.py` | Sandbox capability reporting (`aindy-runtime sandbox`) |
| `health_service.py` | Provides `/health` and `/ready` implementation |
| `metrics.py` | Prometheus metrics registry |
| `otel.py` | OpenTelemetry trace setup |
| `rate_limiter.py` | Per-tenant rate enforcement |
| `registry.py` | Plugin/callback registration |
| `registry_contracts.py` | Typed registry contracts |
| `runtime_callback_host.py` | Subprocess isolation for registered callbacks |
| `runtime_callback_worker.py` | Subprocess callback runner |
| `runtime_compatibility.py` | Compatibility metadata for cross-repo version checks |
| `public_contract.py` | Declared public surface |

### Platform support (operational / convenience)

| Module | Notes |
|---|---|
| `scheduler_service.py` | APScheduler maintenance jobs (TTL cleanup, stale log cleanup) |
| `system_state_service.py` | System-wide state queries |
| `node_registry.py` | Node type registry for the operator panel |
| `platform_loader.py` | Platform module loader |
| `plugin_host.py` | Plugin host process management |
| `plugin_artifacts.py` | Plugin artifact storage |
| `agent_plugin_contracts.py` | Agent plugin type contracts |
| `api_key_service.py` | API key lifecycle (create/revoke/validate) |
| `app_runtime.py` | App-layer runtime shim |
| `async_execution_context.py` | Async execution context helpers |
| `async_job_service.py` | Async job management |
| `cache_backend.py` | Redis cache backend |
| `domain_health.py` | Domain-level health aggregation |
| `event_service.py` | Platform event dispatch helpers |
| `event_trace_service.py` | Event trace storage |
| `external_call_service.py` | External HTTP call wrapper |
| `extension_provenance.py` | Extension provenance tracking |
| `extension_provenance_inventory.py` | Provenance inventory |
| `extension_runtime_api.py` | Extension runtime API helpers |
| `extension_runtime_inventory.py` | Extension inventory |
| `kernel_proc_reader.py` | Kernel process reader |
| `log_config.py` | Logging configuration |
| `memory_runtime.py` | Memory subsystem runtime shim |
| `nodus_script_store.py` | Nodus script persistence (operator panel) |
| `recovery_jobs.py` | Post-crash recovery job scheduling |
| `response_adapters.py` | Response shaping adapters |
| `runtime_agent_defaults.py` | Default agent configuration |
| `trace_context.py` | Distributed trace context propagation |
| `user_ids.py` | User ID resolution helpers |

### Notes on LLM clients

`llm_client.py`, `openai_client.py`, `deepseek_client.py` are the runtime's internal
clients for calling OpenAI and DeepSeek directly — used for embedding generation and
LLM completions as part of the execution path. They are not candidates for extraction
to `aindy-sdk`: the SDK wraps the runtime API for external consumers; these wrap external
AI APIs for the runtime's own use. They are correctly in `platform_layer/` as internal
runtime dependencies on external services.

---

## AINDY/routes/

**Tag: MIXED** — tagged per router

### Core Runtime routes (define execution or runtime contracts)

| Router | Tag | Justification |
|---|---|---|
| `health_router.py` | CORE RUNTIME | `/health`, `/ready` — runtime readiness contract |
| `version_router.py` | CORE RUNTIME | `/api/version` — runtime metadata contract |
| `auth_router.py` | CORE RUNTIME | Issues JWTs required for all authenticated execution paths |
| `flow_router.py` | CORE RUNTIME | Flow execution contract |
| `agent_router.py` | CORE RUNTIME | Agent execution contract |
| `memory_router.py` | CORE RUNTIME | Memory read/write required for execution correctness |
| `coordination_router.py` | CORE RUNTIME | Multi-agent coordination (execution-critical path) |

### Platform support routes (operational, not execution contracts)

| Router | Tag | Justification |
|---|---|---|
| `db_verify_router.py` | PLATFORM SUPPORT | DB connectivity check; operator tool, not execution contract |
| `observability_router.py` | PLATFORM SUPPORT | Scheduler/system status; operational telemetry |
| `memory_metrics_router.py` | PLATFORM SUPPORT | Memory metric aggregates; operational |
| `memory_trace_router.py` | PLATFORM SUPPORT | Memory trace inspection; operational |
| `platform_router.py` | PLATFORM SUPPORT | Operator panel router aggregate |
| `platform/flows_router.py` | PLATFORM SUPPORT | Operator flow management |
| `platform/keys_router.py` | PLATFORM SUPPORT | API key management |
| `platform/nodes_router.py` | PLATFORM SUPPORT | Node registry queries |
| `platform/nodus_router.py` | PLATFORM SUPPORT | Nodus script management |
| `platform/nodus_flow_router.py` | PLATFORM SUPPORT | Nodus flow triggers |
| `platform/nodus_schedule_router.py` | PLATFORM SUPPORT | Nodus schedule management |
| `platform/platform_ops_router.py` | PLATFORM SUPPORT | Operator ops (scheduler status, restart, etc.) |
| `platform/queue_router.py` | PLATFORM SUPPORT | Queue inspection |

### Extraction candidates in routes

| Router | Tag | Notes |
|---|---|---|
| `watcher_router.py` | PLATFORM SUPPORT | Signal ingestion for the watcher client; triggers flows |
| `platform/webhooks_router.py` | Review | Webhook management; convenience surface, not execution-critical |

---

## AINDY/services/

**Tag: PLATFORM SUPPORT**

| Module | Notes |
|---|---|
| `auth_service.py` | bcrypt password hashing, JWT issuance, key ring management |

Auth is required for security but not the execution nucleus.
If the auth surface ever migrates to a dedicated auth service, this is the extraction unit.

---

## AINDY/schemas/

**Tag: PLATFORM SUPPORT**

| Module | Notes |
|---|---|
| `auth.py` | Pydantic schemas for auth request/response bodies |

---

## AINDY/auth/

**Tag: PLATFORM SUPPORT**

| Module | Notes |
|---|---|
| `api_key_middleware.py` (or equivalent) | API key validation middleware on inbound requests |

Security enforcement belongs at the runtime boundary but is platform support, not execution nucleus.

---

## AINDY/apscheduler/

**Tag: PLATFORM SUPPORT**

Thin wrappers around the APScheduler library. The `SchedulerEngine` itself (execution-critical) lives in `kernel/`; this package provides the APScheduler scheduler/trigger implementations the engine delegates to.

---

## AINDY/utils/

**Tag: PLATFORM SUPPORT**

| Module | Notes |
|---|---|
| Text utilities, sanitize, normalize_encoding, UUID helpers | Cross-cutting utility; no runtime-specific logic |

---

## AINDY/worker/

**Tag: PLATFORM SUPPORT**

| Module | Notes |
|---|---|
| `worker_loop.py` | Background worker process main loop |
| `health_server.py` | Worker health HTTP server |
| `memory_ingest_worker.py` | Memory embedding queue consumer |
| `metric_writer_worker.py` | Metric flush worker |

Required for production memory ingestion and metric write buffering.
Not inline execution; these are operational support processes.
0% test coverage is a known gap (see TECH_DEBT.md).

---

## AINDY/watcher/

**Tag: PLATFORM SUPPORT**

The watcher is an intentional product component, not legacy spillover. It runs as a
**separate client process** on the user's machine, polls the active OS window at a
configurable interval (`poll_interval`, default 5s), classifies the activity as
WORK / COMMUNICATION / DISTRACTION / IDLE / UNKNOWN, tracks session state transitions
(session_started, focus_achieved, distraction_detected, session_ended, heartbeat),
and emits batched signals to the runtime via `POST /watcher/signals`.

The runtime side (`watcher_router.py`) runs incoming signals through
`execute_with_pipeline_sync` and triggers `run_flow("watcher_signals_receive", ...)`,
meaning watcher signals are a flow-triggering event source. The query path uses
`SyscallDispatcher.dispatch("sys.v1.watcher.query", ..., capabilities=["watcher.query"])`.

**Client code location (2026-06-03):** The watcher client process
(`classifier.py`, `window_detector.py`, `session_tracker.py`, `signal_emitter.py`,
`config.py`, `watcher.py`) was extracted to `aindy_sdk/watcher/` in the `aindy-sdk`
repo. Run with `python -m aindy_sdk.watcher.watcher`.

**What remains in this package:** Only `constants.py` — the authoritative signal type
and activity type definitions that `watcher_router.py` and `watcher_contract.py`
import for server-side validation.

**0% test coverage is a gap**, not a design signal — the integration path
(watcher client → runtime API → flow engine) is the hard part to test.

| Module | Notes |
|---|---|
| `constants.py` | Signal type and activity type enumerations (server authority) |

---

## AINDY/plugins/nodes/

**Tag: LEGACY SPILLOVER → EXTRACTION CANDIDATE (empty)**

Empty directory. If plugin nodes are ever implemented they should live in
`aindy-apps-monolith` or a plugin package, not in the runtime itself.
The directory can be removed once confirmed unused by any install path.

---

## AINDY/platform/ (dist)

**Tag: PLATFORM SUPPORT**

Compiled SPA bundle served by `_SPAStaticFiles` at `/platform`. The runtime serves
it (deployment responsibility); `aindy-ui-kit` authors it. See `RUNTIME_BOUNDARY.md`
for the platform SPA ownership clarification.

---

## AINDY/domain/ and AINDY/modules/

**Tag: LEGACY SPILLOVER → EXTRACTION CANDIDATE (empty)**

> **Resolved — verified 2026-08-13: both directories no longer exist.** The recommendation
> below was carried out. The section is kept because `EXECUTION_CONTRACT.md` still refers to
> `domain.task_services`, `domain.genesis_ai`, `domain.masterplan_factory` and
> `modules.deepseek.*`; all of those are `aindy-apps-monolith`, and this is where that is
> recorded.

Both directories were empty stubs. No Python modules, no `__init__.py` detected.
These should be removed — they carry zero runtime value and create confusion
about planned-but-nonexistent surfaces.

---

## AINDY/Tools/authorship/

**Tag: LEGACY SPILLOVER**

> **Resolved — verified 2026-08-13: `AINDY/Tools/authorship/` no longer exists.**

Developer authorship tooling committed into the runtime package directory.
This is not runtime infrastructure and should not be in the installed wheel.
Belongs in a dev-tools repo or `.gitignore`d build artifact location.

---

## AINDY/scripts/nodus/

**Tag: LEGACY SPILLOVER**

Contains sample/test Nodus script files (`dup-script.nodus`, `stored-script.nodus`,
`test-script.nodus`). These are development artifacts, not runtime infrastructure.
Should live in `tests/fixtures/` or a dedicated samples directory, not in `AINDY/`.

---

## AINDY/grep.bat

**Tag: LEGACY SPILLOVER**

Developer utility batch script in the package root. Should not be committed here.

---

## Summary

| Tag | Key packages |
|---|---|
| **CORE RUNTIME** | `kernel/`, `core/`, `runtime/`, `db/`, `nodus/`, `agents/`, `memory/` (ingest/persistence core), plus `main.py`, `startup.py`, `routing.py`, `runtime_only.py`, `middleware.py`, `exception_handlers.py`, `config.py` |
| **PLATFORM SUPPORT** | `auth/`, `services/`, `schemas/`, `utils/`, `apscheduler/`, `worker/`, `watcher/` (client process), `platform/dist`, `platform_layer/` (majority) |
| **LEGACY SPILLOVER** | `domain/` (empty), `modules/` (empty), `plugins/nodes/` (empty), `Tools/authorship/`, `grep.bat`, `scripts/nodus/` |
| **EXTRACTION CANDIDATES** | Empty stubs (`domain/`, `modules/`, `plugins/nodes/`) → delete; `platform/webhooks_router.py` → review |

---

## Boundary Validation Notes

These findings either validate or challenge the claims in `RUNTIME_BOUNDARY.md`.

**Validates — kernel, core, runtime, db, nodus are cleanly substrate.**
No boundary leakage found. These packages contain nothing that belongs in SDK or app layers.

**Validates — watcher is intentional platform support, not legacy.**
The watcher runs as a separate client process, feeds signals through the execution
pipeline via `execute_with_pipeline_sync`, and can trigger flows. `watcher_service.py`
routes queries through SyscallDispatcher with `watcher.query` capability. The 0% test
coverage is a gap in test discipline, not a signal about design intent.

**Corrects earlier assumption — LLM clients are internal runtime dependencies, not SDK material.**
`platform_layer/llm_client.py`, `openai_client.py`, `deepseek_client.py` are the runtime's
own HTTP clients for calling OpenAI and DeepSeek. `aindy-sdk` wraps the runtime API for
external consumers; it does not and should not wrap the runtime's internal AI service calls.
These stay in `platform_layer/`.

**New finding — empty stubs create false surface area.**
`domain/`, `modules/`, and `plugins/nodes/` are empty. They suggest planned expansion
that never happened. Deleting them removes the implied promise of future surfaces.

**Validates — platform_layer/ split is the key Phase 3 challenge.**
The execution-critical subset within platform_layer (deployment contract, extension ABI,
sandbox runner, health service) is deeply intertwined with the platform support subset
(LLM clients, scheduler service, watcher shims). Splitting this package cleanly is the
highest-complexity boundary work in Phase 3.

**Challenge — agent/presentation.py and memory scoring are borderline.**
`agents/agent_runtime/presentation.py` shapes API responses rather than driving execution.
`memory/memory_scoring_service.py` and `memory_capture_engine.py` are above the execution
nucleus. These are the first modules to review if the agent or memory boundary is ever narrowed.
