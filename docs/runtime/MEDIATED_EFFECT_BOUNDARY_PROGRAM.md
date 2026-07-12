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
- **MEB-1b — ✅ SHIPPED 2026-07-11 (gate repair, the behavior change).** The gate now *fires*
  from a per-syscall `SyscallEntry.execution_guarantee` declaration (new additive field,
  default `AT_LEAST_ONCE`), flag-gated by `AINDY_SYSCALL_IDEMPOTENCY` (default off), scoped to
  `execution_unit_id` — the dead `ExecutionUnit.extra` PK lookup is removed. Kept the separate
  `_gate_db` session and the `_is_uuid` #157 guard (the gate stays scoped to UUID runs); a ledger
  failure degrades to AT_LEAST_ONCE. **No syscall declares `EXACTLY_ONCE` yet — this ships the
  mechanism, inert until a syscall opts in AND the flag is on.** Verified: gate unit suite
  rewritten to the entry+flag mechanism (fires/skips/replay/degrade/uuid-guard/action-id), plus a
  new real-Postgres end-to-end dedup test (`tests/integration/test_idempotency_gate_e2e.py::
  test_syscall_idempotency_dedup_e2e`) that runs in CI's Integration job — the only cover for the
  `_gate_db` transaction lifecycle under a real transaction. **Deferred (follow-ups):** populating
  `execution_id` on the writer (EU-by-source lookup — compensation-ledger bonus); relaxing the
  `_is_uuid` guard for broader (`run_<uuid>`) coverage now that the EU-PK cast is gone; and
  adopting `EXACTLY_ONCE` on specific syscalls (e.g. memory.write) — a per-syscall decision.

### MEB-2 — G4a activation (enforcement)
Orthogonal to idempotency; hangs off the MEB-0 seam.
- **2a (thin) — ✅ SHIPPED 2026-07-11.** Config-driven activation: `AINDY_CAPABILITY_POLICIES`
  (JSON) → `register_capability_policy` and `AINDY_SECRET_SCOPES` (JSON) → `register_secret_scope`,
  loaded (memoized) in `tool_registry._ensure_tools_loaded` so the gates are live in every process
  that runs `execute_tool`. Any registered policy flips `has_capability_policies()` true, activating
  the recipient/domain/rate enforcement in `execute_tool`; secret scopes make `resolve_secret`
  fail-closed. Empty/absent config = no-op (behavior unchanged). Verified: config parse/register +
  a real `execute_tool` denial of an out-of-allowlist domain. **Known limits (→ 2b / follow-up):**
  enforcement is static arg-string inspection (a runtime-built URL is uncovered), and
  `resolve_secret` is fail-open on secret names with no registered scope.
- **2b (strong) — TODO:** a **true egress chokepoint** — mediate outbound network (httpx/socket) at the
  boundary so a tool cannot bypass by constructing a URL at runtime. Kernel-adjacent; converges
  with the sandbox `--network none` extension path. Dedicated sub-effort.

### MEB-3 — Multi-tenant MCP (identity + attribution)

