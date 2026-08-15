---
title: "What aindy-runtime Is"
api_version: "1.0"
last_verified: "2026-08-15"
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
| **Agent runtime** | goal → plan → **approval that mints authority** → capability token → execute → verify → **undo**, with delegation clamped by a computed capability ceiling |
| **Observation** | the watcher — runtime-owned, and the support-system document marks it CRITICAL |
| **Platform** | operator SPA at `/platform`, REST + SDK, OTel + Prometheus, health and degraded-mode matrix |
| **A first-party language** | **Nodus** — a language built from scratch, with its own stdlib and a runtime execution adapter, not a DSL-shaped config format |

Two of these are rare on their own. The combination — plus a memory system that accumulates — is
what the comparison audits kept finding their reference systems had reinvented more shallowly.

---

## 4. What a consumer inherits by default

The question worth answering is not *"what features does it have"* but *"what would I stop having
to build?"* Concretely, for the kinds of systems that have been audited against it:

| A system doing this | Inherits |
|---|---|
| Executing model-authored code | containment posture, an **effect ledger** and `sys.v1.agent.undo`, an approval gate that mints scoped authority |
| Long-horizon autonomous work | suspension that **survives a restart**, retry policy, orphan-run recovery, provenance of what a long run knew |
| Multi-agent delegation | delegation that **cannot escalate** (capability ceiling), and one causal graph across agents instead of per-agent transcripts |
| Anything multi-tenant | tenant isolation, cross-instance quota, lease-elected background work |
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
- **The effect ledger is opt-in today.** See `IDEM-11` — the gate is off by default and few
  syscalls declare their execution guarantee. Duplicate-effect exposure in default configuration
  is real until that changes.
- **The isolation posture is Tier-1.** Trusted, first-party code in-process is a stated design
  position, not an accident. But three open findings — `GUEST-CONFINE-1`, `TOOL-SEAM-ISOLATION-1`,
  `EXEC-ENV-BIND-1` — mean the isolation provider is bound to the extension boundary and not to
  the guest or tool seams. Do not read "OS-shaped" as "safe for untrusted code."
- **Capability enforcement does not yet reach every surface.** `HTTP-SCOPE-GAP-1`: scope checks
  are applied to a small minority of HTTP routes, and a decision is recorded there to stop JWTs
  bypassing scopes.
- **There is no intra-execution parallelism.** `FLOW-PARALLEL-1` — the flow engine advances one
  node at a time.

Every item above is tracked in `TECH_DEBT.md` with its evidence. A positioning document that
omitted them would be the same failure as a security matrix describing enforcement the code does
not perform.

---

## Sources

Claims here were read against source on `last_verified`, not inherited from documentation.
Deeper detail lives in: `MEMORY_ADDRESS_SPACE.md`, `NATIVE_MEMORY_BRIDGE.md`,
`EXECUTION_INVARIANTS.md`, `PUBLIC_RUNTIME_SURFACES.md`, `SECURITY_MATRIX.md`, and
`TECH_DEBT.md` for anything open.
