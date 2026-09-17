---
title: "Durable State Ownership Contract"
api_version: "1.0"
last_verified: "2026-09-16"
status: current
owner: "platform-team"
---

# Durable state ownership contract

**`ORCHESTRATOR-SPLIT-1` (b).** This document states, for every place durable *work* state
lives, **which store is authoritative for what, who writes it, who recovers it, on what
trigger, and what it must never do to another store's state.** It describes HEAD as
measured on 2026-09-16; nothing here is aspirational, and the two places where the answer is
"nobody" say so (§4, §6). Where a section names a defect it cites the entry that owns it.

The entry's premise is that durable work state lives in several stores with no shared
transaction, so there is no single answer to *"what work remains after a crash"*. **That
premise is true and the conclusion the entry drew from it is not:** there is no shared
transaction, but there is a **single authority** for each unit of work, and the stores that
are not authoritative are either *derived* (rebuilt from the authority on boot) or
*write-only* (nothing reads them back). The split is a design, and this is its contract.

---

## 1. The rule

> **For any unit of work, exactly one store is authoritative for its status. Every other
> store's view of that unit is derived from the authority or is advisory. Recovery re-derives
> from the authority; it never reconciles two stores.**

"Authoritative" means: the status row that `execute`, `resume`, `fail` and every watchdog
read before acting, and the row a CAS guards. "Derived" means: rebuilt from the authority at
boot (`rehydrate_*`), with no state of its own that survives a restart. "Advisory" means: it
may be consulted, it is never a precondition, and its absence changes nothing.

---

## 2. The stores

