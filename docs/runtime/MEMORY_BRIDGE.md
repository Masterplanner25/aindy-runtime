---
title: "Memory Bridge"
last_verified: "2026-08-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
﻿# Memory Bridge - Canonical Definition & Evolution Plan

---

> ## Verified against source 2026-08-13 (DOCS-STALE-1)
>
> The **model** is sound — the lifecycle, the five storage tables, the layering and the phase
> framing all check out, and §7's "Partial" on Phase v1 is **still honestly Partial** (both
> `MemoryNodeDAO` classes and `save_memory_node` are still present). Corrections are localised:
>
> - **Every `services/…` path in this file is wrong.** `AINDY/services/` contains exactly one
>   module, `auth_service.py`. Nine citations pointed there; seven relocate, one is
>   roadmap-only, one is app-owned. Fixed throughout §3, §7 and §10.
> - **One open debt is resolved.** Embedding is *not* synchronous on the write path any more —
>   `MemoryNodeDAO.save` sets `embedding_pending=True` and enqueues. That also completes §10
>   Step 4.
> - **The metrics endpoints are plugin-layer, not runtime.** `/memory/metrics*` is not served by
>   a bare runtime.
> - **"RippleTrace" is a name the runtime no longer owns** — the primitive is `EventEdge`. The
>   mechanism this document describes is real; only the label moved.
>
> §11 claims this file is "the canonical reference for Memory Bridge architecture". Treat that as
> aspirational: it is a **design-and-roadmap** document, and `MEMORY_ADDRESS_SPACE.md`,
> `NATIVE_MEMORY_BRIDGE.md` and `EXECUTION_INVARIANTS.md` are narrower and source-verified.

---

## 1. System Definition (Canonical)

The Memory Bridge is a **memory execution engine**: a persistence, recall, and feedback system that embeds continuity directly into AI execution.

It is not a storage layer.

It is a **memory orchestration system** designed to:

* enforce contextual continuity
* preserve authorship and identity
* improve execution through feedback-driven recall

---

## 2. Core Lifecycle (Canonical Pipeline)

```
MemoryNode -> Trace -> Recall -> Resonance -> Continuity
```

### MemoryNode

Atomic unit of memory:

* content
* tags (JSONB)
* embeddings (pgvector)
* node_type
* causal references (`source_event_id`, `root_event_id`)
* causal scoring (`causal_depth`, `impact_score`)
* `memory_type` (`decision`, `outcome`, `failure`, `insight`)
* feedback signals (usage, success rate, weight)

---

### Trace (Critical Abstraction)

Ordered sequence of MemoryNodes representing **continuity over time**.

Defines:

* sequence
* causality
* narrative structure

Trace is the **missing link between memory and meaning**.

Current implementation note:
* Trace continuity is complemented by execution-side causality from the `EventEdge` /
  `SystemEvent` primitives, so memory can store not just sequence but source event, root cause,
  and downstream impact.

  > *Naming, corrected 2026-08-13.* This said "RippleTrace". Per TECH_DEBT **RTR-7**, that name
  > was **deliberately dissolved into the `EventEdge` primitive** — the runtime half is closed and
  > the mechanism is real, but there is no RippleTrace component to point at. What survives is
  > surface naming: a `GET /observability/rippletrace/status` route that proxies an *app-registered*
  > health check and reports `"rippletrace health check not registered"` on a bare runtime.

---

### Recall

Retrieves candidate memory using:

* semantic similarity (embeddings)
* graph relationships (links)
* trace context (sequence proximity)

---

### Resonance

Deterministic scoring pipeline combining:

* semantic similarity
* graph strength
* trace context
* recency
* feedback signals
* impact weighting from causal memory

---

### Continuity

Final stage where:

* memory is injected into execution
* execution produces new memory
* feedback updates future recall

---

## 3. Core Components

### MemoryNodes

* Stored in: `memory_nodes`
* Represent atomic memory units

---

### Traces

* Stored in:

  * `memory_traces`
  * `memory_trace_nodes`
* Represent ordered continuity structures

---

### Graph (Links)

* Stored in: `memory_links`
* Directed edges with numeric weight (legacy strength retained)
* Used for traversal and context expansion

---

### Resonance Engine

* Central scoring pipeline
* Combines all memory signals into ranked recall
* Trace-aware bonus applied when `trace_id` is provided in recall metadata

---

