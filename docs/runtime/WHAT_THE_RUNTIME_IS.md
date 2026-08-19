---
title: "What aindy-runtime Is"
api_version: "1.0"
last_verified: "2026-08-17"
status: current
owner: "platform-team"
---
# What aindy-runtime Is

> **The app owns the formulas. The runtime owns the loop.**

This document exists because nothing else states the runtime's *category*. The README lists
components; `RUNTIME_MODULE_MAP.md` tags directories; `PUBLIC_RUNTIME_SURFACES.md` declares
stability. All useful, none of them answer *"what is this, and what would I gain by building on
it?"*

It is deliberately not a feature list. Feature lists read like every other agent framework's,
and they age into the thing this repository keeps catching itself doing — describing a mechanism
while the binding says something else. Every claim below names the mechanism that makes it true,
and §6 states plainly where the claims stop.

---

## 1. The loop this exists to run

The system was built to support a closed loop
(`aindy-apps-monolith/docs/apps/INFINITY_ALGORITHM_SUPPORT_SYSTEM.md`):

```
observe → score → adjust → execute → observe
```

Its operational truth, in that document's words: without the support layer the algorithm is a
*measurement system*; with it, it becomes a *self-improving execution engine*.

**The consequence for this repository is the whole point of this document.** The support layer is
not app code. Building it forced the loop's primitives down into the runtime as **generic
mechanisms**, so anything mounted on the runtime inherits them without writing any of it:

| Loop stage | Runtime primitive |
|---|---|
| observe | the **watcher** — `platform_layer/watcher_service.py`, `watcher_contract.py`, `routes/watcher_router.py` |
| recall | `RECALL_USED` (`recall.used`) |
| score | `SCORE_COMPUTED` (`score.computed`) |
| adjust | `NEXT_ACTION_CHOSEN` (`next_action.chosen`) |
| execute | `NEXT_ACTION_DISPATCHED` (`next_action.dispatched`) |

Those are entries in `AINDY/core/system_event_types.py` — **runtime event types, not application
concepts.** An app supplies the scoring formulas and the policy; the runtime supplies observation,
the signal path, the causal record, and the re-execution.

That division is why the loop is reusable: the formulas are domain logic and belong app-side, and
the machinery that makes a loop *close* — durably, across restarts, with provenance — is exactly
what a substrate should own.

---

## 2. The nouns this project under-advertises

**`ExecutionUnit` — a first-class execution unit, not a DAG node.** The README advertises DAGs, a
shape every workflow library has. The noun is the interesting part:

- a real status machine — `pending | executing | waiting | resumed | completed | failed`, with
  **`waiting` and `resumed` distinct** so an audit query can tell a fresh run from a resumption;
- quota-bounded per tenant, claimed and finalised by the execution pipeline;
- and it carries **provenance**: `memory_context_ids` and `output_memory_ids`
  (`db/models/execution_unit.py`), whose own docstring says they *"close the memory feedback
  loop."*

That last property answers a question most systems cannot: **what did this execution know going
in, and what did it produce going out?** — from a single row.

**Memory is on the execution path, not beside it.** "Persistent vector storage with hybrid
retrieval" describes a RAG library. What is actually here:

- memory is **addressable** — `/memory/{tenant}/{namespace}/{type}/{id}` (`MEMORY_ADDRESS_SPACE.md`);
- it **accumulates and is scored** — impact, usage, causal depth — and those scores feed decisions;
- it is **joined to execution** through the provenance columns above;
- and the scoring path is hot enough to justify a **native kernel**: a Rust `cdylib`
  (`AINDY/memory/native/memory_bridge_rs`) with a C++ semantic kernel (`memory_cpp/semantic.cpp`)
  compiled through `build.rs`, with a Python fallback and parity tests pinning them equal.

The significance of the native scorer is **not speed**. It is the tell: nobody writes a Rust/C++
kernel for something that sits beside the request path. It is there because scoring is *in* the
loop.