| # | Store | Backing | What it holds | Class |
|---|---|---|---|---|
| 1a | **`flow_runs`** + `flow_history` | Postgres | a flow's status, `current_node`, JSON `state`, `graph_signature`; one history row per completed node | **authority** for flow work |
| 1b | **`agent_runs`** + `agent_steps`, `agent_events` | Postgres | an agent run's status, plan, `wait_state`; one step row per executed step | **authority** for agent work |
| 1c | `execution_units` | Postgres | the unit a request / job / flow / agent bound; status, ceilings, `env_applied` | **derived** — finalised by whoever established it (`QUOTA-ACCRUAL-ORPHAN-1`); never a recovery source since `rehydrate_waiting_eus` was removed (2026-09-16) |
| 1d | `effect_records` | Postgres | one row per mediated effect: key, status (`pending/success/failed/partial/unknown`) | **ledger**, not a work store — the thing that makes *re-executing* work from 1a/1b safe (`DUR-2`, `IDEMPOTENCY_CONTRACT.md`) |
| 2a | **`job_logs`** | Postgres | an async job's status, `attempt_count` / `max_attempts`, payload | **authority** for async-job work |
| 2b | distributed queue (`aindy:jobs:*`) | Redis, or in-process | ready / delayed / in-flight (visibility timeout) / dead-letter messages | **transport** for 2a and for scheduler resumes (`run_id` + `eu_type`, rebuilt by `resume_reconstruction.resume_context`) — a message is never the authority for the work it names |
| 2c | scheduler `_waiting` + event-bus buffer | in-memory (+ Redis pub/sub) | live wait callbacks; a wake by `run_id` | **derived** — rebuilt at boot from 1a/1b (`rehydrate_waiting_flow_runs`, `rehydrate_waiting_agent_runs`); the bus never carries a payload (DEC-013) |
| 3 | `.nodus/graphs/` | files under `NODUS_RUN_STATE_ROOT` | nodus `task_graph` checkpoints for a guest workflow | **write-only from the runtime** — same location and lifecycle as store 4 since nodus `#476`/`#585` |
| 4 | `nodus_lang_workflow` store | SQLite under `NODUS_RUN_STATE_ROOT` (declared by `declare_guest_state_environment`, #611) | the guest's own run records: claim, wait, retry, status | **write-only from the runtime**, and — see §4 — **unrecovered by anyone** |
| 5 | `nodus_workflows` | Postgres | registered guest workflow *definitions* (source, version, capabilities) | a registry, not run state; `rehydrate_nodus_workflows` recompiles it at boot; listed so nobody mistakes it for store 4's Postgres twin |

`background_task_leases` (LEASE-1) is a *coordination* row, not work state; its contract is
`LEASE_FENCE_DESIGN.md`.

---

## 3. Per authority: who writes, who recovers, on what trigger

### 3a. Flow work — `flow_runs`

| | |
|---|---|
| **Status machine** | `pending → executing → waiting → completed / failed` (`FlowRun.status`); no `waiting → completed` edge (DEC-021) |
| **Writers** | `PersistentFlowRunner` on the runner's session, one commit per node; `route_event` writes `state["event"]` **before** the wake (`WAIT-PAYLOAD-PATH-1`) |
| **CAS** | `_claim_waiting_run` (`runner_steps.py:29`) — the only way a `waiting` row becomes `executing` |
| **Boot** | Phase 14 `rehydrate_waiting_flow_runs` re-registers every `waiting` row's callback (store 2c is derived from here); `graph_signature` mismatch → quarantine, never resume (`FLOW-GRAPH-SIGNATURE-1`) |
| **Periodic** | `recover_stuck_flow_runs` (5 min), `expire_timed_out_wait_flows` (60 s) — both go through the CAS |
| **Crash mid-node** | `DUR-4`: the `FlowHistory` fold resumes forward from the last committed node; **never re-executes code**, which is why kernel replay was declined (DEC-019) |
| **Cross-instance** | a resume crosses the queue as `run_id` + `eu_type` and is rebuilt from the row (`resume_reconstruction`); the fixed `SESSION-COMMIT-1` instance is the one that used to move only the unit |

### 3b. Agent work — `agent_runs`

| | |
|---|---|
| **Status machine** | `pending_approval → approved → executing → waiting → completed / failed`; `approve_run` is the only CAS (`pending_approval → approved`) and the path **bypasses the dispatcher** — no effect-gate idempotency on approve |
| **Writers** | `execute_run` (own session; `executing` at `execution.py:178`); the nodus adapter writes `agent_steps` per step after its retry loop |
| **Boot** | `rehydrate_waiting_agent_runs` (waits); then `continue_crashed_agent_runs` (`AINDY_DURABLE_CONTINUATION`, default off) re-drives an `executing` row from its last *fully completed segment* — a partially executed segment restarts from step one (`RECOVERY-GRANULARITY-1`; safe because 1d dedups the mediated effects, costly because the LLM calls are re-issued) |
| **Periodic** | `_recover_orphaned_approved_runs` (5 min, `approved` older than 10 min) re-dispatches `execute_run`, whose entry guard is a read-then-set that assumes **one leader** — `LEASE_FENCE_DESIGN.md` §2 |
| **Resume** | `agent_execution` resolves for resume only, never into the public `FLOW_REGISTRY` (DEC-020) |

### 3c. Async-job work — `job_logs`

| | |
|---|---|
| **Status machine** | `pending → running → completed / failed / deferred`; `attempt_count` is numbered **before** the handler lookup and restored after a rollback (`ASYNC-JOB-UNREGISTERED-STORM-1`); an unregistered handler is terminal |
| **Transport** | thread mode: an in-process future, nothing durable; distributed mode: store 2b, where the DB-side atomic claim is the primary guard and the visibility-timeout re-enqueue is the backstop (`distributed_queue.py:176`) |
| **Boot** | thread mode only: `recover_orphaned_thread_jobs` re-dispatches every `pending`/`running` row — safe *only* at boot, because the executor has no live futures yet; a periodic scanner cannot tell a long job from a dead one |
| **Periodic** | `deferred_async_job_retry` (1 min) re-dispatches `deferred` rows; distributed mode: the worker's `requeue_stale_jobs` re-enqueues in-flight messages past their visibility timeout |
| **Dead letter** | 2b's DLQ keeps the original payload; `dlq.drained` is an audit event; a message in the DLQ names a 2a row that is still the authority for whether the job *happened* |

---

## 4. ★★ Stores 3 and 4 — write-only, and recovered by nobody

The entry's concrete failure mode is: a `.nd` workflow "can be resumed by either layer", so
after a crash the host re-runs a segment from step one while the guest independently
rehydrates its own claim/wait/retry state, and the two disagree undetected.

**Measured at HEAD, that requires an actor that does not exist.** Three facts, each already
established elsewhere and collected here because together they are the contract:

1. **The runtime never reads store 4.** Zero references to `WorkflowStore`, `get_run`,
   `claim_run` or the store's records under `AINDY/` (`WORKFLOW_STORE_DECLARATION_PROPOSAL.md`
   §"write-only"). Guest scripts create records because the runtime appends `run_workflow(…)`
   to compiled flow-graph source (`nodus_execution_service.py:566`, `:1238`); the host resumes
   the *flow* through 3a and never asks the guest what it recorded.