### Memory Metrics

* Stored in: `memory_metrics`
* Captures per-run impact signals (impact_score, memory_count, avg_similarity)
* Exposed via `GET /apps/memory/metrics`, `/apps/memory/metrics/detail`,
  `/apps/memory/metrics/dashboard` — **and not by a bare runtime.** *(The prefix was also wrong
  here: the doc said `/memory/metrics`.)* *Corrected 2026-08-13:* all three live in
  `AINDY/routes/memory_metrics_router.py`, which `AINDY/routes/__init__.py` records as
  **moved to the plugin layer**; it is not in `APP_ROUTERS`. Same shape as the `/apps/agent/*`
  correction in `PUBLIC_RUNTIME_SURFACES.md` — the file is still in the tree, so the routes look
  runtime-owned until you boot the runtime alone. `ROUTE_OWNERSHIP_INVENTORY.md` already
  recorded this — canonical owner `apps/memory/routes/memory_metrics_router.py`, extracted
  2026-06-06.

---

### Execution Loop

Enforced lifecycle:

```
recall -> execute -> capture -> feedback
```

Current implementation note:
* `runtime/memory/orchestrator.py` coordinates recall (strategy -> scoring -> filtering -> token budget).
* `runtime/memory/memory_feedback.py` records usage/success signals.
* `runtime/memory_loop.py` wraps recall -> execute -> capture -> feedback (pluggable executor).
* `runtime/memory/memory_learning.py` updates per-execution success_rate and low-value flags to adapt recall quality.
* `runtime/memory/memory_metrics.py` + `runtime/memory/metrics_store.py` compute and persist memory impact metrics.
* *(2026-08-13)* This line claimed `tests/system/test_memory_loop_e2e.py` validates the full
  loop. That file has never existed in either repo, and `tests/system/` is not a directory
  in this one. End-to-end loop coverage is unestablished.
* `AINDY/memory/memory_capture_engine.py` auto-captures high-impact `SystemEvent` outcomes into
  causal memory records and links them back via `EventEdge` relationships
  (`relationship_type="stored_as_memory"`, `:406`). *Corrected 2026-08-13: the path was
  `services/…` and the target was named "RippleTrace".*

---

## 4. Architectural Layers

### Storage Layer

* PostgreSQL
* JSONB (tags)
* pgvector (embeddings, HNSW index)
* graph links
* trace tables

---

### Orchestration Layer

* Python / FastAPI
* DAOs
* API routes
* execution hooks
* Memory Orchestrator (recall orchestration + context building)
* Memory Feedback Engine (usage/success recording)
* Execution Loop wrapper (recall -> execute -> capture -> feedback)
* Memory Metrics Engine (impact scoring + persistence)

---

### Engine Layer (Planned)

* Rust (PyO3)
* C++ (FFI via Rust)

Used for:

* similarity
* traversal
* scoring

---

## 5. Behavioral Guarantees

At runtime, the system guarantees:

* Memory-informed execution exists for selected execution paths
* Memory-producing execution exists for selected execution paths
* Feedback updates future recall
* Traces preserve ordered continuity
* Retrieval is explainable (resonance scoring)
* High-impact execution outcomes can now be stored with explicit causal provenance

---

## 6. System Classification

The Memory Bridge is:

> A hybrid memory execution engine that enforces continuity through structured memory, trace sequencing, and feedback-driven recall.

It is NOT:

* a vector database
* a RAG system
* a passive memory store

---

## 7. Evolution Plan (System Roadmap)

---

### Phase v1 - Canonical Unification (FOUNDATION)

**Goal:** Single source of truth

**Actions:**

* Remove legacy DAO (`memory.memory_persistence.MemoryNodeDAO`)
* Standardize all operations on `db/dao/memory_node_dao.py`
* Eliminate dual write paths (`bridge/*` vs `/memory/*`)
* Normalize schema:

  * `node_type` (nullable vs default)
  * `tags` (JSONB consistency)
* Remove dead code (`save_memory_node`)

**Outcome:**

* Stable, predictable memory layer
* No behavioral drift between pathways

**Status:** Partial

---

### Phase v2 - Trace Layer (CORE COMPLETION)

**Goal:** Implement continuity structure

**Actions:**

* Create tables:

  * `memory_traces`
  * `memory_trace_nodes`
* Implement Trace DAO
* Add API endpoints:

  * create trace
  * append to trace
  * retrieve trace
