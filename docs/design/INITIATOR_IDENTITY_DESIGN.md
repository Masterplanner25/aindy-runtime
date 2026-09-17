---
title: "Initiator Identity — Design"
api_version: "1.0"
last_verified: "2026-09-17"
status: current
owner: "platform-team"
---

# `INITIATOR-IDENTITY-1` — an asserted acting subject beside the authenticated one — design

**DESIGN ONLY — nothing shipped. Proposal under `AGENT_WORKING_RULES.md` §8 (a new identity
field on the syscall context, with a rule about what it may touch).** The entry already states
the rule that makes this safe — *an asserted subject may only CONSTRAIN, never widen or
SELECT* — and the trap — *do not namespace memory by it*. §2 turns the rule into a list of
which readers may consult the field and which may not; §4 is what not to build. It is P2 with
no inbound-driven consumer at HEAD; this records the shape so the first one does not invent it.

---

## 1. The finding, verified

`tenant_context.py:13` — *"single-user-per-tenant model: tenant_id == user_id"*. Every
accounting and attribution surface keys on that one id:

| Surface | Keyed on | Collapses under an operator acting for N peers |
|---|---|---|
| `ResourceManager` concurrency + token windows (`resource_manager.py:208/:223`) | `tenant_id` | one deployment-wide bucket |
| LLM attribution (`token_meter.llm_attribution_scope(tenant_id, run_id)`) | `tenant_id` | every peer's spend is the operator's |
| `SystemEvent.user_id` (`system_event_service.py:62`) | `user_id` | "the operator did this" |
| memory namespace `/memory/{tenant_id}/…` | `tenant_id` | one namespace — **the correct outcome, see §2** |

`SyscallContext` (`syscall_registry.py:54`) carries `user_id`, `capabilities`, `trace_id`,
`execution_unit_id`, `metadata`. There is no field for *who asked*; `metadata` is free-form
and nothing reads a subject from it. `end_user|acting_subject|on_behalf` return zero hits.

**Corrections to the entry:** the memory-namespace row is listed as a *collapse*; it is the one
row that must stay collapsed. The operator authenticated; the operator's namespace is the only
one the runtime can authorise reads from. Splitting it by an asserted id would let a peer's
*claim* select what is read — exactly the entry's own prohibition. The other three rows are
accounting and audit, where an asserted id can only make things stricter or more specific.

---

## 2. The field, and the readers allowed to see it

`SyscallContext.subject: str | None = None` — **asserted, not authenticated**, set by the
transport from whatever it knows (a webhook's sender id, a chat peer, an MCP client's
`end_user`), never derived from a `User` row, never validated against one. Carried beside
`user_id`, which stays the authenticated owner of everything.

**Allowed readers — constrain-only, each stricter with the subject than without:**

- **Quota:** `ResourceManager` gains a per-subject sub-key under the tenant
  (`aindy:rm:tenant:{tenant}:subject:{subject}:…`) checked *in addition to* the tenant key.
  A subject cap can only refuse what the tenant cap would admit; it can never admit what the
  tenant cap refuses. Default unlimited (`AINDY_QUOTA_MAX_CONCURRENT_PER_SUBJECT=0`).
- **LLM accounting:** `llm_attribution_scope(tenant_id, run_id, subject)` — a third field on
  the ContextVar, a label on the ledger record and the usage counter. LiteLLM's `end_user`
  shape exactly: a budget key, never an auth key.
- **Audit:** `SystemEvent.payload["subject"]` (additive payload key, no column, no schema —
  `AUDIT-CORRELATION-1` takes the same route). The event's `user_id` stays the operator.
- **Attribution on OTel spans:** `enduser.id` already carries `user_id` (`DEC-036`); the
  subject goes on `enduser.pseudo.id`, the semconv slot for an asserted, non-authenticated id.

**Forbidden readers — pinned by test, not by convention:**

- `TenantContext.assert_memory_path` / `tenant_owns_memory_path` — the namespace is
  `tenant_id`'s and only `tenant_id`'s.
- `check_tool_capability`, `verify_token`, `capability_ceiling`, `enforce_api_key_scope` —
  the subject is never an input to an authorisation decision, in either direction.
- Any `FlowRun.user_id == …` / `AgentRun.user_id == …` ownership filter.

The test is an AST census (variant 12's rule): every reader of `.subject` on a context is in an
allowlist of accounting modules; a new reader outside it is red.

---

## 3. Transport is the only writer

The entry says it and `CLI-EXEC-SURFACE-1` §8 repeats it: *transports own identity*. So the
field is set where a request enters — the route dependency that builds the context (from a
header, `X-AINDY-Subject`, honoured **only** for API-key principals, never for a browser session,
because a session cookie's subject is the session), the MCP server (from the client's
declared `end_user`), and the agent runtime (inherited from the run's creating request and
carried on `AgentRun.metadata["subject"]` — no column). A syscall handler may read it and
must not set it; `child_context()` copies it and may not change it (the same narrow-only rule
as `AUTHORITY-VALUE-1`'s clamp).

## 4. What not to build

- **Not a `User` row for the peer** — the entry's own line; a row is an authenticated thing.
- **Not memory namespacing by subject** — §1; the first inbound consumer will ask for it,
  and the answer is that the operator's memory is the operator's, partitioned by the
  operator's own tags if they wish.
- **Not a validated subject registry** — validation would make it authenticated, and then it
  is `user_id`.
- **Not a column on `system_events` / `agent_runs`** — payload/metadata keys; a column is a
  schema bump for a label.

## 5. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. `SyscallContext.subject` — asserted, transport-set, `None` by default; `user_id` stays the
   authenticated owner of authorisation, ownership and memory.
2. Readers are constrain-only and enumerated (quota sub-key, LLM attribution label, event
   payload key, `enduser.pseudo.id`); the forbidden set is pinned by an AST census.
3. Honoured from `X-AINDY-Subject` only for API-key principals; sessions never carry one.
4. No schema: payload and metadata keys only.

## 6. Tests

- A subject quota refuses a 3rd concurrent call for subject A while subject B and the
  tenant proceed; with the subject cap unset nothing changes (control).
- A subject cap set *higher* than the tenant cap admits nothing the tenant cap refuses
  (the constrain-only property, as a test).
- Memory recall with a subject set returns the operator's namespace, identical to without
  (pinned equality).
- AST census: `.subject` readers ⊆ allowlist, non-empty; add a reader in
  `capability_service.py` → red (mutation).
- Session principal with the header set → subject is `None`.