2. **The guest's only autonomous actor cannot resume.** nodus's auto-sweep thread calls
   `expire_wait_timeouts()` without `release_schedules=True` — it can dead-letter a waiting
   record and *"has no way to resume anything"* (nodus `#733`); the `sweep()` that resumes
   needs a `vm_factory` nobody supplies. And since #611 the worker declares
   `NODUS_WORKFLOW_AUTOSWEEP=0`, so even that thread is absent.
3. **Each execution is a fresh guest.** A resumed flow node re-runs its script in a new VM
   (`DEC-012`: a resumed script is *not* seeded with its prior output state), which creates a
   *new* store-4 record rather than claiming the old one.

So after a crash there is no second layer *acting*: the host re-drives from 3a, the guest
writes a new record, and the old record sits in store 4 as an orphan (`running`, `claim:
null` — the two the entry's 2026-09-09 census found, "unreapable" because `running` is not a
terminal status). **That is two records of one run, not two executions of it**, and the
disagreement is confined to a store nothing reads.

**The contract for stores 3 and 4, therefore:**

- **Authority for a guest workflow's progress is store 1a**, via the flow node that ran it.
- **Stores 3 and 4 are advisory** — useful for a guest-side `nodus workflow …` inspection,
  never a precondition for anything the host does, and **never resumed by the host**.
- **Nothing may add a guest-side resumer** (a `vm_factory` to `sweep()`, a re-enabled
  autosweep, a runtime call into `WorkflowRunner.resume`) without first choosing which of 1a
  and 4 is authoritative for the *same* run — because that is the moment the entry's failure
  mode becomes reachable. Today it is not.
