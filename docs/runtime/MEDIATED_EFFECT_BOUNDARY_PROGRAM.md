---
title: "Mediated Effect Boundary — program plan (MEB)"
api_version: "1.0"
last_verified: "2026-07-11"
status: current
owner: "platform-team"
---

# Mediated Effect Boundary (MEB) — program plan

A consolidated, source-verified plan for three runtime concerns that were being tracked
separately but share one substrate:

- **IDEM-10** — the EXACTLY_ONCE idempotency gate is dead in production; agent tool calls
  bypass it entirely.
- **ECOGAP-4 / G4a** — the capability-egress + secret boundary is scaffolded but inert.
- **Multi-tenant MCP** — the MCP server (ECOGAP-4 / G4b, server-side) ships single-identity;
  per-session identity + capability is deferred.

This doc is the durable home for the plan so it survives across sessions. It supersedes the
inline "reopen scope" prose in the three TECH_DEBT entries, which now point here.

## The thesis (and its honest correction)

The three are **not the same work**. What they share is *plumbing*, not finished behavior:
**one primitive** (an idempotent, identity-scoped, capability-enforced effect record) applied
at **two chokepoints** (`execute_tool` for agent tools, `SyscallDispatcher._dispatch` for
syscalls), serving **three distinct concerns** (dedupe / enforcement / attribution). Each
concern needs its own net-new work on top of the shared seam.

## Verified current reality (source-audited 2026-07-11)

### The idempotency gate is structurally dead — two independent reasons
`kernel/syscall_dispatcher.py:504-579` (Step 2f), with `_resolve_effect_record` (`:195-256`)
and `_complete_effect_record` (`:259-267`). It only dedupes when the resolved
`execution_guarantee == "EXACTLY_ONCE"`, read from
`ExecutionUnit.extra["retry_policy"]["execution_guarantee"]` via
`ExecutionUnit.id == context.execution_unit_id`. In production it never fires:

1. **PK never matches (structural).** `ExecutionUnit.id` is a server-side random `uuid4`
   (`db/models/execution_unit.py:54`); the originating run id lives in the separate `source_id`
   column (`:87`), and EUs are looked up elsewhere by `get_by_source(...)`
   (`execution_unit_service.py:269`). But a syscall carries `execution_unit_id = run_id` (or a
   fresh `uuid4` from `make_syscall_ctx_from_tool:900`, or nothing). The lookup
   `ExecutionUnit.id == execution_unit_id` returns `None` → guarantee defaults to
   `AT_LEAST_ONCE`. No production caller ever sets `execution_unit_id = str(eu.id)`.
2. **Guarantee lives where the gate can't read it.** `EXACTLY_ONCE` is produced only by the
   `AGENT_HIGH_RISK` policy (`retry_policy.py:65-71`) and written by `require_execution_unit` /
   `_resolve_policy_for_eu` (`core/execution_gate.py:174-241`) — never for the EU the syscall
   addresses. `gate_and_dispatch` (`execution_gate.py:364`) is dead code with no callers.

`tests/unit/test_idempotency_gate.py` stubs an EU with a matching bare-UUID PK and the
`EXACTLY_ONCE` extra — a state production never constructs — so the logic is verified in
isolation while its wiring is absent.

### Agent tool calls have NO idempotency at any layer
`call_tool → run_agent_tool → execute_tool` (`runtime/nodus_worker.py:92-153, 242-262` →
`agents/tool_registry.py:94-244`). `run_agent_tool` calls `execute_tool` directly — it never
touches the dispatcher, EffectRecord, or any `action_id`. Its only gate is fail-closed
capability-token enforcement. The two side-effecting entrypoints are fully disjoint: syscalls
go through `_dispatch` (dead gate); tools go through `execute_tool` (no gate). A retried Nodus
step that calls `call_tool("send_email", …)` re-sends.

### EffectRecord — the substrate exists and is sound, but under-used
`db/models/effect_record.py`: `action_id` (Text, UNIQUE `uq_effect_records_action_id`, the
dedupe key = sha256 from `compute_action_id`), `action_type`, `input_hash`, `execution_id`
(UUID FK → `execution_units.id`, **nullable, never populated by the writer**), `step_id`,
`status` (`pending|success|failed` string), `result_payload` (JSONB, replay cache),
`external_receipt` (JSONB), `created_at`/`completed_at`. Written **only** on the syscall path
(`_resolve_effect_record` inserts `pending`; `_complete_effect_record` finalizes). Read by the
compensation walker (`core/effect_compensation.py:85-105`, keyed on `execution_id`, which the
writer never sets — a latent undo-link gap). GC'd by `scheduler_service.py:395-476`. The upsert
logic is race-safe (unique constraint, stale-pending recovery, concurrent-degrade to
AT_LEAST_ONCE) and reusable verbatim.

