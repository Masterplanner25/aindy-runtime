---
title: "Infinity Loop Audit"
api_version: "1.0"
last_verified: "2026-07-08"
status: current
owner: "platform-team"
---

# Infinity Loop Audit

> **Cross-repo pairing (2026-07-05).** This audit is the **runtime** end of a
> cross-repo pair. It covers Infinity loop closure at the **execution altitude**
> (`Intent→Plan→Execute→Observe→Memory→Recall→Score→Improve`) and enumerates the five
> structural runtime gaps below. The **app** end is the Infinity docset in
> `aindy-apps-monolith`: `docs/apps/INFINITY_ALGORITHM.md` and its siblings
> (`_CANONICAL`, `_FORMALIZATION`, `_SUPPORT_SYSTEM`). The Infinity scoring engine,
> orchestrator, and adjustment loop are **app-owned**
> (`apps/analytics/services/{scoring,orchestration}/`); this repo owns the execution
> substrate and the loop-closure primitives the app phases depend on. The two docsets
> are complementary, not duplicative. Gap 4 (a runtime-owned Next-Action engine) in
> particular gates the app-side Infinity Phase 2 ("force major execution through the
> orchestrator" / pre-dispatch control). Runtime-side tracking: `TECH_DEBT.md` →
> **INFINITY-RUNTIME-1** (accepts the app-side handoff `INFINITY-RUNTIME-HANDOFF-1`).

**Question:** Can every execution automatically complete the loop?

```
Intent → Plan → Execute → Observe → Memory → Recall → Score → Improve Next Execution
```

**Short answer:** The machine form is architecturally present but not uniformly closed.
The loop closes end-to-end on the agent execution path. It does not automatically close
on async jobs, standalone Nodus scripts, or flow executions that are not agent-backed.
Five structural gaps prevent the claim from being universally true today.

---

## Section-by-Section Verdict

### 1. Intent Intake ✅ Confirmed

| Requirement | Status | Evidence |
|---|---|---|
| Accept user goals | ✅ | `POST /agents/runs` → `AgentRun.goal` |
| Accept API-triggered jobs | ✅ | `sys.v1.job.submit` → `async_job_service` |
| Accept scheduled jobs | ✅ | APScheduler + `_scheduled_jobs` registry |
| Accept event-triggered jobs | ✅ | `EventBus.publish_event()` → scheduler `notify_event()` wakes waiting flows |
| Normalize all inputs to one request shape | ✅ | `SyscallContext` is the canonical execution identity across all paths |
| Attach user/session/context | ✅ | `SyscallContext.user_id`, `execution_unit_id`, `trace_id` |
| Attach permissions/capabilities | ✅ | `SyscallContext.capabilities` + scoped capability token on agent runs |
| Attach success criteria | ⚠️ | Present in agent plans (`overall_risk`, step `risk_level`); absent on flows and jobs |

**Gap:** Success criteria exist on the agent plan schema but are not a required field on
every execution path. Flows and async jobs have no formal success criteria contract.

---

### 2. Single Execution Pipeline ✅ Confirmed (with one known gap)

| Requirement | Status | Evidence |
|---|---|---|
| No route bypasses the pipeline | ✅ | `ExecutionPipeline.run()` wraps all route handlers via `execute_with_pipeline()` |
| No agent bypasses the pipeline | ✅ | `sys.v1.agent.execute` routes through `SyscallDispatcher` |
| No workflow bypasses the pipeline | ✅ | `sys.v1.flow.run` → `PersistentFlowRunner` inside dispatcher |
| No scheduled task bypasses the pipeline | ⚠️ | APScheduler maintenance jobs call `SessionLocal()` directly, not through `ExecutionPipeline` |
| No plugin bypasses the pipeline | ✅ | Extension callbacks go through dispatcher or subprocess isolation |
| Every execution has a run ID | ✅ | `FlowRun.id`, `AgentRun.id`, `ExecutionUnit.id`, `SyscallContext.execution_unit_id` |
| Every execution has lifecycle state | ✅ | `pending → executing → waiting → completed/failed/dead_letter` |
| Every execution has timeout/failure behavior | ✅ | `FLOW_WAIT_TIMEOUT_MINUTES`, `STUCK_RUN_THRESHOLD_MINUTES`, dead-letter service |

**Gap:** APScheduler maintenance jobs (`_cleanup_stale_logs`, `_cleanup_expired_effect_records`)
run outside `ExecutionPipeline`. They are infrastructure jobs, not user workloads, but they
set a precedent that not every execution path carries the full pipeline envelope.

---

### 3. Execution Contract ⚠️ Partial

| Contract field | Status | Evidence |
|---|---|---|
| Goal | ✅ | `AgentRun.goal`, `FlowRun.workflow_type` |
| Context | ✅ | `SyscallContext`, `memory_context` injected before execution |
| Plan | ✅ | `AgentRun.plan` (JSON: steps, tools, risks, executive_summary) |
| Tools allowed | ✅ | `capability_token.allowed_capabilities` + `TOOL_REGISTRY` |
| Capabilities allowed | ✅ | `SyscallContext.capabilities` enforced on every dispatch |
| Memory access allowed | ✅ | Capability gates: `memory.read`, `memory.write`, `memory.search` |
| Expected output | ❌ | Not defined as a contract field. No output schema per-run. |
| Success criteria | ⚠️ | Implicit via `overall_risk`; no machine-checkable success predicate |
| Error policy | ⚠️ | `error_policy` on Nodus execute (`halt`/`continue`); not on all paths |
| Retry policy | ⚠️ | `ExecutionUnit.extra.retry_policy.execution_guarantee` exists; not always populated |
| Event policy | ❌ | No per-run event emission policy. All events emit by default. |
| Memory write policy | ⚠️ | `_memory_policies` registry exists; not enforced on every path |
| Next-action policy | ❌ | No formal next-action contract field on any execution type |

**Gap:** The execution contract is structurally present for agent runs but is not a
validated schema enforced at execution start. Expected output, event policy, and
next-action policy have no runtime representation.

---

### 4. Planning Layer ⚠️ Partial

| Requirement | Status | Evidence |
|---|---|---|
| Turns goals into structured plans | ✅ | `generate_plan()` → LLM → `{executive_summary, steps, overall_risk}` |
| Uses recalled memory before planning | ✅ | Runtime-owned (2026-07-08): `generate_plan` → `_recall_planner_memory` → `MemoryOrchestrator.get_context` recalls objective-keyed memory pre-plan and injects it via `_build_planner_prompt`. Gated by `AINDY_PLANNER_MEMORY_INJECTION` (default off, opt-in); no longer dependent on the app provider's `context_block`. |
| Selects tools intentionally | ✅ | Planner prompt includes tool catalog with risk levels; LLM selects from registered tools |
| Defines steps | ✅ | Plan JSON has `steps[]` with tool, args, risk_level, description |
| Defines success conditions | ⚠️ | `overall_risk` is a proxy, not a success predicate |
| Defines fallback paths | ❌ | No fallback step definition in the plan schema |
| Can revise plans after failure | ❌ | No plan revision path. Failed runs stay failed; retry creates a new run. |
| Can explain why a plan was chosen | ⚠️ | `executive_summary` field provides a human-readable rationale. No formal provenance trace. |

**Critical gap:** The recall → planning link is broken at the architecture level. Memory
is injected into execution context but not systematically into the planner prompt.
The planner receives a `context_block` whose content depends entirely on the app-layer
planner context provider. This means prior execution history does not automatically
inform planning — it only informs execution.

> **RESOLVED 2026-07-08 (Gap 1, INFINITY-RUNTIME-1).** `generate_plan` now recalls memory
> keyed on the objective via a runtime-owned path (`_recall_planner_memory` →
> `MemoryOrchestrator.get_context`) — independent of the app-registered planner context
> provider — and threads it into the planner prompt through `_build_planner_prompt`
> (symmetric to `context_block`). Each recall (planning **and** execution) emits a
> `RECALL_USED` event (`core/execution_recall.py`). Injection is gated by
> `AINDY_PLANNER_MEMORY_INJECTION` (default **off**; flip after app-side soak so plan
> quality does not shift silently). The `RECALL_USED` observability half is always on.

---

### 5. Execution Layer ✅ Confirmed

| Requirement | Status | Evidence |
|---|---|---|
| Executes plans step-by-step | ✅ | `PersistentFlowRunner` → `_execute_current_node()` → step state machine |
| Supports tools | ✅ | `TOOL_REGISTRY` + `execute_tool()` + capability check |
| Supports agents | ✅ | `sys.v1.agent.execute` → `execute_agent_run_via_nodus()` |
| Supports workflows | ✅ | `sys.v1.flow.run` → `PersistentFlowRunner` |
| Supports scripts | ✅ | `sys.v1.nodus.execute` → `NodusRuntimeAdapter.run_script()` |
| Supports APIs | ✅ | `perform_external_call()` with circuit breaker protection |
| Supports human approval steps | ✅ | `AgentTrustSettings`, `APPROVED`/`REJECTED` events, approval gate in `approvals.py` |
| Tracks step status | ✅ | `FlowRun.current_node`, `FLOW_NODE_STARTED/COMPLETED/FAILED` events, `AgentEvent` log |
| Handles failure predictably | ✅ | `error_policy` (halt/continue), dead-letter, `fail_execution()`, stuck-run watchdog |

---

### 6. Event Ledger ⚠️ Partial

| Event | Status | Evidence |
|---|---|---|
| ExecutionStarted | ✅ | `SystemEventTypes.EXECUTION_STARTED` |
| PlanCreated | ⚠️ | `PLAN_CREATED` exists in `AGENT_EVENT_TYPES` (agent-specific only, not SystemEventTypes) |
| RecallUsed | ✅ | `SystemEventTypes.RECALL_USED` emitted at the planning and execution recall sites via `core/execution_recall.py` (Gap 1, 2026-07-08). |
| StepStarted | ✅ | `SystemEventTypes.FLOW_NODE_STARTED` / `AGENT_STEP` |
| ToolCalled | ⚠️ | Tool execution emits via `queue_system_event()` in `tool_registry.py`; not a named SystemEventType |
| StepCompleted | ✅ | `SystemEventTypes.FLOW_NODE_COMPLETED` / `AGENT_STEP_COMPLETED` |
| StepFailed | ✅ | `SystemEventTypes.FLOW_NODE_FAILED` / `AGENT_STEP_FAILED` |
| MemoryWritten | ✅ | `SystemEventTypes.MEMORY_WRITE` |
| ScoreComputed | ✅ | `SystemEventTypes.SCORE_COMPUTED` emitted per run via `core/execution_score.py` (Gap 3, 2026-07-08). |
| NextActionChosen | ❌ | Not emitted. Autonomy decisions emit `AUTONOMY_DECISION` but not as a post-run next-action. |
| ExecutionCompleted | ✅ | `SystemEventTypes.EXECUTION_COMPLETED` |
| ExecutionFailed | ✅ | `SystemEventTypes.EXECUTION_FAILED` |

**Gap:** Three events are missing from the event ledger: `RecallUsed`, `ScoreComputed`,
`NextActionChosen`. These are the events that would make the learning loop observable and
auditable. Without them, the system does improve but cannot explain that it is improving.

---

### 7. Memory Layer ✅ Confirmed (schema is implicit)

| Storable | Status | Evidence |
|---|---|---|
| Goal | ✅ | Captured as `node_type="outcome"` with content = objective |
| Plan | ✅ | `AgentRun.plan` (JSONB); plan steps captured in memory via capture engine |
| Inputs | ✅ | `initial_state` / `input_payload` captured in FlowRun/node content |
| Outputs | ✅ | `ExecutionLoop` writes result as `node_type="outcome"` |
| Errors | ✅ | `emit_error_event()` → SystemEvent; memory capture engine stores failures |
| Decisions | ✅ | `AUTONOMY_DECISION` events → memory capture |
| Tool usage | ⚠️ | Tool calls emit events but no structured `node_type="tool_usage"` schema |
| User feedback | ⚠️ | `MemoryFeedbackEngine.record_usage()` updates scores; no explicit feedback node type |
| Assumptions | ❌ | No structured capture of planner assumptions |
| Lessons learned | ⚠️ | `low_value_flag` marks low-success memories; no explicit `node_type="lesson"` |
| Reusable patterns | ⚠️ | `node_type="insight"` exists; no enforced capture of reusable patterns |

**Note:** Most of these are stored in node content + tags rather than as structured fields.
The schema is flexible but not enforced — applications can store anything; the runtime
doesn't mandate that every execution writes each of these types.

---

### 8. Recall Layer ✅ Confirmed

| Requirement | Status | Evidence |
|---|---|---|
| Similar past goals | ✅ | `MemoryOrchestrator.get_context()` → semantic vector search via pgvector |
| Similar failures | ✅ | Failure nodes stored with `success_rate < 0.3` flagged; recalled via semantic search |
| Relevant user/project context | ✅ | MAS path `/memory/{tenant}/{ns}/...` scopes recall to the tenant |
| Prior successful plans | ⚠️ | Plans are in `AgentRun.plan` (JSONB), not as memory nodes; not directly recallable via semantic search |
| Tool reliability history | ✅ | `success_rate` per memory node; `MemoryScorer` weights it at 0.20–0.25 |
| Known constraints | ⚠️ | No explicit `node_type="constraint"` — constraints would need to be written as memory nodes |
| Previously learned preferences | ✅ | `usage_count`, `success_rate`, `impact_score` on nodes influence ranking |
| Existing memory links | ✅ | `MemoryOrchestrator._inject_trace_context()` pulls nodes linked to the current trace |

**Gap:** Prior successful plans are not stored as recallable memory nodes — they live in
`AgentRun.plan` (a JSONB column on the agent run row). A planner cannot recall "what plan
worked last time for a similar goal" via semantic search without an explicit bridge that
writes plans to the memory graph.

---

### 9. Scoring Layer ✅ Confirmed (memory-node level; not execution level)

| Dimension | Status | Evidence |
|---|---|---|
| Success | ✅ | `MemoryLearningEngine.evaluate_result()` → 0.0–1.0 heuristic; `success_rate` persisted |
| Accuracy | ⚠️ | No separate accuracy dimension. `success_score` from result dict is a proxy. |
| Usefulness | ✅ | `impact_score` computed by `memory_capture_engine.calculate_impact_score()` |
| Cost | ❌ | No cost dimension. LLM token counts are not scored or stored per execution. |
| Latency | ⚠️ | `duration_ms` in syscall envelope and `ExecutionPipeline` metrics; not stored as a memory score |
| Reliability | ✅ | `failure_count` / `success_count` on `MemoryNodeModel`; `success_rate` derived |
| Confidence | ⚠️ | `semantic_score` (embedding similarity) serves as a confidence proxy |
| User satisfaction | ❌ | No explicit user satisfaction signal captured per execution |
| Tool performance | ⚠️ | Tool calls emit events; no per-tool success rate stored as memory score |
| Memory usefulness | ✅ | `recency`, `usage_frequency`, `graph_bonus`, `trace_bonus` all factor into `MemoryScorer` |

**Critical gap:** Scoring is applied at the **memory-node level**, not the **execution level**.
There is no single execution score record emitted after each run. `ANALYTICS_SCORE_UPDATED`
exists in `SystemEventTypes` but is not consistently emitted. Cost, user satisfaction, and
per-tool scores are absent.

> **RESOLVED 2026-07-08 (Gap 3, INFINITY-RUNTIME-1).** An execution-level score record is
> now emitted after each finished run: `core/execution_score.py` emits one `SCORE_COMPUTED`
> SystemEvent carrying `{run_id, score, status, dimensions[, duration_ms]}` at the agent-run
> terminal path (`agent_runtime/execution.py`, both `completed`/`failed`) and the generic
> `ExecutionLoop` (`runtime/memory_loop.py`). The event row is the durable, trace-queryable
> record — no schema table required. The scalar dimension is live (`compute_execution_score`);
> cost / user-satisfaction / per-tool dimensions remain future `dimensions{}` entries.

---

### 10. Learning Layer ✅ Confirmed (memory-node level; planning influence is indirect)

| Requirement | Status | Evidence |
|---|---|---|
| Future planning | ⚠️ | Scores update memory nodes; nodes are recalled into execution context; but not injected into the planner prompt unless the planner context provider does it explicitly |
| Tool selection | ⚠️ | Tool reliability history is in memory nodes but the planner selects tools based on its prompt, not from live memory scores |
| Retry behavior | ⚠️ | `low_value_flag` marks failed memories; no automatic retry-policy update from scores |
| Memory ranking | ✅ | `success_rate` + `usage_count` + `impact_score` directly feed `MemoryScorer` weights |
| Workflow recommendations | ❌ | No mechanism to recommend a different workflow based on past scores |
| Agent strategy | ⚠️ | `AgentCoordinator.decide_execution_mode()` can route differently; not score-driven |
| Risk detection | ⚠️ | `overall_risk` in agent plans; not derived from historical execution scores |
| Default execution policies | ❌ | No mechanism for scores to update default policies (timeout, retry budget, concurrency) |

**Gap:** The learning loop closes on **memory recall quality** (nodes that led to success get
recalled more in future). It does not close on **planning** (the planner does not see scores
as part of its input), **tool selection** (the LLM chooses tools from the prompt, not from
a score-ranked tool catalog), or **execution policies** (retry budgets and timeouts are
static configuration).

---

### 11. Next-Action Engine ❌ Not implemented as a first-class system

| Requirement | Status | Evidence |
|---|---|---|
| Done | ✅ | `run.status = "completed"` |
| Retry | ⚠️ | Manual retry (create new run); no automatic retry-based-on-failure path |
| Ask user | ⚠️ | Human approval gate exists pre-execution; no post-execution ask path |
| Escalate | ⚠️ | Dead-letter service handles stuck flows; not a user-facing escalation |
| Schedule follow-up | ⚠️ | `sys.v1.job.submit` can schedule work; nothing does it automatically post-run |
| Create memory | ✅ | `ExecutionLoop` writes result to memory after execution |
| Improve workflow | ❌ | No mechanism |
| Trigger another execution | ⚠️ | `_run_completion_hooks()` fires app-registered hooks; those CAN trigger another run but nothing in the kernel mandates it |
| Recommend next step | ❌ | No next-step recommendation engine |

**Gap:** There is no runtime-owned Next-Action Engine. After every execution, the runtime
records state and writes memory. What happens next is either determined by the flow graph
(next node), by an app-registered completion hook, or by nothing. The `AutonomousController`
evaluates triggers for future scheduled runs but does not operate as a real-time
post-execution decision engine.

---

### 12. Observability ✅ Confirmed (some views aspirational)

| Requirement | Status | Evidence |
|---|---|---|
| Logs | ✅ | Structured `logger.*` throughout; `[ClassName]` prefixes |
| Metrics | ✅ | Prometheus: `execution_duration_seconds`, `execution_total`, `ai_circuit_breaker_state`, `quota_redis_mode`, `flow_runs_dead_lettered_total` |
| Traces | ✅ | OTel spans on every `dispatch()` call; `trace_id` propagated via ContextVars |
| Event timeline | ✅ | `SystemEvent` table; `AgentEvent` table; queryable by `trace_id` |
| Run history | ✅ | `FlowRun`, `AgentRun`, `ExecutionUnit` tables |
| Failure dashboard | ⚠️ | `/health/deep` and dead-letter list exist; no dedicated failure dashboard UI |
| Tool usage dashboard | ⚠️ | Tool events emitted; no aggregated UI view |
| Memory writes dashboard | ⚠️ | `MEMORY_WRITE` events recorded; no dedicated UI view |
| Cost/latency dashboard | ⚠️ | `duration_ms` tracked; cost not tracked; no unified dashboard |
| Audit trail | ✅ | `AgentEvent` log (PLAN_CREATED, APPROVED, EXECUTION_STARTED, etc.) + `SystemEvent` |

---

### 13. Reliability ✅ Confirmed

| Requirement | Status | Evidence |
|---|---|---|
| Idempotent execution | ✅ | `EffectRecord` + `EXACTLY_ONCE` gate in `SyscallDispatcher` |
| Retries | ✅ | `node_max_retries` on Nodus execute; `max_attempts` on job submit |
| Timeouts | ✅ | `FLOW_WAIT_TIMEOUT_MINUTES`, `max_execution_ms` in `NodusRuntimeAdapter` |
| Cancellation | ⚠️ | No explicit cancellation syscall; status can be set externally |
| Resume from checkpoint | ✅ | WAIT/RESUME with DB-backed `FlowRun.status`; startup rehydration |
| Dead-letter queue | ✅ | `dead_letter_service.py` + `dead_letter` status on `FlowRun` |
| Stuck-run recovery | ✅ | `stuck_run_watchdog.py` + `stuck_run_service.py` |
| Health checks | ✅ | `/health/deep` checks syscall_registry, scheduler, event bus, domain health |
| Graceful shutdown | ✅ | `EventBus.stop()`, scheduler drain |
| Known failure modes documented | ✅ | `DEGRADED_MODE_MATRIX.md`, `EXECUTION_INVARIANTS.md`, `IDEMPOTENCY_CONTRACT.md` |

---

### 14. Extensibility ✅ Confirmed

| Requirement | Status | Evidence |
|---|---|---|
| Plugin manifest | ✅ | `aindy.extension.manifest/v1` ABI; `validate_extension_manifest_document()` |
| Tool registry | ✅ | `TOOL_REGISTRY` in `agents/tool_registry.py` |
| Capability registry | ✅ | `_capability_definitions` + `register_capability_definition()` |
| Agent registry | ✅ | `AgentCoordinator` + `register_or_update_agent()` |
| Workflow registry | ✅ | `FLOW_REGISTRY` in `runtime/flow_engine/registry.py` |
| Versioned plugin contracts | ✅ | `MANIFEST_ABI_V1`, `AGENT_TOOL_REGISTRATION_ABI_V1ALPHA1`, etc. |
| Safe plugin loading | ✅ | Three trust tiers; subprocess isolation for external callbacks |
| Plugin permissions | ✅ | 40 `INPROC_CAP_*` constants; denied capabilities logged in audit record |
| Plugin observability | ✅ | Bootstrap audit record: `used_capabilities`, `denied_capabilities` per module |

---

### 15. Security / Capability Control ✅ Confirmed (secrets gap)

| Requirement | Status | Evidence |
|---|---|---|
| Auth | ✅ | JWT via `auth_router.py`; bcrypt + key ring in `auth_service.py` |
| Authorization | ✅ | `SyscallContext.capabilities` checked on every dispatch |
| Per-tool permissions | ✅ | `required_capability` on every tool entry; `check_tool_capability()` enforced |
| Memory read/write permissions | ✅ | `memory.read`/`memory.write` capability gates; `TenantContext.assert_memory_path()` |
| API access control | ✅ | Route guards via `_route_guards` registry; `rate_limiter` |
| Secrets management | ❌ | Environment variables only; no rotation, no per-tenant key injection, no audit of access |
| Sandboxing | ✅ | Nodus VM blocks imports/exec/eval; container OCI runner in CI |
| Audit logs | ✅ | `AgentEvent` + `SystemEvent` tables; `EffectRecord` for idempotency audit |
| Human approval for risky actions | ✅ | `AgentTrustSettings`; `_requires_approval()` gates high-risk plans |

---

## 16. Final Maturity Test

| Claim | Verdict |
|---|---|
| Every execution path produces memory | ✅ **Yes** (async jobs opt-in, 2026-07-08). Agent paths: yes. Flow paths via auto-capture: yes. Async jobs: yes when `AINDY_ASYNC_JOB_LOOP_CLOSURE` is enabled (default off pending soak). |
| Every memory can influence recall | ✅ **Yes.** `MemoryScorer` uses `success_rate`, `impact_score`, `usage_count`, `recency` on every recall. |
| Every recall can improve planning | ✅ **Yes** (opt-in, 2026-07-08). `generate_plan` recalls memory and injects it into the planner prompt (runtime-owned, gated by `AINDY_PLANNER_MEMORY_INJECTION`, default off); recall is emitted as `RECALL_USED`. Default-on pending app-side soak. |
| Every plan runs through the same execution contract | ✅ **Yes** for agent runs. ⚠️ **Partially** for flows (no formal contract schema). |
| Every result is scored | ✅ **Yes** (execution-level, 2026-07-08). Each finished run emits one `SCORE_COMPUTED` record via `core/execution_score.py`; memory nodes are still separately scored by `MemoryLearningEngine`. Multi-dimension (cost/satisfaction) remains future work. |
| Every score changes future behavior | ✅ **Yes** — but only for memory retrieval ranking. Scores do not feed back into planning, tool selection, or policy updates. |

---

## Summary: What Is and Isn't Closed

### What is working end-to-end today

The loop **closes completely** on the agent execution path when the monolith plugin is
loaded and a planner context provider is registered that includes memory:

```
API request
  → AgentRun created (intent)
  → generate_plan() → LLM plan (plan)
  → _build_execution_memory_context() → MemoryOrchestrator.get_context() (recall into execution)
  → execute_agent_run_via_nodus() (execute)
  → SystemEvents emitted (observe)
  → memory_capture_engine auto-captures (memory write)
  → MemoryLearningEngine.update_after_execution() (score)
  → success_rate updates MemoryScorer weights (improve next recall)
```

### The five structural gaps

**Gap 1 — Recall → Planning link is broken. ✅ CLOSED 2026-07-08.**
~~Memory is recalled into execution context but not into the planning prompt.~~
Closed: `generate_plan` recalls memory (runtime-owned, objective-keyed) and injects it into
the planner prompt via `_build_planner_prompt`, gated by `AINDY_PLANNER_MEMORY_INJECTION`
(default off, opt-in). Both recall sites emit `RECALL_USED` (`core/execution_recall.py`).

**Gap 2 — The event ledger is missing three entries.**
`RecallUsed`, `ScoreComputed`, and `NextActionChosen` are not emitted as `SystemEventTypes`.
The learning loop improves but cannot explain itself. Fix: add these as named system events
with payloads describing what was recalled, what score was produced, and what was decided.

**Gap 3 — No execution-level score record. ✅ CLOSED 2026-07-08.**
~~`MemoryLearningEngine` scores recalled memory nodes, not the execution as a whole.
There is no single `{run_id, score, dimensions}` record written after each run.~~
Closed: `core/execution_score.py` emits a single `SCORE_COMPUTED` SystemEvent per finished
execution (`{run_id, score, status, dimensions[, duration_ms]}`) at agent-run completion and
in the generic `ExecutionLoop`. The event row is the durable record — no schema table.

**Gap 4 — Next-action engine does not exist as a runtime primitive.**
After every run the system records what happened. What should happen next is determined
by the flow graph or app-registered completion hooks — not by a runtime-owned decision.
There is no formal post-run decision: retry / ask_user / escalate / schedule_follow_up /
recommend. Fix: `_run_completion_hooks()` is the right attachment point; it needs a
contract return type that the runtime acts on.

**Gap 5 — Async jobs are outside the full loop. ✅ CLOSED 2026-07-08 (opt-in).**
~~Jobs submitted via `sys.v1.job.submit` do not automatically produce memory, trigger recall,
or get scored. They are fire-and-forget with an audit log.~~
Closed: gated by `AINDY_ASYNC_JOB_LOOP_CLOSURE` (default off), `_execute_job_inline` now
activates the async-execution context so its `EXECUTION_*` events (previously raised by the
contract gate and swallowed by `_emit_async_system_event`) persist and drive auto-capture —
jobs produce memory — and emits a per-job `SCORE_COMPUTED` record via the Gap-3 helper
(`_emit_async_job_score`). Recall-into-jobs is deliberately **not** wired (job bodies are
mostly infra — embedding ingestion, metric writing — where recall relevance is weak);
`SCORE_COMPUTED` does not require recalled ids. Opt-in until soaked, since it makes all job
workers write memory + score.

---

## Verdict

**You have most of the machine form. You do not yet have the closed loop.**

The infrastructure for every layer exists. The scoring engine is real. The recall engine
is real. The memory write is automatic on the agent path. The event ledger is 70% complete.
What is missing is the **wiring**: recall must flow into the planner, scores must emit as
named events, and the next-action decision must be a runtime-owned primitive rather than an
app convention.

When those five gaps are closed, every execution will automatically complete the loop and
the Infinity Runtime claim will be structurally true.