---

## 3. What building the loop forced in — six categories

Each is a product category in its own right. The claim is not parity with the best system in each
row; it is that all six are present in one substrate, which is unusual.

| Category | What is actually here |
|---|---|
| **Durable execution** | `FlowRun` checkpointing, WAIT/RESUME as a first-class lifecycle state, `WaitingFlowRun` with timeouts, boot-time rehydration, DLQ, `RetryPolicy`, lease-elected background leadership |
| **OS-shaped primitives** | a versioned syscall ABI (`sys.v1.<domain>.<action>`) behind one `SyscallDispatcher`, capabilities, tenant isolation, `ResourceManager` quota, a priority-lane scheduler, an event bus |
| **Agent runtime** | goal → plan → **approval that mints authority** → capability token → execute → verify → **undo**, with a computed capability ceiling on delegation (clamp opt-in — §6) |
| **Observation** | the watcher — runtime-owned, and the support-system document marks it CRITICAL |
| **Platform** | operator SPA at `/platform`, REST + SDK, OTel + Prometheus, health and degraded-mode matrix |
| **A first-party language** | **Nodus** — a language built from scratch, with its own stdlib and a runtime execution adapter, not a DSL-shaped config format |

Two of these are rare on their own. The combination — plus a memory system that accumulates — is
what the comparison audits kept finding their reference systems had reinvented more shallowly.

**★ That claim is now measured rather than asserted.** Fourteen systems were audited against this
runtime (`COMPARATIVE_RESEARCH_INDEX.md` §5b). Every serious one grows, buys or hand-rolls **part**
of a runtime — Pi grew durable sessions with migrations and fenced leases; OpenClaw grew Docker
isolation and cron; Codex grew 72 000 lines of three-OS sandboxing; MAF *bought* durability from
Azure Durable Task; MetaGPT grew a cost governor. **None assembles the whole set, and each grows
the part that hurt first.** The sharpest instance is a single stack split across two teams:
OpenClaw took isolation and scheduling in February, and Pi — its own dependency — grew transport
and durable sessions five months later, with neither holding the other's half.

**What that licenses and what it does not.** It licenses *the category is real, by construction* —
nine independent teams each built a piece. It does **not** license *"therefore they will adopt
ours"*: a serious framework does not wait for a substrate, it grows one, and this runtime still has
one first-party consumer talking to it over HTTP (`SUBSTRATE-WITNESS-1`). **Category validation and
adoption are different claims; only the first is evidenced.**

---

## 4. What a consumer inherits by default

The question worth answering is not *"what features does it have"* but *"what would I stop having
to build?"* Concretely, for the kinds of systems that have been audited against it:

| A system doing this | Inherits |
|---|---|
| Executing model-authored code | containment posture, an **effect ledger** and `sys.v1.agent.undo`, an approval gate that mints scoped authority |
| Long-horizon autonomous work | suspension that **survives a restart**, retry policy, orphan-run recovery, provenance of what a long run knew |
| Multi-agent delegation | a capability ceiling on delegation, and one causal graph across agents instead of per-agent transcripts — see §6 on which half of the ceiling is on by default |
| Work isolated per caller | caller-scoped execution and memory paths, cross-instance quota, lease-elected background work — *caller* isolation; see §6 before reading this as multi-tenancy |
| Anything that should improve with use | the loop primitives in §1 — for free |

The last row is the differentiator. Durability and authority can be assembled from existing
infrastructure. A substrate where **executions accumulate into memory that scores and feeds the
next decision** is not something a consumer can bolt on afterwards, because it has to be recorded
at execution time or it is lost.

---

## 5. What this is not

- **Not the algorithm.** Domain formulas, KPI weighting and policy are app-side by design; that
  boundary was settled long ago and remains correct.
