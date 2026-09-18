---
title: "Design Records"
last_verified: "2026-09-17"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Design records

**Why a thing was built the way it was, and — for the ones not built yet — what building it
would mean.** Every document here is a scope, design, program plan or proposal written *before*
the code, usually because `AGENT_WORKING_RULES.md` §8 requires an approved proposal ahead of a
runtime behaviour change. They are the reasoning; `docs/runtime/` is the resulting contract.

This folder exists because these documents are neither of the other two things. A shipped design
is not a **contract** (`docs/runtime/` says what the runtime guarantees *now*; a design says what
was intended and why, and may describe options that were rejected). And it is not **dead**
(`docs/archive/` holds documents nothing needs; source code cites most of these by name —
`execution_environment.py` opens with *"Design: … Read it before…"*, `effect_ledger.py` and
`syscall_dispatcher.py` cite the MEB program for why the chokepoints sit where they do).

**A design's status is its owning `TECH_DEBT.md` entry's status**, restated in the table so a
reader does not have to cross-reference. When an entry closes, the design stays here with its
header updated — it becomes the record. It moves to the archive only when nothing cites it and
its reasoning has been carried into a contract, which so far has never happened.

## Index

| Document | Owner | Status (2026-09-17) | What it decides |
|---|---|---|---|
| [`AUDIT_CORRELATION_DESIGN.md`](./AUDIT_CORRELATION_DESIGN.md) | `AUDIT-CORRELATION-1` | **IMPLEMENTED 2026-09-17 (#719; DEC-052..055)** | Re-measures the three joins: `ExecutionAuthority` does not exist, (2) already fell out of `EXEC-ENV-BIND-1`, and `EffectRecord` has no `trace_id` — the real join is unindexed JSONB. Three additive payload keys on `syscall.executed`; **no FK in either direction**; the join is time-bounded by retention on both sides. |
| [`AUTHORITY_LIFETIME_DESIGN.md`](./AUTHORITY_LIFETIME_DESIGN.md) | `AUTHORITY-LIFETIME-1` | **IMPLEMENTED 2026-09-17 (#720; DEC-056..059)** | Authority ends with the run: `CANCEL-REACH-1`'s cached own-session fail-open read widened to every terminal status, sticky once terminal, at the same two sites; the HMAC check stays stateless. **A `waiting` run keeps its authority.** |
| [`AUTHORITY_NEGOTIATION_DESIGN.md`](./AUTHORITY_NEGOTIATION_DESIGN.md) | `AUTHORITY-NEGOTIATION-1` | **In flight** — phases 0+1 shipped (#600, 09-10); 2–3 design only | A denied capability gets one bounded, downgrade-only retry that cannot grant authority. **§2 overturns the entry's own proposed primitive.** |
| [`C3_NON_LINUX_STRONG_SANDBOX_PLAN.md`](./C3_NON_LINUX_STRONG_SANDBOX_PLAN.md) | `C3` | **Preparation, unscheduled** — phases 0–5 done; the native non-Linux strong-VM runner waits on a trigger | Windows-native and macOS tracks for `strong-sandbox-certified` off Linux, so either can start on day 1. |
| [`CLI_EXECUTION_SURFACE_SCOPE.md`](./CLI_EXECUTION_SURFACE_SCOPE.md) | `CLI-EXEC-SURFACE-1` | **Decided 2026-09-17 — DEC-047 (provisional)**: the operator half stays HTTP-only; the entry closes on acceptance | §8: an operator syscall opens three doors at once (`/platform/syscall`, MCP, any CLI). Do **not** build a CLI to answer it. |
| [`DURABLE_EXECUTION_PROGRAM.md`](./DURABLE_EXECUTION_PROGRAM.md) | `ECOGAP-1` phase 3 | **Complete 2026-07-12** — DUR-1..4 shipped, opt-in; soak-then-flip remains | Crash continuation resumes *forward* from the last completed node; it never re-executes code, which is why kernel replay was declined. |
| [`EGRESS_INPROC_DESIGN.md`](./EGRESS_INPROC_DESIGN.md) | `EGRESS-INPROC-1` | **IMPLEMENTED 2026-09-16 (#718; DEC-048..051)** | **§1 finds a defect the entry did not name: the isolated tool branch returns before `egress_scope`, so a declared-isolation tool has no egress enforcement at all.** Decision once at `execute_tool`; enforcement per provider; the worker installs the guard from its payload; `env_applied.network` reports the mechanism. |
| [`EVENT_OUTBOX_DESIGN.md`](./EVENT_OUTBOX_DESIGN.md) | `EVENT-OUTBOX-1` | **IMPLEMENTED 2026-09-17 (#721; DEC-060..062)** | The buffer engages only inside a request pipeline (every other path already co-locates), and the capture read is a score, not a gate. Inside a pipeline the event row is added to the handler's session without commit and rides its next commit; a raising handler leaves no row. No outbox table. |
| [`EXECUTION_ENVIRONMENT_SPEC_DESIGN.md`](./EXECUTION_ENVIRONMENT_SPEC_DESIGN.md) | `EXEC-ENV-BIND-1` | **Complete 2026-09-13** — all four phases (#567, #639) | The `ExecutionEnvironmentSpec` vocabulary: what a unit *requires* (visibility, authority, resources, `min_assurance`), clamped to a seam's floor. Header corrected 09-13 — it said phases 3–4 were still design. |
| [`FLOW_PARALLEL_DESIGN.md`](./FLOW_PARALLEL_DESIGN.md) | `FLOW-PARALLEL-1` | **In flight** — phases 0–2 shipped (#603, 09-10, #640); 3–4 design only | Fan-out as one superstep with a barrier; join policies `all` / `any` / `quorum`; the runtime's first `partial` emitter. |
| [`FR35_GUEST_LLM_USAGE_DESIGN.md`](./FR35_GUEST_LLM_USAGE_DESIGN.md) | `FR-35` | **Shipped 2026-09-17 (#712)** — closed; the Redis-backed admission integration test is owed | On `nodus_vm` a tool step's LLM usage is metered in the WORKER and reaches nothing. Usage rides the worker reply as a fourth deferred collection (beside `memory_writes`) and is recorded in the parent under an explicit subject; deferral REPLACES observation in the worker. **§3: the governor's admission and accounting split differently** — admission stays in the worker (real only on a Redis RM). Also closes #706's worker-span gap by replaying `chat {model}` with recorded timestamps. |
| [`FR27_ADVISORY_LOCK_DESIGN.md`](./FR27_ADVISORY_LOCK_DESIGN.md) | `FR-27` | **Approved + shipped 2026-09-12** (#627), opt-in `AINDY_SYSCALL_IDEMPOTENCY_STRICT` | Strict at-most-once under contention via a session-level advisory lock with explicit unlock; the measured prototype (N−1 of N) is in the doc. |
| [`INITIATOR_IDENTITY_DESIGN.md`](./INITIATOR_IDENTITY_DESIGN.md) | `INITIATOR-IDENTITY-1` | **Design filed 2026-09-17** — four decisions pending | `SyscallContext.subject`, asserted and transport-set; readers are constrain-only and AST-pinned (quota sub-key, ledger label, payload key, `enduser.pseudo.id`). **The memory namespace stays the operator's — the entry listed that as a collapse; it is the one row that must stay collapsed.** |
| [`LEASE_FENCE_DESIGN.md`](./LEASE_FENCE_DESIGN.md) | `LEASE-FENCE-1` | **Shipped 2026-09-16 (#705)** — closed; Alembic `0019`; phase 3 (fence the prune) is a follow-up | One `fence` integer, incremented on takeover; checked `FOR SHARE` inside a leader-only job's transaction. **§2 finds the least-idempotent job is the one `CLAUDE.md` says not to guard twice.** |
| [`LLM_SEAM_ADOPTION_SCOPE.md`](./LLM_SEAM_ADOPTION_SCOPE.md) | `COST-GOVERNOR-1` | **Superseded by events** — governor shipped 2026-09-13 (#638) | Why the governor waited on a real consumer being routed through the seam first. The ordering, not the design. |
| [`MEDIATED_EFFECT_BOUNDARY_PROGRAM.md`](./MEDIATED_EFFECT_BOUNDARY_PROGRAM.md) | `IDEM-10`, `ECOGAP-4` G4a | **Complete 2026-07-11** — MEB-0..3b shipped; the gate defaulted on in 2.5.0 | One primitive (`EffectRecord`) at two chokepoints (`execute_tool`, the dispatcher). Live contract: `docs/runtime/IDEMPOTENCY_CONTRACT.md`. |
| [`OTEL_GENAI_SEMCONV_DESIGN.md`](./OTEL_GENAI_SEMCONV_DESIGN.md) | `OTEL-GENAI-SEMCONV-1` | **Shipped 2026-09-16 (#706)** — closed; `user.id` drop owed next release | **§1: HEAD emits two span kinds and no LLM/tool/agent span — there is nothing GenAI-shaped to rename.** Three additive span kinds at three existing seams; the meter folds into the span helper; content capture stays out. |
| [`PROVIDER_BREADTH_PROGRAM.md`](./PROVIDER_BREADTH_PROGRAM.md) | `ECOGAP-3` | **Complete 2026-07-12** (#241); more providers on demand | Embedding-provider abstraction with a configurable dimension and `memory reembed`; §3.2's dimension-migration constraint is cited from `embedding_providers.py`. |
| [`RECOVERY_GRANULARITY_DESIGN.md`](./RECOVERY_GRANULARITY_DESIGN.md) | `RECOVERY-GRANULARITY-1` | **Design filed 2026-09-17** — four decisions pending | Per-step durable write at the worker seam (`run_agent_tool` already holds a session), into `agent_steps`; replay by the plan's **step index** (compiler-emitted), not a call ordinal — the retry loop lives inside the guest; replay only on a continued run, from a `success` row. Not kernel replay (`DEC-019`). |
| [`RETRY_CLASSIFICATION_AND_CONTEXT_DESIGN.md`](./RETRY_CLASSIFICATION_AND_CONTEXT_DESIGN.md) | `RETRY-CLASSIFY-1`, `RETRY-CONTEXT-1` | **Phase 1 shipped 2026-09-16 (#703)** — CLASSIFY-1 closed; phase 2 (the carry) design only | One `FailureRecord` for both entries: a class set at the raising site, the substring table as a recorded fallback; the carried failure rides a **scope, never an argument** (the `EffectRecord` key). **§2: `execute_with_retry` has zero callers; §3: the classifier is wrong on the runtime's own refusals.** |
| [`SYSEVENT_RETENTION_DESIGN.md`](./SYSEVENT_RETENTION_DESIGN.md) | `SYSEVENT-RETENTION-1` | **Shipped 2026-09-16 (#704)** — closed; ships with the env var unset | Prune **leaves only** — four `NO ACTION` FKs make a referenced row undeletable and the one `CASCADE` is on the causal graph; a class per type, unclassified = keep, `report` before `prune`. |
| [`TOOL_SEAM_ISOLATION_SCOPE.md`](./TOOL_SEAM_ISOLATION_SCOPE.md) | `TOOL-SEAM-ISOLATION-1` | **Closed 2026-08-19** — A, B, C1, C2 all shipped | How the tool seam was measured against source before anything was built; the status table inside tracks each step. Header corrected 09-13 — it said "no code". |
| [`WITNESS_AND_BASELINE_SCOPE.md`](./WITNESS_AND_BASELINE_SCOPE.md) | `SUBSTRATE-WITNESS-1`, `PERF-BASELINE-1` | **Open (P1)** — both are consumer-shaped, not code-shaped | Why neither closes with a synthetic fixture: what is missing is a consumer that would *notice* if the guarantee broke. |
| [`WORKFLOW_STORE_DECLARATION_PROPOSAL.md`](./WORKFLOW_STORE_DECLARATION_PROPOSAL.md) | `ORCHESTRATOR-SPLIT-1` store 4 | **Approved + implemented 2026-09-09** (#611); entry CLOSED 2026-09-16 — (b) published, (a) declined (DEC-039) | The worker *declares* its guest workflow store (`sqlite`, autosweep off) rather than migrating it; §8 is the decision. |

**Not in this folder but of this kind:** `ORCHESTRATOR-SPLIT-1` (b) — *publish the ownership contract* — is a
contract, so it lives with the others in [`docs/runtime/DURABLE_STATE_OWNERSHIP_CONTRACT.md`](../runtime/DURABLE_STATE_OWNERSHIP_CONTRACT.md).
Its §7 records the decision (DEC-039: option (a) declined until the runtime reads guest state); the entry closed on it 2026-09-16.

## Conventions

- **Frontmatter** — the five keys; `Runtime Docs Validation` checks this folder.
- **A status line at the top** that says what shipped and where the live record is. When a
  phase lands, update the header in the same PR — four of the thirteen had headers that were
  wrong by one or more shipped phases when this folder was created, and the `CLAUDE.md` key-file
  rows for three of them still said "awaiting approval" or "no code".
- **Do not rewrite the design to match what shipped.** Add a dated status note; the delta between
  what was proposed and what was built is part of the record (`AUTHORITY_NEGOTIATION_DESIGN.md`
  §1 corrects the entry's count of `CAPABILITY_DENIED` sites from two to four — that correction
  is the most useful sentence in it).
- **Source cites these by path.** Moving or renaming one means updating `AINDY/`, `tests/` and
  `alembic/` — except under `AINDY/db/models/`, where a docstring edit costs a schema-version
  bump for zero DDL; `effect_record.py` and `execution_unit.py` deliberately keep the old
  `docs/runtime/` path, and `git grep` on the basename still resolves it.
