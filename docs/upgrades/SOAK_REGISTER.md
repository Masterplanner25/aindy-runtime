---
title: "Soak Register — flags waiting on evidence from the app's stack"
api_version: "1.0"
last_verified: "2026-09-22"
status: current
owner: "platform-team"
---

# Soak register — flags waiting on evidence from the app's stack

**Why this file exists.** Eight `TECH_DEBT.md` entries end in *"soak, then flip"*. Every one of
them is finished code behind a default-off flag, and every one has been "waiting on soak" for
weeks without a definition of what a soak IS: which flag, on what, for how long, watching what,
and what result flips the default. `SUBSTRATE-WITNESS-1` records the cost — the flag backlog is
not blocked on courage or build effort; it is blocked on nobody having asked precisely. This
file is the precise ask. The app reports against it in its `RUNTIME_<version>_UPGRADE.md`; the
runtime flips a default in the release after the evidence lands, and the row moves to *flipped*.

**Rules of the register**

- **One flag at a time**, in the order below (blast radius ascending). A second flag goes on
  only after the first has its evidence. Two flags on at once and a regression cannot be
  attributed.
- **A soak is a stretch of ORDINARY traffic**, not a demo: the `MasterPlan` page, the daily
  Infinity jobs, whatever agents run anyway. Minimum: **7 days or 200 agent runs**, whichever
  comes second. The "what to watch" column is read at the start (baseline) and the end.
- **Evidence is a number or a row, never a sentence.** "Nothing broke" is the *absence* half;
  every row also names a *presence* signal — something that must have HAPPENED for the flag to
  have been exercised at all (variant 9: green because nothing was caught).
- **What counts as a failure** is per row. A failure does not close the ask; it is the best
  possible outcome of a soak and is filed as an FR with the row's signals attached.
- **Where to report:** the app's `RUNTIME_<version>_UPGRADE.md`, a subsection per flag, quoting
  the presence signal and the absence signal with dates. The runtime copies the result here.

---

## The order, and each row

### 1. `AINDY_MEMORY_RECALL_OWN_SESSION` — `DB-NODUS-BUDGET-1` — **status: wired, value unconfirmed**