* Auto-link sequential nodes (`trace_sequence`)

**Outcome:**

* Memory becomes ordered and contextual
* Continuity becomes technically enforceable

**Status:** Complete

---

### Phase v3 - Symbolic Integration

**Goal:** Unify symbolic and operational memory

**Actions:**

* Ingest:

  * `memorytraces/`
  * `memoryevents/`
  * external docs
* Convert artifacts into:

  * MemoryNodes
  * Traces
* Preserve metadata:

  * file path
  * timestamps
  * canonical IDs

**Outcome:**

* Symbolic memory becomes queryable
* Identity/continuity anchors enter runtime system

**Status:** Complete

---

### Phase v4 - Resonance Engine

**Goal:** Replace ad-hoc recall with unified scoring

**Actions:**

* Implemented scoring and ranking in `runtime/memory/scorer.py`
* Integrated into `runtime/memory/orchestrator.py` pipeline
* Combines semantic, graph, trace, recency, feedback, and impact signals

**Outcome:**

* Deterministic, explainable memory ranking
* Improved recall quality

**Status:** Complete

### Phase v4.5 - Causal Memory Integration

**Goal:** Attach meaning to memory via execution causality

**Actions:**

* add causal fields to `memory_nodes`
* auto-capture high-impact `SystemEvent` outcomes into memory
* compute `impact_score` from downstream span and depth over the `EventEdge` causal graph
  *(was: "RippleTrace downstream span")*
* create `stored_as_memory` edges from event -> memory node — **verified present**
  (`memory_capture_engine.py:406`, `platform_layer/event_trace_service.py`)
* use impact-aware scoring during recall

**Outcome:**

* memory stores what happened, why it happened, and what it caused
* causal memory can influence future execution decisions

**Status:** Complete

---

### Phase v5 - Execution Loop Enforcement

**Goal:** Make memory unavoidable

**Actions:**

* Implemented `runtime/memory_loop.py`
* Enforced `recall -> execute -> capture -> feedback`
* Routed `/memory/execute` and workflow handlers via execution registry

**Outcome:**

* Memory becomes part of execution, not optional
* Closed-loop learning system

**Status:** Partial

---

### Phase v5+ - Engine Layer (Performance)

**Goal:** High-performance memory engine

**Actions:**

* Create abstraction: `services/memory_engine.py`
* Integrate:

  * Rust (PyO3)
  * C++ (via Rust)
* Offload:

  * traversal
  * similarity
  * scoring

**Outcome:**

* Scalable, high-performance memory system
* Engine-level optimization without architecture change

---

## 8. Technical Debt (Current State)

### Open Debt

* **Unverifiable from source (2026-08-13):** legacy `node_type="generic"` cleanup on existing
  rows. `VALID_NODE_TYPES` is `{decision, outcome, insight, relationship}` and the string
  `"generic"` appears nowhere in the memory layer, so current code cannot produce such a row.
  Whether historical rows carry it is a data question, not a code one — see also **MEM-NODETYPE-1**
  (closed 2026-06-27), which fixed a *different* invalid default.
* ✅ **Resolved (verified 2026-08-13):** embedding generation is **no longer synchronous on the
  write path.** `MemoryNodeDAO.save` writes with `embedding_pending=True` and calls
  `_enqueue_embedding(...)` (`memory_node_dao.py:211`, `:267`); the work is drained by
  `memory/ingest_queue.py` → `memory/embedding_jobs.py`. This also completes §10 Step 4.
  **Related and still live:** `RT-MEMTXN-LEAK-1` — the async path had its own failure mode, an
  unbounded capture → job → capture cascade that exhausted the connection pool. Fixed on three
  axes; read that entry before touching this path.
* ✅ **Resolved:** HMAC removed from bridge write endpoints; JWT only.
* Engine Layer (Rust/C++) integrated into runtime scoring with Python fallback; traversal-side
  acceleration and release-build hardening remain open. *Update:* `Native Crate Build (Rust)` is
  now a required check (NATIVE-CI-1) — it proves the crate **compiles**, and asserts nothing about
  scoring behaviour. See `NATIVE_MEMORY_BRIDGE.md`.
