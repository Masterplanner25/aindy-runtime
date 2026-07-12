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

### DUR-1 — Memory-effect boundary (keystone) — extends MEB — ✅ SHIPPED 2026-07-12
Add a **third `EffectRecord` chokepoint** at the direct-DAO memory writes so `remember()`, the
deferred `memory.write()`, and `share` dedup like the tool/syscall paths. Reuse the MEB primitive
(`kernel/effect_ledger.resolve_effect_record` / `complete_effect_record`) verbatim.

**Shipped shape (opt-in, `AINDY_MEMORY_IDEMPOTENCY`, default off).** The gate lives at the single
parent-side commit point `nodus_runtime_adapter._apply_deferred_memory_writes` (covers both
`memory.write` and `remember` deferred kinds). Keyed content-independently on **(run, node/segment,
ordinal)** via `_memory_effect_action_id(scope, ordinal)` — `scope = f"{execution_unit_id}:{effect_scope}"`
where `effect_scope` is the flow node name (threaded through `execute_nodus_runtime` from
`context["node_name"]`). **The per-node discriminator is load-bearing, not just for continuation:**
flow nodes share the run's `execution_unit_id`, so without it two sibling nodes writing memory would
collide on ordinal 0 and the second would be silently skipped — data loss on a *normal* run once the
flag is on. A ledger failure degrades to at-least-once; a failed write leaves the slot reclaimable.
Verified on real Postgres: a node re-run dedups (1 node, not 2), a sibling node at the same ordinal
does **not** collide, distinct ordinals don't collapse, and 4 distinct slots produce 4 effect_records.
Tests: `tests/unit/test_dur1_memory_effect_boundary.py`. **Deferred within DUR-1:** any *immediate*
(non-deferred) `remember` path, and populating `EffectRecord.execution_id` (needs a ledger param —
the MEB-1 follow-up).

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

### DUR-2 — Per-run at-most-once signal — ✅ SHIPPED 2026-07-12
Chose mechanism (a): an ambient contextvar. `kernel/effect_ledger.durable_effects_scope()` /
`durable_effects_active()` mark the current execution context as at-most-once; all three chokepoints
(memory `_apply_deferred_memory_writes`, syscall dispatcher gate, tool `execute_tool`) now fire under
`durable_effects_active()` **regardless of the per-tool/per-syscall `EXACTLY_ONCE` declaration or the
per-effect master flag** — the literal "broaden beyond `AGENT_HIGH_RISK`, declaration-free" work. Both
continuation drivers set the scope around the re-drive (`flow_continuation._dispatch_resume` wraps
`runner.resume`; `agent_continuation` wraps the resume callback via `_durable_resume`). No schema.
Verified on real Postgres: with `AINDY_MEMORY_IDEMPOTENCY` **off**, a re-applied write inside the
scope dedups (1 node), and outside the scope does not (2 nodes) — the signal is what engages it.
Tests: `tests/unit/test_dur2_per_run_at_most_once.py` + declaration-free cases in the syscall/tool
harnesses.

**Reach (both gaps closed by DUR-2b).** A contextvar stays within one execution context, so DUR-2
alone reaches **parent-side** effects (deferred memory writes) + **in-process** dispatches but not
the nodus worker subprocess; and the **agent-segment** path needed a stable per-segment scope. Both
are resolved by DUR-2b (below).

### DUR-2b — Subprocess propagation + stable per-segment scope — ✅ SHIPPED 2026-07-12
Two fixes that make the **agent / nodus-subprocess** continuation path fully at-most-once (the
prerequisite for a safe DUR-3):
1. **Subprocess propagation.** The parent writes `durable_effects=durable_effects_active()` into the
   nodus worker payload (`_execute`); the worker (`nodus_worker.main`) re-establishes
   `durable_effects_scope()` around `run_source`, so in-subprocess `sys()`/`call_tool()` effect gates
   dedup declaration-free.