### G4a is inert, not merely off
Four seams in `execute_tool`, all vacuous in prod:
- `enforce_capability_policy` (`tool_registry.py:198`) is gated behind `has_capability_policies()`
  (`:179`), which returns `bool(CAPABILITY_POLICIES)` (`agents/capability_policy.py:58-59`);
  `register_capability_policy` (`:45`) has **zero** production callers → block skipped.
- `enforce_capability_rate` (`:214`) — same gate, same vacuity.
- `capability_scope` (`:239`, `secret_broker.py:181-187`) executes but is only meaningful if a
  tool reads it via `resolve_secret` — and none do.
- `resolve_secret` (`secret_broker.py:231-269`) has **zero** production callers;
  `register_secret_scope` / `set_secret_broker` likewise, so `SECRET_SCOPES` is empty and every
  secret is ungated.
- **No real egress chokepoint.** Only static arg-string inspection (`extract_recipients` /
  `extract_domains`, `capability_policy.py:73-89`) — no socket/httpx interception. A tool that
  builds a URL at runtime, or egresses to a host not literally in the args, is uncovered. And
  even this is dead because the policy gate is false.

### The identity primitive already exists (tool path only)
`agents/capability_service.py::mint_token(run_id, user_id, plan, db, approval_mode,
agent_type="default", capability_ceiling=None)` (`:442-532`) — HMAC-SHA256 signed dict,
`TOKEN_TTL_HOURS=24`, `capability_ceiling` for least-privilege delegation (RTR-4). Verified in
`execute_tool` via `check_tool_capability` → `validate_token` (`:605-673`, fail-closed). It
already carries `run_id` + `user_id` — the per-session identity MCP needs. **But the token
governs only the tool path.** The dispatcher uses a separate caller-supplied capabilities list
(`make_syscall_ctx_from_tool:883-907`; `dispatch_syscall` fills one inferred capability via
`_infer_dispatch_capability:811-827`) and checks `entry.capability in context.capabilities`
(`:431`) — it does not consume the minted token at all.

## The unifying primitive — a mediated effect boundary

A boundary side-effecting calls pass through:

```
resolve identity (token)
  → enforce capability / egress
  → idempotency check/write (EffectRecord on a STABLE scope)
  → execute
  → record effect (finalize)
```

Building blocks already exist and are sound: `compute_action_id` (`execution_gate.py:70`), the
race-safe `_resolve_effect_record` upsert (`syscall_dispatcher.py:195-267`), and
`mint_token`/`check_tool_capability`. There is **no single chokepoint** — side effects bifurcate
into `execute_tool` (tools) and `_dispatch` (syscalls). The program installs the same boundary
at both, keyed on a stable scope (the run/session id the token carries) rather than the
unaddressable EU PK.

## The program — 4 phases (prefix `MEB-*`)

Phases are dependency-ordered. MEB-0 is the keystone and a standalone win; the rest can follow
in any order after it.

### MEB-0 — Tool-path effect boundary (keystone) — ✅ SHIPPED 2026-07-11
Inserts `compute_action_id` + the EffectRecord upsert (pending → execute → finalize) into
`execute_tool`, scoped to the stable `run_id` from the token. **Delivers agent-tool
idempotency — the "part that actually matters" (IDEM-10)** — and creates the seam MEB-2 and
MEB-3 hang off. No schema change.

**Shipped shape (opt-in, doubly-gated):** the global flag `AINDY_TOOL_IDEMPOTENCY` (default
off) AND a per-tool `execution_guarantee="EXACTLY_ONCE"` (via `register_tool`), with a stable
`run_id`. Default `AT_LEAST_ONCE` = current behavior (no dedup). On a match, a retry replays
the cached result (`idempotent_replay: true`) instead of re-executing; a ledger failure
degrades to AT_LEAST_ONCE; a failed tool leaves the slot reclaimable (retryable). The shared
primitive lives in **`AINDY/kernel/effect_ledger.py`** (`resolve_effect_record` /
`complete_effect_record`), used only by the tool path for now — **the byte-identical private
copies in `syscall_dispatcher` are intentionally left in place; MEB-1 consolidates them.**
Verified on real Postgres (tool executed once across two identical calls; second replayed).
Keys only on `EffectRecord.action_id` (text), never the EU UUID — so it does not touch the
#157 lookup path. Tests: `tests/unit/test_tool_idempotency.py`.

### MEB-1 — Repair the dispatcher gate (IDEM-10 layer 1)
Split into two PRs so the behavior-preserving refactor lands separately from the behavior
change (same discipline that kept MEB-0 out of the dispatcher):

- **MEB-1a — ✅ SHIPPED 2026-07-11 (consolidation, behavior-preserving).** The dispatcher's
  duplicated private `_resolve_effect_record` / `_complete_effect_record` copies and the
  `STALE_PENDING_THRESHOLD_SECONDS` constant were removed; the dispatcher now imports them from
  `kernel/effect_ledger.py` (the module MEB-0 introduced). Gate call sites unchanged; the gate
  still reads its guarantee from the (dead) EU lookup, so **no behavior change** — this only
  pays off the temporary duplication MEB-0 left behind. Verified: `test_idempotency_gate` +
  the tool/contract suites green; the aliases resolve to `effect_ledger`.
