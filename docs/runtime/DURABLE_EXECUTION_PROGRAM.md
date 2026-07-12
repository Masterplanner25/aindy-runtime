---
title: "Durable Execution — ECOGAP-1 Phase 3 program plan"
api_version: "1.0"
last_verified: "2026-07-12"
status: current
owner: "platform-team"
---

# Durable Execution (ECOGAP-1 Phase 3) — program plan

A source-verified plan for the last and largest piece of ECOGAP-1: **transparent crash
continuation without a per-flow/per-agent safety declaration.** Phases 1/2/2a shipped an
opt-in, idempotent-only, snapshot-based continuation (2026-07-08); this program removes the
"idempotent-only" gate so continuation is safe by default.

This doc is the durable home for the plan. It **supersedes** the inline "Phase 3" prose in
the `ECOGAP-1` TECH_DEBT entry, which now points here.

## The thesis — and the reframe

The TECH_DEBT note framed Phase 3 as three pillars: (A) fold `FlowHistory` as the canonical
state source, (B) thread the REPLAY-1 clock through the execution hot paths for
**deterministic event-sourced replay**, and (C) broaden `EffectRecord`/`execution_guarantee`
beyond `AGENT_HIGH_RISK` for declaration-free continuation.

A four-front source audit (2026-07-12) shows the weighting is wrong:

- **(B) kernel-level deterministic replay is out of scope** — wrong layer, unnecessary, and
  huge. It is unnecessary because **continuation resumes *forward* from the last completed
  node/segment; it never re-executes completed code**, so there is nothing whose determinism
  must be preserved. Chasing it anyway would mean threading a recorded clock + a seeded id
  source through ~20 `uuid4` hot-path sites and across a subprocess boundary the ContextVar
  cannot reach — and it **contradicts the codebase's own design rule** ("*Determinism/replay
  stays a VM concern, never a kernel concern — Temporal design rule*",
  `ECOSYSTEM_CAPABILITY_GAPS.md:109`). Determinism, if ever needed, is a Nodus/VM concern.
- **(A) the FlowHistory fold is optional robustness** — the `FlowRun.state` snapshot already
  delivers "continue from the last node" and works today. The fold buys torn-snapshot
  resilience and an event-sourced audit log; it is not on the critical path.
- **(C) the effect boundary *is* Phase 3.** The one node that re-runs on resume must produce
  **at-most-once effects**. This is an effect-boundary problem — a direct extension of the
  Mediated Effect Boundary (MEB) program — not a replay problem. And the audit shows it is
  **not** a flip-the-default: the effects that dominate the continuation hot path bypass every
  existing `EffectRecord` chokepoint.

So Phase 3 is: **make the single re-running node/segment idempotent by default via the MEB
effect ledger, then drop the declaration gate.**

## Verified current reality (source-audited 2026-07-12)

### Continuation re-runs exactly one node — and only its effects are unsafe
`PersistentFlowRunner.resume()` (`runtime/flow_engine/runner.py:219`) reloads `current_node`
(`:261`) and `state` (`:252`) and re-drives the node loop. The post-node checkpoint —
`run.current_node = next_node; run.state = _json_safe(state); db.commit()` — is at
`runner_steps.py:367-369`, and is a **separate transaction from the node's own effects**. The
node body (`node_fn(state, context)`, `node_executor.py:34`) commits its side effects first,
then FlowHistory commits (`runner.py:346`), then the advance commits (`runner_steps.py:369`).
**A crash between the effect commit and the advance leaves `current_node` un-advanced**, so on
restart the runner re-runs that one node's entire body. All prior nodes are safe (their deltas
are in the committed `state` snapshot). This single re-run is the whole risk surface.

`core/flow_continuation.py` (`try_continue_flow_run`) and `core/agent_continuation.py`
(`continue_crashed_agent_runs`) are startup-only, behind `AINDY_DURABLE_CONTINUATION` (default
off), gated to explicitly-declared continuation-safe flows/agents (`CONTINUATION_SAFE_FLOWS` /
`CONTINUATION_SAFE_AGENT_TYPES`, both empty by default), with a 3-attempt dead-letter. They
re-claim atomically (`UPDATE … WHERE status IN (running,executing)`) and re-drive on a daemon
thread. The declaration gate exists **only because the re-run node's effects are not
idempotent** — remove that unsafety and the gate can go.

### The dominant effects bypass every EffectRecord chokepoint
The MEB gates are shipped but nearly inert (**0 tools** and **1 syscall** — `sys.v1.memory.write`,
`syscall_registry.py:1441` — declare `EXACTLY_ONCE`; both master flags default off). Worse, the
memory-write idioms that a nodus flow/agent actually emits **do not reach either gate**:

| Effect path | Routes to | Gated? |
|---|---|---|
| `sys("sys.v1.memory.write", …)` | dispatcher gate | gated (flag + EXACTLY_ONCE) |
| `call_tool(name, args)` | `execute_tool` (MEB-0) | gated path, but 0 tools opt in |
| **`remember(...)`** | `AINDYMemoryBridge.remember` → `MemoryNodeDAO.save()` (`nodus/runtime/memory_bridge.py:109`) | **BYPASS** |
| **deferred `memory.write(...)`** | collected, committed by `_apply_deferred_memory_writes` → `dao.save()` (`runtime/nodus_runtime_adapter.py:341-348`) | **BYPASS** |
| `share`, `record_outcome` | direct DAO | **BYPASS** |

So on a continuation re-run, the common case — a node that called `remember()` or
`memory.write()` — **writes a duplicate memory node**. This is IDEM-10 generalized: the
continuation hot path's real-world effects mostly transit no idempotency boundary.

### The per-run guarantee seam exists but is orphaned
`core/retry_policy.py:65-71` maps `AGENT_HIGH_RISK` → `execution_guarantee="EXACTLY_ONCE"`, and
`execution_gate.py:201/241` writes it onto `eu.extra["retry_policy"]`. But **nothing reads it to
gate an effect** — MEB-1b deliberately gates on the addressable `SyscallEntry.execution_guarantee`
instead (`syscall_dispatcher.py:210-213`). So the per-EU guarantee is a decorative marker today.
It is, however, the natural *per-run* seam a continuation driver could set to mean "gate every
effect on this run."

### FlowHistory is near-fold-able but audit-only
`flow_history` (`db/models/flow_run.py:62-78`) is per-node append-only; each row already carries
`output_patch` (the real state delta applied via `state.update(patch)` at `runner_steps.py:257`)
**and** `input_state` (a full pre-node checkpoint). The single writer is `runner.py:335-346`. But
it is read only by audit/serialization consumers — live execution rehydrates from `FlowRun.state`
(`runner.py:252`), never the log. It is **not** a canonical fold source yet: out-of-band state
mutations bypass it (`event_router.py:34-36` event injection; `runner.py:159-161/253-257`
trace/root-event injection), there is no genesis row and no monotonic `sequence_number` (only
`created_at`), and a hard-crashed node writes **no** row (`runner_steps.py:117-139` /
`runner.py:316` short-circuits before the write). It is also create_all-managed, not
alembic-tracked (like `memory_nodes`).

## The program — phases (prefix `DUR-*`)

Dependency-ordered. **DUR-1 is the keystone and a standalone win**; the core (DUR-1→DUR-3) needs
**no schema change** — the only bump lives in the optional DUR-4 (same shape as MEB, where only
MEB-3b touched schema). Every phase is opt-in / default-off and verified on throwaway Postgres.

### DUR-1 — Memory-effect boundary (keystone) — extends MEB
Add a **third `EffectRecord` chokepoint** at the direct-DAO memory writes so `remember()`, the
deferred `memory.write()`, and `share` dedup like the tool/syscall paths. Reuse the MEB primitive
(`kernel/effect_ledger.resolve_effect_record` / `complete_effect_record`) verbatim.

- **Dedup key = (run, step) identity, NOT content.** Key `action_id` on
  `compute_action_id(action_type="memory.write", input_payload={…stable…}, scope=<eu_id>:<step_id>)`
  using the currently-unused `EffectRecord.step_id` column. Keying on run+step (not a content
  hash) means a re-run of the same step dedups **even if the content carries a fresh
  uuid/timestamp** — sidestepping the whole determinism problem for effects.
- **Two seam locations** (the subprocess/deferred split): `_apply_deferred_memory_writes`
  commits in the parent process (easy — full DB + ledger access); `remember()` runs in the nodus
  worker subprocess (`nodus_worker.py`) and needs `eu_id`/`step_index` threaded in (the worker
  already receives `run_id`/`execution_token`, so the channel exists).
- Naturally **populates `EffectRecord.execution_id`** (the deferred MEB-1 follow-up) since we now
  have a per-run consumer.
- Opt-in behind a flag (mirrors `AINDY_TOOL_IDEMPOTENCY`); no schema.
- **Standalone win:** dedups memory writes on *any* retry, not only continuation — closes the
  largest remaining IDEM-10 bypass.