**Upstream unblocked 2026-07-11 (nodus-mcp 0.1.2):** both gates that blocked this at 0.1.1 are
fixed — `auth_hook` now receives a real per-call context (`session`/`request_id`/`request`/
**`headers`**, #8) and `run_sse_app()` mounts `/messages/` (#7). Split into two pieces:

**MEB-3a — per-session identity (no schema). ✅ SHIPPED 2026-07-11.** `platform_layer/mcp_server.py`
adds an SSE transport (`aindy-runtime mcp-server --transport sse`) and, under
`AINDY_MCP_SERVER_MULTI_TENANT=true`, an `auth_hook` that resolves each session's
`Authorization: Bearer <jwt>` or `X-Platform-Key` header to a real user id via the **existing** auth
surface (`decode_access_token` / `_resolve_platform_key_as_user` — no new mechanism) and dispatches
every call as that identity (threaded handler-side via a `_SESSION_IDENTITY` contextvar). Fail-closed:
a call with no resolvable identity is denied; the syscall dispatcher then enforces per-syscall
capability + tenant isolation for that user. Multi-tenant is rejected over stdio (no per-request
headers). Opt-in, off by default; stdio behaviour unchanged. Verified: real nodus-mcp 0.1.2 SSE app
builds with `/sse` + `/messages/` and the auth_hook attached.

**MEB-3b — EffectRecord attribution columns (schema bump). Deferred.** Add tenant/session columns to
EffectRecord (**schema-contract bump + Alembic migration** — the program's only schema change) so
each effect/idempotency row records *which* session produced it. Pure attribution/audit; separable
from the 3a identity mapping and not required by it. Optionally mint a per-session capability token
(`mint_token(run_id=session, …)`) for a capability ceiling below the resolved user's full grant.

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
- **MEB-1b — ✅ SHIPPED 2026-07-11.** Dispatcher gate repaired: fires from a per-syscall
  `execution_guarantee` (flag-gated) instead of the dead EU lookup. IDEM-10 is now closed at the
  mechanism level for both the tool (MEB-0) and syscall (MEB-1b) paths.
- **MEB-2a — ✅ SHIPPED 2026-07-11.** G4a thin activation: config-driven capability policies +
  secret scopes (`AINDY_CAPABILITY_POLICIES` / `AINDY_SECRET_SCOPES`) make the dormant
  `execute_tool` enforcement live. Opt-in; static arg-inspection level (MEB-2b is the true socket
  chokepoint).
- **MEB-2b — ✅ SHIPPED 2026-07-11.** True egress chokepoint: `platform_layer/egress_guard.py`
  wraps `socket.getaddrinfo` and denies hostname resolution outside the capability policy's
  domain allowlist — catching runtime-built URLs that MEB-2a's static arg inspection cannot see.
  Installed once, process-wide, but **inert unless an `egress_scope` allowlist is set**;
  `execute_tool` scopes it only for the tool `fn` call, only when the tool's capability carries a
  `domains` policy and `AINDY_EGRESS_ENFORCEMENT` is on. Opt-in, off by default. Honest limits
  (documented in the module): IP-literal connections perform no `getaddrinfo` and are uncovered; a
  resolution on a thread that doesn't inherit the contextvar escapes the scope; only resolution is
  guarded, not the eventual connect. The non-bypassable form remains the sandbox `--network none`
  + mediated proxy — this is the in-process strong-form for the non-sandboxed tool path.
- **MEB-3a — ✅ SHIPPED 2026-07-11.** Per-session multi-tenant MCP identity over SSE (nodus-mcp
  0.1.2 unblocked #7 + #8): auth_hook resolves each session's bearer/platform-key header to a real
  user; calls dispatch as that identity, fail-closed. No schema. Opt-in
  (`AINDY_MCP_SERVER_MULTI_TENANT`).
- **Next: MEB-3b (EffectRecord attribution columns)** — the program's only schema-contract bump;
  deferred (attribution/audit, not required by 3a). Plus the MEB-1 follow-ups (adopt EXACTLY_ONCE
  on chosen syscalls; populate execution_id; relax _is_uuid) and the MEB-2b IP-literal /
  thread-escape hardening.

MEB-0 was the standalone win — the single biggest real gap (side-effecting agent tools had no
idempotency at any layer) is closed; MEB-2a/2b give the tool path a live, runtime-aware egress
boundary; and MEB-3a makes the server-side MCP surface genuinely multi-tenant.

## Cross-references

- TECH_DEBT: `IDEM-10` (MEB-0 + MEB-1), `ECOGAP-4` G4a (MEB-2) and MCP multi-tenant (MEB-3),
  `ECOGAP-1` (the continuation payoff).
- Key files: `kernel/syscall_dispatcher.py`, `agents/tool_registry.py`,
  `db/models/effect_record.py`, `core/execution_gate.py`, `agents/capability_service.py`,
  `agents/capability_policy.py`, `platform_layer/secret_broker.py`,
  `platform_layer/mcp_server.py`.