- **Not a finished application.** A bare install gives the execution layer and operator surfaces.
- **Not an agent framework.** It ships a deterministic reference planner to prove the seam works
  without a model. Prompt construction, context assembly, compaction and conversation state are
  deliberately outside it — a conclusion three independent comparison audits reached separately.
- **Not an orchestration language, and this one has been tested rather than argued.** Composition
  belongs in Nodus; the runtime supplies dispatch, durability and authority underneath it. A
  CrewAI hierarchical crew expressed as a 39-line `.nd` flow survived four successive rounds of
  host deepening — a real LLM provider, a real MCP client↔server transport, cross-process A2A with
  bearer auth, and scope-addressed memory — with the flow file **unchanged**. The transport, the
  model, the auth and the memory backend all moved; the orchestration expression did not. *(Source:
  `C:\codev\nodus-showcase-crewai`; the invariance is corroborated by file mtimes — the flow dates
  to 2026-06-24 while every host-wiring file dates to 2026-07-09. Recorded in `TECH_DEBT.md` under
  `CREWAI-NODUS-2026-08-18`.)* **The honest limit on that result: the showcase never routes through
  `sys()`, so it demonstrates the composition boundary and says nothing about the authority
  boundary — see §6 and `SUBSTRATE-WITNESS-1`.**

---

## 6. Where the claims stop

This section is not a disclaimer. It is the reason the rest is believable: the runtime's own
`sandbox_runner_assurance_posture()` reports `ASSURANCE_CEILING_NO_ISOLATION_GUARANTEE` rather
than overclaiming, and this document holds itself to the same standard.

- **Durable execution is a category comparison, not parity.** A dedicated durable-execution engine
  offers determinism-and-replay. This offers **at-least-once with resumability at node
  granularity**, **at-most-once for effects that pass a ledgered chokepoint**, duplicate
  suppression under concurrent delivery, and at-most-one background leader bounded by a lease TTL.
  It does not offer exactly-once and does not claim to.
- **The effect ledger is opt-in today.** See `IDEM-11` — the gate is off by default. *Corrected
  2026-08-17: this said "few syscalls declare their execution guarantee." The per-syscall audit is
  done and **eight** now declare `EXACTLY_ONCE` (`memory.write`, `memory.link`, `flow.run`,
  `event.emit`, `flow.execute_intent`, `nodus.execute`, `job.submit`, `agent.undo`), so coverage is
  no longer the limiter — the flag is.* Duplicate-effect exposure in default configuration is real
  until it is flipped, and the flip is a soak decision (§6.1), not a build.
- **The isolation posture is Tier-1.** Trusted, first-party code in-process is a stated design
  position, not an accident. `GUEST-CONFINE-1` closed 2026-08-15, so the guest VM is now confined;
  `TOOL-SEAM-ISOLATION-1` and `EXEC-ENV-BIND-1` remain open, which means the isolation provider is
  bound to the extension boundary and **the tool seam is the one place foreign code still runs
  in-process with ambient authority.** Do not read "OS-shaped" as "safe for untrusted code."
- **The capability ceiling on delegation is opt-in.** `AUTHORITY-VALUE-1` — `child_context()` can
  widen a capability set, and the clamp that prevents it ships behind
  `AINDY_CHILD_CONTEXT_CLAMP`, **default off**. A widening is logged at WARNING either way, so the
  exposure is countable. §4 lists the ceiling as inherited; this is the qualifier on that row.
- **"Multi-tenant" would be an overclaim; "caller-isolated" is accurate.** Isolation is enforced
  by requiring an authenticated caller at the dispatcher and scoping memory paths to it; two
  tables carry `tenant_id` and both document `tenant_id == user_id`, a single-user-per-tenant
  model. Real multi-tenancy — billing identity decoupled from user identity, per-tenant quota
  enforcement, data residency — is tracked as `DEPLOY-TARGET-2`, `TENANT-2`, `BILLING-1` and
  `DATA-1`, all open.