### DUR-2 — Per-run at-most-once signal
Reconnect a **per-EU/per-run guarantee** that all three chokepoints consult, so the re-running
node's syscalls/tools/memory-writes dedup **without each declaring `EXACTLY_ONCE`**. Two candidate
mechanisms (decide during design): (a) an ambient contextvar the continuation driver sets before
re-driving; (b) revive the orphaned `eu.extra["retry_policy"]["execution_guarantee"]` read for
continued runs only. This is the literal "broaden beyond `AGENT_HIGH_RISK`, declaration-free" work.
No schema. Depends on DUR-1.

### DUR-3 — Flip continuation default-safe
With effects guarded per-run, **invert/remove the continuation-safe declaration gate** so
continuation covers all flows/agents — first still behind the master `AINDY_DURABLE_CONTINUATION`
flag, then default-on after soak. This is the ECOGAP-1 headline. No schema. Depends on DUR-2.

### DUR-4 — FlowHistory canonicalization + fold (optional robustness)
Make the event log a true fold source: close the out-of-band write gaps, add a **genesis row + a
monotonic `sequence_number`** (indexed with `flow_run_id` — **the program's one schema-contract
bump**), capture the exception-failure path, and write a folder that honors per-status apply rules
(SUCCESS→`update`, WAIT/FAILURE→no-apply) + shallow merge. Value: torn-snapshot recovery +
event-sourced audit. **Not on the critical path** — do only if we want belt-and-suspenders beyond
the `FlowRun.state` snapshot. Independent of DUR-1→3.

### Out of scope — kernel deterministic replay
Threading the REPLAY-1 clock + a seeded id source through the kernel execution hot paths.
Rejected: wrong layer (VM/Nodus concern per the design rule), unnecessary (forward-resume + data
fold, not code re-execution), and disproportionate (~20 `uuid4` sites + an unreachable subprocess
boundary + an inherently non-deterministic wall-clock subprocess timeout). If deterministic
single-node re-execution is ever wanted, it is tracked as a Nodus-layer item, not here.

## Decisions

1. **Continuation resumes forward; it does not re-execute completed work.** Therefore the
   correctness lever is *at-most-once effects on the one re-run node*, not deterministic replay.
2. **Effect dedup keys on run+step identity, not content** — deterministic across re-runs by
   construction, so effect safety does not depend on node-output determinism.
3. **Only DUR-4 bumps the schema;** DUR-1→DUR-3 are logic on existing tables (reusing
   `EffectRecord`, the unused `step_id`, and the existing continuation drivers).
4. **A partial complement, not a substitute:** the torn-write window can also be *shrunk* by
   folding the `current_node` advance into the same commit as the FlowHistory write. Cheap; do it
   opportunistically in DUR-3, but the effect boundary is what makes re-run *safe*.

## Size, risk, verification

A multi-PR program on the kernel's most correctness-sensitive path, but **materially smaller than
the original framing** (deterministic replay dropped). Reuses the MEB primitive and the existing
continuation drivers — mostly "add a chokepoint + a per-run signal + flip a gate," not greenfield.

- **PG-only hazards** (#157 transaction-poisoning; JSONB; pgvector) reproduce only on real
  Postgres — every phase verified against throwaway `pgvector/pgvector:pg16`, per the MEB recipe.
- **Risks:** (1) the subprocess/deferred memory-write split (two seams); (2) making `remember()`'s
  in-subprocess DAO write reach the ledger with the right scope; (3) ensuring the per-run signal
  degrades open on ledger failure (never blocks a real run) — the MEB `resolve_effect_record`
  already does this.
- Relationship to MEB: this is the **consumer** that the MEB substrate was built for — it turns
  the idempotency primitive into declaration-free durable execution.

## Progress / next

- **DUR-1 (memory-effect boundary)** — the keystone and standalone win; start here.
- DUR-2 → DUR-3 follow in order; DUR-4 is optional/independent.
- All opt-in/default-off; release remains on hold past v1.6.2.

## Cross-references

- TECH_DEBT: `ECOGAP-1` (this program is its Phase 3), `IDEM-10` / MEB (the effect substrate),
  `REPLAY-1` (the clock — deliberately *not* extended here).
- Program docs: `MEDIATED_EFFECT_BOUNDARY_PROGRAM.md` (the effect ledger this reuses).
- Key files: `runtime/flow_engine/runner.py`, `runtime/flow_engine/runner_steps.py`,
  `core/flow_continuation.py`, `core/agent_continuation.py`, `kernel/effect_ledger.py`,
  `nodus/runtime/memory_bridge.py`, `runtime/nodus_runtime_adapter.py`, `db/models/flow_run.py`.