- **MEB-1b — TODO (gate repair, the behavior change).** Make the gate *fire* by reading the
  guarantee from a per-syscall `SyscallEntry.execution_guarantee` declaration (flag-gated,
  default off) instead of the unmatchable EU PK lookup, scoped to `execution_unit_id`. Keep the
  separate `_gate_db` session and the `_is_uuid` #157 guard. **Deferred out of MEB-1:**
  populating `execution_id` on the writer (needs an EU-by-source lookup — a compensation-ledger
  bonus, not core idempotency; tracked as a follow-up).

### MEB-2 — G4a activation (enforcement)
Orthogonal to idempotency; hangs off the MEB-0 seam.
- **2a (thin):** wire `register_capability_policy` + `register_secret_scope` from config/plugins
  so `has_capability_policies()` is non-empty and `resolve_secret` has a real path. Activates the
  existing (bypassable) static enforcement. Opt-in flag.
- **2b (strong):** a **true egress chokepoint** — mediate outbound network (httpx/socket) at the
  boundary so a tool cannot bypass by constructing a URL at runtime. Kernel-adjacent; converges
  with the sandbox `--network none` extension path. Dedicated sub-effort.

### MEB-3 — Multi-tenant MCP attribution (identity)
Add tenant/session columns to EffectRecord (**schema-contract bump + Alembic migration** — the
program's only schema change) and wire the MCP server's `NodusServer.auth_hook` to map each
session → `mint_token(run_id=session, user_id=…, capability_ceiling=…)` → dispatch as that
identity. Upgrades server-side MCP from single-identity to real multi-tenant. Leans on MEB-0/1 +
the identity primitive.

## Decisions

1. **Idempotency scope key:** `scope = stable run/session id` (not the EU PK). Carry
   tenant/session as *added EffectRecord columns* for attribution, not baked into the hash — so
   IDEM-10 (per-run replay) and MCP (per-session attribution) share one design.
2. **Opt-in vs always-on tool idempotency:** default `AT_LEAST_ONCE` (no behavior change);
   `EXACTLY_ONCE` opt-in per-tool or per-run, so MEB-0 can't regress existing behavior.
3. **G4a-strong depth:** thin activation (2a) first; true socket mediation (2b) as its own
   effort (the genuinely hard, sandbox-adjacent part).
4. **Schema/migration timing:** only MEB-3 bumps the schema contract; MEB-0/1/2 are logic on
   existing tables.

## Size, risk, verification

A **multi-PR program (~5–8 PRs across 4 phases) on the kernel's most correctness-sensitive
path.** The #157 history is PG-only transaction-poisoning (`InvalidTextRepresentation` on UUID
casts, savepoint semantics) that only reproduces on real Postgres — **every phase must be
verified against throwaway Postgres, not SQLite.** This is foundational work, not a
"batch-into-the-release" item.

**Relationship to ECOGAP-1:** ECOGAP-1 Phases 1/2/2a ship crash-continuation gated to
*idempotent-declared* flows/agents precisely because this layer doesn't exist. MEB-0/1 are the
prerequisite for **declaration-free** continuation (the ECOGAP-1 Phase 3 payoff).

## Progress / next

- **MEB-0 — ✅ SHIPPED 2026-07-11.** Tool-call idempotency behind `AINDY_TOOL_IDEMPOTENCY` +
  per-tool `EXACTLY_ONCE`; shared primitive in `kernel/effect_ledger.py`; PG-verified.
- **MEB-1a — ✅ SHIPPED 2026-07-11.** Dispatcher consolidated onto `kernel/effect_ledger.py`
  (duplicated private copies removed); behavior-preserving.
- **Next: MEB-1b** — repair the gate to fire from a per-syscall `execution_guarantee` (flag-gated)
  instead of the dead EU lookup, so syscall idempotency is real too.
- Then MEB-2 (G4a) and MEB-3 (multi-tenant MCP), in any order.

Even if MEB-2b and MEB-3 are never done, MEB-0 was the standalone win — the single biggest real
gap (side-effecting agent tools had no idempotency at any layer) is closed.

## Cross-references

- TECH_DEBT: `IDEM-10` (MEB-0 + MEB-1), `ECOGAP-4` G4a (MEB-2) and MCP multi-tenant (MEB-3),
  `ECOGAP-1` (the continuation payoff).
- Key files: `kernel/syscall_dispatcher.py`, `agents/tool_registry.py`,
  `db/models/effect_record.py`, `core/execution_gate.py`, `agents/capability_service.py`,
  `agents/capability_policy.py`, `platform_layer/secret_broker.py`,
  `platform_layer/mcp_server.py`.