* Execution-loop enforcement is not universal across all runtime paths
* End-to-end validation for the causal-memory path is still missing — and is now recorded as
  **DOCS-COVERAGE-CLAIM-1**, since this document previously claimed a
  `tests/system/test_memory_loop_e2e.py` that never existed. *(Was: "the new RippleTrace ->
  Memory Bridge -> Infinity path".)*

---

## 9. Memory Bridge Phase Mapping

| Phase | Component            | Status   | Required Action        |
| ----- | -------------------- | -------- | ---------------------- |
| v1    | DAO + Schema         | Partial  | Finish canonical unification |
| v2    | Trace Layer          | Complete | Maintenance only       |
| v3    | Symbolic Integration | Complete | Maintenance only       |
| v4    | Resonance Engine     | Complete | Tune/extend as needed  |
| v4.5  | Causal Memory        | Complete | Add stronger scenario tests |
| v5    | Execution Loop       | Partial  | Expand workflow usage  |
| v5+   | Engine Layer         | Partial  | Runtime scoring integrated; traversal + release hardening remain. `services/memory_engine.py` (§7) was never created — the abstraction landed as `runtime/memory/native_scorer.py` + `scorer.py` fallback |

---

## 10. Next Steps

> **All paths in this section were corrected 2026-08-13.** They pointed at `AINDY/services/`,
> which contains exactly one module (`auth_service.py`). Seven relocate, one was never built, one
> is app-owned.

### Step 1 - Finish canonical DAO unification
**Files:** `AINDY/memory/memory_persistence.py`, `AINDY/db/dao/memory_node_dao.py`, bridge memory helper paths
**Outcome:** all memory writes and queries use the canonical DAO without compatibility drift.
**Still open — confirmed 2026-08-13:** there really are two `MemoryNodeDAO` classes
(`db/dao/memory_node_dao.py:32` and `memory/memory_persistence.py:239`), and the dead
`save_memory_node` is still at `memory_persistence.py:260`. §7's "Partial" is accurate.

### Step 2 - Expand trace usage in recall
**Files:** `AINDY/db/dao/memory_trace_dao.py`, `AINDY/runtime/memory/orchestrator.py`, `AINDY/runtime/memory/scorer.py` — all three verified present  
**Outcome:** trace context affects recall more meaningfully than a flat bonus on matching nodes.

### Step 3 - Route more execution through the memory loop
**Files:** `AINDY/runtime/memory_loop.py`, `AINDY/runtime/flow_engine/` (a package), `AINDY/agents/agent_runtime/` (a package)  
**Outcome:** memory-informed execution becomes true for a larger share of runtime behavior.

### Step 4 - Move embeddings off the synchronous write path — ✅ **DONE**
**Files:** `AINDY/memory/embedding_service.py`, `AINDY/db/dao/memory_node_dao.py`,
`AINDY/memory/ingest_queue.py`, `AINDY/memory/embedding_jobs.py`, `AINDY/memory/memory_ingest_service.py`
**Outcome:** achieved — `save()` sets `embedding_pending=True` and enqueues rather than embedding
inline. See §8; note `RT-MEMTXN-LEAK-1` for the failure modes this path then developed.

### Step 5 - Add end-to-end causal-memory validation
**Files:** tests around `AINDY/memory/memory_capture_engine.py`, `AINDY/core/system_event_service.py`, `AINDY/memory/memory_scoring_service.py`, and — **in `aindy-apps-monolith`, not this repo** — `apps/analytics/services/infinity_orchestrator.py`  
**Outcome:** a high-impact failure can be shown to become memory and influence a later decision path.

---

## 11. Governance Notes

* This document is the **canonical reference** for Memory Bridge *architecture and roadmap*.
  *Qualified 2026-08-13:* it is a design document, not a source-verified API reference. Where it
  conflicts with `MEMORY_ADDRESS_SPACE.md`, `NATIVE_MEMORY_BRIDGE.md`, `EXECUTION_INVARIANTS.md`
  or `IDEMPOTENCY_CONTRACT.md`, those are narrower and newer — prefer them, per the docset
  precedence rule in `RUNTIME_DOCSET_GOVERNANCE.md`.
* All future changes must align with:

  * the lifecycle pipeline
  * single-source memory model
  * execution loop enforcement
* Any deviation must be documented in CHANGELOG and ARCHITECTURE updates

---

## 12. Summary (Operational Truth)

The Memory Bridge is not complete when it stores memory.

It is complete when:

> Memory directly shapes execution, and execution continuously reshapes memory through traceable continuity.