2. **Stable per-segment memory scope** — and a **correction** to the DUR-2 note: agent segments do
   **not** get a fresh `execution_unit_id`; `_run_agent_segment_flow` passes `trace_id=correlation_id`,
   so **all segments share the run's `execution_unit_id`** AND run through the one constant
   `nodus.execute` node — meaning the DUR-1 scope `(eu:node:ordinal)` would **collide across segments**
   (silent memory loss), not merely be ineffective, once the gate is on. Fix: `_run_agent_segment_flow`
   threads `__effect_scope=agent_plan_seg<N>` via `extra_initial_state`; the flow node handler appends
   it (`_dur_effect_scope`) so each segment's scope is distinct and reproduced identically on a re-run.
   PG-verified: two segments writing at the same ordinal under the shared run eu no longer collide, and
   a segment re-run dedups. Tests: `tests/unit/test_dur2b_subprocess_and_segment_scope.py`. (Latent —
   agent memory gating is only reachable behind `AINDY_DURABLE_CONTINUATION` + a continuation-safe agent
   type, so this closed the hazard before DUR-3 can enable it.)

### DUR-2c — Gate immediate in-subprocess bridge writes — ✅ SHIPPED 2026-07-12
Verify-first for DUR-3 found a reach hole: `remember()`, `record_outcome()`, and `share()` are
`AINDYMemoryBridge` methods that write **immediately, in-subprocess, via a direct DAO** — they do
**not** go through the deferred list DUR-1 gates, so a continuation re-run would double-write them
(a duplicate memory node for the common `remember()`). DUR-2c closes it: `AINDYMemoryBridge` gains a
`run_scope` + a `_gate()` helper that dedups through the shared effect ledger, keyed
content-independently on `(run_scope, per-action ordinal)` with **cached-result replay** (a re-run's
`remember()` returns the *original* node id). Active only under the per-run at-most-once signal
(propagated into the subprocess by DUR-2b) or `AINDY_MEMORY_IDEMPOTENCY`. `remember` +
`record_outcome` are gated; **`share` is left ungated — setting an existing node to `shared` is
naturally idempotent.** The per-(run, segment) scope is threaded into the subprocess payload
(`effect_scope`). PG-verified: a re-run's `remember()` replays the same id (1 node, not 2); without
the signal it does not dedup. Tests: `tests/unit/test_dur2c_immediate_memory_gate.py`.

**With DUR-2c, all *runtime-mediated* effects on a continued run are at-most-once** (deferred memory,
immediate bridge memory, syscalls, tools — parent and subprocess). The only remaining re-fire is a
**raw, un-mediated side effect in arbitrary node code** (e.g. a node calling `requests.post` or
writing another table directly) — which the runtime fundamentally cannot gate. That is why DUR-3 is
an *opt-in* flip with an *opt-out* deny-list, not an unconditional default.