- **Their durability is declared, not guaranteed**: `NODUS_RUN_STATE_ROOT` behind a volume in
  compose (#611); a deployment that omits the volume loses advisory state and no work.

---

## 5. The seams — where two stores describe one unit

| Seam | Rule at HEAD |
|---|---|
| **1a ↔ 1c** (flow run ↔ its execution unit) | the runner finalises its own unit (`finalize_flow_unit`); a request's unit **cannot** enter `waiting` by any path (`EU-WAIT-SIGNAL-DEAD-1`); the unit is a *budget and audit* record, never consulted to decide resumption |
| **1b ↔ 1a** (agent run ↔ the flow that executes it) | the agent run is the authority; `agent_execution` is the flow shape it runs through and is resolved for resume only (DEC-020); a failure after a resume must reach the `AgentRun`, which `AUTHORITY-NEGOTIATION-1` phase 2 fixed |
| **2a ↔ 2b** (job log ↔ queue message) | the row is the authority; a message is a *claim to run it*; an unresolvable message must never ACK as success (`FR-15`'s four silent losses were all this) |
| **1a/1b ↔ 2c** (row ↔ live wait) | 2c is rebuilt from the rows at boot and is empty before Phase 14 — events that arrive during boot are buffered and drained after (`INV-EVENT-002`) |
| **1a ↔ 3/4** (flow node ↔ guest store) | §4: the row wins; the guest store is advisory |
| **any ↔ 1d** (work ↔ effect ledger) | re-executing work is safe iff its mediated effects hit the ledger; a work store's recovery may re-run code, and the ledger is what stops the effect firing twice — this is the one cross-store guarantee, and it holds only inside the `EXACTLY_ONCE` gate's scope (`IDEM-11`: a retry within one run replays; two runs are two effects) |

---

## 6. Failure domains — who recovers what

| Failure | Recovered by | Not recovered |
|---|---|---|
| **API process crash** | boot: Phases 12–14 (stuck runs, rehydration, continuation if enabled); leader-only jobs on the next leader | in-flight request EUs (rolled back — a request's unit finalises with the request or not at all) |
| **Worker process crash** (distributed) | `requeue_stale_jobs` after the visibility timeout; 1a/1b rows resume from the row | store 3/4 orphans (advisory) |
| **Worker process crash** (thread mode) | `recover_orphaned_thread_jobs` at *next boot only* | a job orphaned mid-run stays `running` until that boot |
| **Redis loss** | queue falls back to in-memory (`QUEUE-DURABILITY-CLASS-1`; `AINDY_REQUIRE_REDIS` makes it raise instead); the event bus opens its circuit and `/health/deep` reports it | messages in Redis at the moment of loss — the rows they named are still the authority and the periodic jobs re-find them |
| **Postgres loss** | nothing — every authority is here; the runtime degrades `UNSAFE_DEGRADED` and stops claiming work | — |
| **Guest subprocess crash** | the flow node fails → 3a's retry policy | the guest's own store-4 record (orphan, advisory) |
| **Leadership split** (two leaders) | expiry, then the fence when built (`LEASE_FENCE_DESIGN.md`) | the window in §2 of that design |

---

## 7. What this contract decides about the entry's option (a)

Option (a) — *"implement `WorkflowStore` over Postgres and inject it, collapsing store 4 into
store 1"* — is bounded work against a small abstract surface, and the entry keeps it open as
the only choice that makes the crash-overlap moot. **§4 shows the overlap is already moot, for
a different reason: the runtime does not read the store it would be collapsing.** Moving a
write-only advisory store into Postgres would give the authority table a second copy of runs
it already tracks, written by the guest, read by nobody, and reaped by nothing (`running` is
non-terminal there too).

**DECIDED 2026-09-16 (DEC-039, #707): (a) is DECLINED at HEAD; the trigger that reopens it is** the first
time the runtime wants to *read* guest run state (a guest-side resume, a `nodus workflow`
inspection surfaced in the operator console, or `RECOVERY-GRANULARITY-1` choosing to resume a
segment from the guest's step ordinal rather than the host's). At that point store 4 stops
being advisory, and (a) is the right shape for making it authoritative. Until then it is a
migration of state nothing consumes.

`ORCHESTRATOR-SPLIT-1` CLOSED on this document plus that decision; `QUEUE-DURABILITY-CLASS-1`
folded into §6's Redis row and closed with it.

---

## 8. Invariants this contract adds — ADOPTED as `EXECUTION_INVARIANTS.md` §7 (INV-OWN-001..003, 2026-09-16)

- **INV-OWN-001** — a unit of work has exactly one authoritative status row, and every recovery
  path reads it before acting (§1).
- **INV-OWN-002** — a transport message or live callback is never the authority for the work
  it names; losing one loses at most latency (§2, §5).
- **INV-OWN-003** — the host never resumes a guest workflow from the guest's store, and no
  guest-side resumer exists (§4). A test can pin the second half: the worker's declared
  environment carries `NODUS_WORKFLOW_AUTOSWEEP=0`, and `AINDY/` imports nothing from
  `nodus_lang_workflow` (no `WorkflowRunner`, no `WorkflowStore`, no `sweep` of the guest's —
  `resource_manager._sweep` is the unrelated TTL sweep) — a derived census, non-empty asserted
  on the environment side. **Pinned:** `tests/unit/test_durable_state_ownership.py`.