- **The effect model is thinner than the authority model.** The runtime answers *who is allowed to
  do this* with cryptographic and transactional rigour, and answers *what exactly was done, to
  what version of what, and how much of it succeeded* with a two-state envelope. Open:
  `EFFECT-PARTIAL-1` (no partial-success state), `EFFECT-PRECONDITION-1` (an effect cannot declare
  the version of the world it expects), `FS-SCOPE-1` (the capability vocabulary is verb-shaped and
  cannot name a resource). This is the runtime's weakest axis and it was found by holding it
  against an external system rather than by internal audit.
- **Capability enforcement does not reach every surface, but the gap is much smaller than it
  was.** `HTTP-SCOPE-GAP-1`: a census on a booted app counts **91 scope-gated / 12 admin / 21
  public / 2 identity-only of 126 routes**. The remainder is a design question — `execution.read`
  conflates scope with data ownership, and a scope cannot answer *"may I read someone else's"*.
- **There is no intra-execution parallelism.** `FLOW-PARALLEL-1` — the flow engine advances one
  node at a time.
- **There is no supported profile below `single-instance`, and it requires PostgreSQL.**
  `EMBEDDED-FLOOR-1`. A consumer shaped like a library in a terminal — no server, no daemon, no
  database — is out of contract. See the note immediately below on what that does and does not
  mean.

Every item above is tracked in `TECH_DEBT.md` with its evidence. A positioning document that
omitted them would be the same failure as a security matrix describing enforcement the code does
not perform.

### 6.1 What is gated by capability, and what is gated by soak

**These are not the same kind of "not yet," and collapsing them makes the runtime read as less
finished than it is.**

A large share of what §6 lists is **built, tested, and shipped behind a default-off flag** — the
at-most-once effect gate (`AINDY_SYSCALL_IDEMPOTENCY`, with eight syscalls now declaring their
guarantee), durable crash continuation (`AINDY_DURABLE_CONTINUATION`), delegation-scoped private
memory (`AINDY_DELEGATION_PRIVATE_MEMORY`), the delegation capability clamp
(`AINDY_CHILD_CONTEXT_CLAMP`), async heavy execution (`AINDY_ASYNC_HEAVY_EXECUTION`), the Nodus
warm pool, own-session memory recall. **None of those is waiting on engineering.** They are
waiting on production soak, and the runtime currently has one maintainer and one consuming
application, so soak time is the scarce input — not design, not build effort, and not a missing
capability. An operator willing to run that soak on their own deployment can turn any of them on
today; the flags exist precisely so that choice belongs to the deployer.

`EMBEDDED-FLOOR-1` is the same shape and worth saying plainly: **nothing found so far says the
single-process case *requires* PostgreSQL in a way SQLite could not serve.** `AINDY_ALLOW_SQLITE`
exists and the entire unit suite runs on it. What is missing is a profile that *declares* the
reduced guarantees and a test tier that *asserts* them — bounded work, not invention. The floor
is where it is because no one has needed a lower one, not because a lower one is out of reach.

**The honest boundary between the two categories:** `TOOL-SEAM-ISOLATION-1`, `FS-SCOPE-1`,
`EFFECT-PARTIAL-1`, `EFFECT-PRECONDITION-1` and `FLOW-PARALLEL-1` are genuine capability gaps —
something must be designed and built. Everything in the paragraphs above is a deployment
decision someone else is equally able to make. `PERF-BASELINE-1` sits between them: the flag
backlog is blocked on evidence, and no instrument currently exists to produce it, which is why
it is filed P1.

---

## Sources

Claims here were read against source on `last_verified`, not inherited from documentation.
Deeper detail lives in: `MEMORY_ADDRESS_SPACE.md`, `NATIVE_MEMORY_BRIDGE.md`,
`EXECUTION_INVARIANTS.md`, `PUBLIC_RUNTIME_SURFACES.md`, `SECURITY_MATRIX.md`, and
`TECH_DEBT.md` for anything open.