| | |
|---|---|
| What it changes | memory recall opens its OWN session instead of riding the caller's; the caller's transaction is never held across the recall's reads (the `RT-MEMTXN-LEAK-1` family) |
| Already on your stack? | `docker-compose.prod.yml:148` wires it from your `.env` — **confirm the value you run** |
| Presence signal | recall still returns rows (your agents' `memory.recall` steps succeed) while `SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction'` sampled during a recall-heavy run stays at your baseline — the flag's whole effect is that the CALLER's transaction is no longer held open across the recall |
| Absence signal | `aindy_db_pool_exhaustion_events_total` does not move for the window; `aindy_db_pool_checkedout` / `aindy_db_pool_overflow` flat at baseline; no `[MemoryOrchestrator] recall failed` in the api log |
| Failure looks like | recall returning stale rows the caller had just written and not committed (the entry's one behaviour change: uncommitted writes are no longer visible to recall) |
| Flips | the default in the next runtime release after 7 days / 200 runs with both signals |

### 2. `AINDY_SYSCALL_IDEMPOTENCY_STRICT=1` — `FR-27` (the strict half of `IDEM-11`) — **status: never on anywhere**

| | |
|---|---|
| What it changes | `EXACTLY_ONCE` syscalls under contention wait on a session-level advisory lock (default 300 s ceiling) instead of the second caller re-executing behind a `pending` row |
| Presence signal | `aindy_effect_gate_outcomes_total` — **read every label**: `reserved` must grow (the gate engaged), and `replayed` or `reclaimed` must be non-zero at least once (a duplicate was actually refused). Zero across `replayed`/`reclaimed` after 200 runs means your traffic never contended and the soak proved nothing — say so |
| Absence signal | `degraded` stays at baseline (a `degraded` outcome is the gate giving up); p95 of your enveloped-route latency unchanged (the lock wait is the cost) |
| Failure looks like | a request stuck for 300 s (the lock ceiling) — the trace shows the syscall waiting; file it with the two `action_id`s |
| Flips | default on in the next release after the presence signal shows a refused duplicate |
| ★ If Claw is the traffic | only a NON-WebChat channel reaches the effect seam — WebChat streams and bypasses `deliver()` entirely (measured 2026-09-22, 0 ledger rows from 4 live turns; `SUBSTRATE-WITNESS-1`). And a live Claw's `/metrics` is nodus-observability's registry, not the runtime's, so `aindy_effect_gate_outcomes_total` cannot be read from outside the process: read the `effect_records` rows (durable, survives a restart) or expose the runtime registry first |

### 3. `AINDY_DELEGATION_PRIVATE_MEMORY=1` — `RTR-4` — **status: never on anywhere**

| | |
|---|---|
| What it changes | a delegated (child) agent run's memory writes are stamped `owner_run_id`; the parent's recall excludes them (private to the delegation) |
| Presence signal | `SELECT count(*) FROM memory_nodes WHERE owner_run_id IS NOT NULL` goes from 0 to > 0 during the window — if it stays 0 your workload never delegated and the row is unexercised |
| Absence signal | your delegating agents' completion hooks still `loop_enforced: true`; no `[MemoryNodeDAO]` warnings |
| Failure looks like | a parent run that used to read a child's finding and now cannot (a `score.computed` that regressed on a delegating run) — that is the entry's intended change and may be the wrong default for you; report it either way |
| Flips | default on after the presence signal, unless the failure row is reported — then the default stays off and the entry records why |

### 4. `AINDY_DURABLE_CONTINUATION=1` (then `AINDY_DURABLE_CONTINUATION_ALL=1`) — `DUR-1..4` / `ECOGAP-1` phase 3 — **status: never on anywhere**

| | |
|---|---|
| What it changes | a run whose process died mid-flight is re-claimed and driven FORWARD from its last recorded step on the next boot, instead of staying `executing` forever (`_ALL` drops the per-flow "continuation-safe" declaration, because runtime-mediated effects are at-most-once) |
| Presence signal | one real crash. Do not manufacture it in production; take it when it happens (a deploy restart mid-run counts). The api log shows `[AgentContinuation] crash-continued N agent run(s)` and the run's `agent_steps` show `replayed`-free rows before the cut and fresh rows after; the run reaches `completed` |
| Absence signal | `SELECT count(*) FROM agent_runs WHERE status='executing' AND started_at < now() - interval '1 hour'` reads 0 after each restart in the window (before the flag: every restart strands some) |
| Failure looks like | a continued run that repeated an external effect (a duplicate email, a doubled row) — the exact thing `SUBSTRATE-WITNESS-1` says nothing can witness today; if you see one, it is the most valuable report in this file |
| Flips | `AINDY_DURABLE_CONTINUATION` default on after one witnessed continuation; `_ALL` a release later |

### 5. `AINDY_PLANNER_MEMORY_INJECTION=1` → `AINDY_ASYNC_JOB_LOOP_CLOSURE=1` → `AINDY_NEXT_ACTION_ACTING=1` — `INFINITY-RUNTIME-1` — **status: never on anywhere; one at a time, in this order**

| | |
|---|---|
| What each changes | (a) the planner's prompt gains a memory block; (b) async jobs join the Infinity loop (emit the loop's events); (c) a `NextAction` from a completion hook can start ONE bounded follow-up run (`AINDY_NEXT_ACTION_MAX_CHAIN`, default 3) |
| Presence signal | (a) `recall.used` system events with `payload->>'operation_type' = 'agent_planning'` (the planner records the node ids it injected) — 0 rows means no plan was ever fed memory; (b) `score.computed` system events for async jobs (`emit_execution_score`, `source='agent'`) appearing for job ids that never had one before; (c) an `agent_runs` row whose `parent_run_id` is set, created by `next_action_dispatch` (its chain depth ≤ `AINDY_NEXT_ACTION_MAX_CHAIN`, default 3) |
| Absence signal | (a) plan quality by your own score — the entry's stated risk is plans shifting silently; (b) async job failure rate unchanged; (c) no chain deeper than the cap — walk `parent_run_id` from any run created in the window |
| Failure looks like | (a) a plan that cites memory it should not see (tenant leak — report immediately); (c) a run chain that does not stop, or a follow-up run dispatched from a run that was not approved |
| Flips | each flag's default a release after its own presence signal; (c) never flips before (b) |

### 6. `AINDY_FLOW_FAN_OUT=1` — `FLOW-PARALLEL-1` phase 4 — **status: needs a flow that declares a group; you may have none**

| | |
|---|---|
| What it changes | edges declared in a `FanOutEdgeGroup(join=all\|any\|quorum)` run concurrently (bounded by `AINDY_FLOW_FAN_OUT_MAX_WIDTH`) instead of in declaration order |
| Presence signal | a flow of yours that DECLARES a group. If none of your flows fans out today, the honest report is "no declaring flow" and this row waits — do not declare one for the soak's sake |
| Absence signal | the group's `partial` outcome never appears without a failed branch behind it; `flow_runs.state` for the group carries every branch's result |
| Flips | the default on the evidence of one real declaring flow completing under the flag |

### 7. Not flags — two questions the entries are waiting on

- **`RETRY-CONTEXT-1` (carry half):** is there a tool of yours that would do better on attempt 2
  if it knew WHY attempt 1 failed? Name it. The runtime will not build the carry without a
  consumer, and it will never fold the failure into `args` (that un-dedups the retry).
- **`AUTHORITY-NEGOTIATION-1` phase 3:** the `nodus_vm` denial re-run — `APP_HANDOFF_v2.22.0.md`
  §6 ask 1. With it, the flip is a judgment on both backends. (`AINDY_AUTHORITY_NEGOTIATION` is
  already on in your container; this is evidence, not a flag.)

**Already default-on, not a soak ask:** `AINDY_CHILD_CONTEXT_CLAMP` (`AUTHORITY-VALUE-1`, flipped
with the caller fix), `AINDY_NODUS_WARM_POOL` (`NODUS-WARMPOOL-1`), `AINDY_SYSCALL_IDEMPOTENCY`
(`IDEM-11`, 2.5.0). If any of these is set to `0` on your stack, that is worth knowing too.

---

## Status table (the runtime keeps this current)

| # | Flag | Entry | On your stack since | Evidence received | Default flipped in |
|---|---|---|---|---|---|
| 1 | `AINDY_MEMORY_RECALL_OWN_SESSION` | `DB-NODUS-BUDGET-1` | wired; value unconfirmed | — | — |
| 2 | `AINDY_SYSCALL_IDEMPOTENCY_STRICT` | `FR-27` / `IDEM-11` | — | — | — |
| 3 | `AINDY_DELEGATION_PRIVATE_MEMORY` | `RTR-4` | — | — | — |
| 4 | `AINDY_DURABLE_CONTINUATION` (+`_ALL`) | `DUR-1..4` | — | — | — |
| 5a | `AINDY_PLANNER_MEMORY_INJECTION` | `INFINITY-RUNTIME-1` | — | — | — |
| 5b | `AINDY_ASYNC_JOB_LOOP_CLOSURE` | `INFINITY-RUNTIME-1` | — | — | — |
| 5c | `AINDY_NEXT_ACTION_ACTING` | `INFINITY-RUNTIME-1` | — | — | — |
| 6 | `AINDY_FLOW_FAN_OUT` | `FLOW-PARALLEL-1` | — | needs a declaring flow | — |