### DUR-3 — Flip continuation default-safe — ✅ SHIPPED 2026-07-12
The ECOGAP-1 headline. With runtime-mediated effects guarded per-run (DUR-1/2/2b/2c), the per-flow /
per-agent continuation-safe **declaration is no longer required for safety**. New opt-in flag
**`AINDY_DURABLE_CONTINUATION_ALL`** (default off): when on — alongside the master
`AINDY_DURABLE_CONTINUATION` — crash continuation covers **all** flows/agents **except** those on an
**opt-out deny-list** (`mark_flow_continuation_unsafe` / `mark_agent_type_continuation_unsafe`, for
flows/agents with raw un-mediated side effects the runtime can't dedup). Default off keeps the exact
current behavior (per-flow/per-agent declaration required). Both continuation drivers already wrap
the re-drive in `durable_effects_scope()` (DUR-2), so a default-safe continuation's effects are
at-most-once. Permission is a small helper on each side (`_flow_continuation_permitted` /
`_agent_continuation_permitted`). No schema. Tests:
`tests/unit/test_dur3_continuation_default_safe.py`.

**Staged rollout:** ship opt-in (done); flip the default after real-world soak. Raw un-mediated
side effects remain the operator's responsibility via the deny-list — the runtime guarantees
at-most-once only for effects that pass its boundary (memory / syscalls / tools).

### DUR-4 — FlowHistory canonicalization + fold — ✅ SHIPPED 2026-07-12
Makes `flow_history` a deterministically ordered, fold-able event log — the program's **one
schema-contract bump** (`2026-07-11`→`2026-07-12`, Alembic `0012`, head `0011`→`0012`): a nullable
monotonic **`sequence_number`** per `flow_run` + index `ix_flow_history_run_seq`, populated by the
runner writer (`max()+1`, safe because a run's nodes execute sequentially and it continues across a
resume). `core/flow_history_fold.py` reconstructs `FlowRun.state` from the ordered rows — the last
row's full `input_state` checkpoint with its `output_patch` applied **only on SUCCESS** (shallow
merge, parity with the engine's per-status apply). An **opt-in** resume repair
(`AINDY_DURABLE_FOLD_REPAIR`, default off) rebuilds a lost/torn snapshot from the fold before
resuming — the last history row commits *before* the snapshot advance, so it is at least as fresh
for the last completed node (`current_node` is a separate column, unaffected). Verified on real
Postgres: column + index materialize, the folder reconstructs across out-of-order rows honoring the
WAIT-no-apply rule, and Alembic `0012` adds/drops cleanly. Tests:
`tests/unit/test_dur4_flow_history_fold.py`. **Deferred (audit-completeness only, non-critical because
each row's `input_state` is a full pre-image the fold anchors on):** a first-class genesis row,
capturing the exception-failure path as a terminal row, and closing the out-of-band state-injection
write gaps (event-payload / trace injection).

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

- **DUR-1 (memory-effect boundary) — ✅ SHIPPED 2026-07-12.** Opt-in `AINDY_MEMORY_IDEMPOTENCY`;
  position-keyed dedup at `_apply_deferred_memory_writes`; PG-verified. The keystone/standalone win.
- **DUR-2 (per-run at-most-once signal) — ✅ SHIPPED 2026-07-12.** `durable_effects_scope()` contextvar;
  all three chokepoints honor it (declaration-free); continuation drivers set it; PG-verified.
- **DUR-2b (subprocess propagation + stable per-segment scope) — ✅ SHIPPED 2026-07-12.** Threads the
  signal into the nodus subprocess payload; adds a per-segment memory-scope discriminator (fixing a
  cross-segment collision on the agent path). PG-verified.
- **DUR-2c (gate immediate bridge writes) — ✅ SHIPPED 2026-07-12.** `remember`/`record_outcome` (immediate,
  in-subprocess, direct-DAO) now dedup through the ledger with cached-id replay; `share` is naturally
  idempotent. PG-verified. **All runtime-mediated effects on a continued run are now at-most-once —
  DUR-3 is unblocked** (only raw un-mediated node side effects remain, handled by DUR-3's opt-in +
  opt-out design).
- **DUR-3 (flip continuation default-safe) — ✅ SHIPPED 2026-07-12.** Opt-in
  `AINDY_DURABLE_CONTINUATION_ALL` makes continuation cover all flows/agents except an opt-out
  deny-list. **The ECOGAP-1 headline — transparent crash continuation without per-flow declaration —
  is delivered** (opt-in; flip the default after soak).
- **DUR-4 (FlowHistory canonicalization + fold) — ✅ SHIPPED 2026-07-12.** `sequence_number` column
  (the program's one schema bump, Alembic `0012`) + `flow_history_fold.reconstruct_flow_run_state` +
  opt-in `AINDY_DURABLE_FOLD_REPAIR` torn-snapshot recovery. PG-verified.

**★ ECOGAP-1 Phase 3 (Durable Execution) is COMPLETE** — DUR-1 → DUR-4 all shipped. Transparent
crash continuation without per-flow declaration, with at-most-once runtime-mediated effects and an
event-sourced fold for torn-snapshot recovery. All opt-in/default-off; one additive schema bump.
Remaining is soak-then-flip-defaults, not new build. Release remains on hold past v1.6.2.

## Cross-references

- TECH_DEBT: `ECOGAP-1` (this program is its Phase 3), `IDEM-10` / MEB (the effect substrate),
  `REPLAY-1` (the clock — deliberately *not* extended here).
- Program docs: `MEDIATED_EFFECT_BOUNDARY_PROGRAM.md` (the effect ledger this reuses).
- Key files: `runtime/flow_engine/runner.py`, `runtime/flow_engine/runner_steps.py`,
  `core/flow_continuation.py`, `core/agent_continuation.py`, `kernel/effect_ledger.py`,
  `nodus/runtime/memory_bridge.py`, `runtime/nodus_runtime_adapter.py`, `db/models/flow_run.py`.
