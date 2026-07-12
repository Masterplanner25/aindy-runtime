---
title: "ECOGAP-3 — Provider Breadth Program (Embedding SPOF + LLM Breadth)"
api_version: "1.0"
last_verified: "2026-07-12"
status: current
owner: "platform-team"
---

# ECOGAP-3 — Provider Breadth Program

> Scope + implementation plan for ECOGAP-3 / `MEMORY-EMBEDDING-PROVIDER-1` / G3.
> **Sequencing decision (owner):** Phase 1 (embedding SPOF) ships first, then Phase 2 (LLM
> breadth) — for both planning and implementation. Both are real concerns; embedding leads
> because it is the harder, higher-value half and the only one with no existing seam.
> Tracking: `TECH_DEBT.md` §ECOGAP-3 + §MEMORY-EMBEDDING-PROVIDER-1; `ECOSYSTEM_CAPABILITY_GAPS.md` G3.

## 1. Why this is two halves at opposite readiness

ECOGAP-3 bundles two distinct gaps. They share the theme "don't be locked to OpenAI" but have
different seams, effort, and triggers:

| Half | State today | Effort | This program |
|---|---|---|---|
| **Embedding SPOF** — OpenAI is the *only* embedding backend; no abstraction | **No seam. Hardwired.** | **M–L** (dimensionality/migration is the hard part) | **Phase 1 (first)** |
| **LLM chat breadth** — only OpenAI + DeepSeek concretely in tree | **Seam already built** (AGENT-HARDEN-5) | **S–M** (write concrete clients behind an existing Protocol) | **Phase 2 (second)** |

### Non-goals

- Not changing the memory scoring / MAS / recall logic — only *where the vectors come from*.
- Not building every provider up front — each concrete provider is trigger-driven.
- Not a planner-backend change — `AINDY_AGENT_PLANNER_BACKEND` is orthogonal.

## 2. Current state — grounded in code (verified 2026-07-12)

### 2.1 Embedding path is hardwired, but funnels through two functions

Every embedding read and write in the codebase goes through exactly two functions in
`AINDY/memory/embedding_service.py`:

- `generate_embedding(text) -> list` (write path)
- `generate_query_embedding(query) -> list` (read/query path; returns a zero vector on failure)

Both call `AINDY.platform_layer.openai_client` directly (`create_embedding` / `get_openai_client`)
and hardcode `service_name="openai"`, `method="openai.embeddings"`. **This two-function funnel is
the clean insertion point for a provider abstraction** — no call site needs to change.

Call sites (all already routed through the funnel):
- Write: `embedding_jobs.py:103`, `db/dao/memory_node_dao.py` (create/update, `generate_embedding` /
  `regenerate_embedding` flags), `memory/memory_capture_engine.py:209`.
- Query: `runtime/flow_definitions_memory.py:191`, `db/dao/memory_node_dao.py:593`,
  `routes/memory_router.py:429`.

### 2.2 The dimensionality is baked into THREE places — this is the hard part

The `1536` dimension is not just a config value; it is committed to the schema and the SQL:

1. **ORM column** — `AINDY/memory/memory_persistence.py:41`: `embedding = Column(Vector(1536), nullable=True)`
2. **Service constants** — `AINDY/memory/embedding_service.py:26-27`:
   `EMBEDDING_MODEL = "text-embedding-ada-002"`, `EMBEDDING_DIMENSIONS = 1536`
3. **Similarity query cast** — `AINDY/db/dao/memory_node_dao.py:435`: `cast(query_embedding, Vector(1536))`

pgvector already shipped (that `Vector(1536)` column is live), so **existing stored vectors are
OpenAI-1536-dimensional**. A different embedding model (e.g. `all-MiniLM-L6-v2` = 384-dim) produces
vectors that are *geometrically incomparable* with what's stored. **A provider swap is therefore a
data-migration problem, not a clean seam-swap** — this is the crux Phase 1 must solve, and it is
what the old `MEMORY-EMBEDDING-PROVIDER-1` resolution sketch omits.

> Correction (verified during implementation): the `EMBEDDING_MODEL = "text-embedding-ada-002"`
> constant is **not** stale — the service passes it *explicitly* on every call, so **ada-002 is
> the live production model** (the `openai_client.py` default of `text-embedding-3-small` is
> never reached from this path). Both are 1536-dim. The new `OpenAIEmbeddingProvider` therefore
> defaults to `ada-002` (`AINDY_EMBEDDING_OPENAI_MODEL`) to preserve behavior exactly.

### 2.3 LLM chat path already has the seam (AGENT-HARDEN-5)

`AINDY/platform_layer/llm_client.py` already provides: `LLMClient` Protocol,
`CircuitBreakerLLMClient`, `FallbackLLMClient`, `get_llm_client(provider)`,
`resolve_provider_chain()`, `get_llm_client_chain()`, config-driven via `LLM_PROVIDER` +
`LLM_FALLBACK_PROVIDERS`. Concrete clients today: OpenAI (`openai_client.py`), DeepSeek
(`deepseek_client.py`). Phase 2 is "add concrete clients behind this Protocol" — the architecture
is done.

---

## Implementation status

- **Phase 1 · Increment 1 (the seam) — BUILT (in working tree, uncommitted) 2026-07-12.**
  `AINDY/memory/embedding_providers.py` (`EmbeddingProvider` protocol + `OpenAIEmbeddingProvider`
  default + `LocalEmbeddingProvider` + `build_embedding_provider` + fail-closed
  `validate_embedding_configuration`); `embedding_service.py` refactored to dispatch through the
  provider while keeping all orchestration; `AINDY_EMBEDDING_*` settings + `.env.example` +
  `[embeddings-local]` extra; `1536` de-duplicated to `MEMORY_EMBEDDING_COLUMN_DIMENSIONS`
  (embedding_service + DAO cast; the ORM column literal stays for now — see below). 10 unit tests
  green (`tests/unit/test_embedding_providers.py`). **Zero behavior change on OpenAI-default
  deployments; no schema change.**
- **Phase 1 · Increment 2 (schema-configurable dimension + re-embed migration) — BUILT (in
  working tree, uncommitted) 2026-07-12.** The `memory_nodes.embedding` column dimension is now
  configurable via `AINDY_EMBEDDING_DIMENSIONS` (`memory_persistence.py` reads
  `resolve_embedding_column_dimensions()`; schema-contract bumped `2026-07-12 → 2026-07-12.1`,
  baseline regenerated, two test assertions updated). New `AINDY/memory/embedding_migration.py`
  `reembed_all_memory_nodes()` + `aindy-runtime memory reembed [--dry-run|--yes|--no-drain]`:
  validates provider↔column dimension fail-closed, NULLs vectors, `ALTER COLUMN ... TYPE
  vector(N)`, marks rows pending, and re-embeds (reusing the per-node job; single-pass drain so
  permanently-deferred empty-content rows can't loop). **Real-PostgreSQL verified**: seed at
  `vector(1536)` → reembed → `vector(8)`, rows re-embedded, `pending=false`.
  **Operational constraint (confirmed on PG):** run reembed in a process started with the target
  `AINDY_EMBEDDING_DIMENSIONS` — pgvector's ORM `Vector(N)` is fixed at import and its bind
  processor enforces width. The CLI satisfies this automatically. A local model (e.g. 384-dim
  MiniLM) is now usable end-to-end: set provider + dimension, run the migration.

- **Phase 2 (LLM hosted-provider breadth) — BUILT (in working tree, uncommitted) 2026-07-12.**
  The provider dispatch in `llm_client.py` is now an extensible registry
  (`_PROVIDER_FACTORIES` + `register_llm_provider()` + `registered_provider_names()`) —
  adding a provider is one factory, and `resolve_provider_chain`/`get_llm_client` read the
  registry. Two concrete providers ship behind the existing `FallbackLLMClient` seam:
  **Anthropic** (`anthropic_client.py`, official `anthropic` SDK / Messages API, optional
  `pip install aindy-runtime[anthropic]`) and **Azure OpenAI** (`azure_openai_client.py`,
  reuses the `openai` SDK — no new dependency). Config via `LLM_PROVIDER` /
  `LLM_FALLBACK_PROVIDERS` + `ANTHROPIC_*` / `AZURE_OPENAI_*`. 10 unit tests green;
  real-SDK verified (Azure + Anthropic construct; `get_llm_client_chain(["anthropic",
  "openai"])` composes a `FallbackLLMClient`). **Skill-verified correctness catch:** the
  Anthropic client deliberately does NOT forward `temperature` — sampling params return a
  400 on current Claude models (Opus 4.8 / Sonnet 5); it maps the OpenAI-style system
  message to the Messages API `system=` arg and defaults the required `max_tokens`. Default
  model `claude-opus-4-8`. No schema change. **ECOGAP-3 is now resolved at the mechanism
  level** (both halves shipped); remaining work is additional concrete providers
  (Gemini/Bedrock) on demand + flipping/soaking.

## 3. Phase 1 — Embedding provider abstraction (the SPOF)  ·  FIRST

**Goal:** memory embeddings can be produced by a configurable provider (OpenAI default, a local
model for air-gapped/regulated/cost-sensitive deployments), without locking every deployment to a
single vendor or stranding already-stored vectors.

### 3.1 The seam

Introduce an `EmbeddingProvider` protocol mirroring the `LLMClient` shape (keep the architecture
symmetric — do NOT invent a second dispatch pattern):

```python
class EmbeddingProvider(Protocol):
    name: str
    dimensions: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

- `OpenAIEmbeddingProvider` — default; wraps the existing `openai_client` path (1536-dim).
- `LocalEmbeddingProvider` — sentence-transformers or similar; offline. Optional extra
  (`pip install aindy-runtime[embeddings-local]`) so the base wheel stays lean.
- (Optional) `HttpEmbeddingProvider` — self-hosted embedding server (TEI, Ollama, vLLM).
- Dispatch via `get_embedding_provider(provider: str = ...)` selected by a new
  `AINDY_EMBEDDING_PROVIDER` setting. Keep `generate_embedding` / `generate_query_embedding` as
  the public funnel — they delegate to the resolved provider — so **no call site changes**.
- Consider wrapping providers in `CircuitBreakerLLMClient`-equivalent breaker semantics (the
  embedding path already uses the circuit breaker via `external_call_service`).

### 3.2 The dimensionality / migration story (the real work — decide at build time)

The blocker in §2.2. Options, cheapest → most flexible:

| Option | Mechanism | Trade-off |
|---|---|---|
| **A. Provider-pinned deployment** | `AINDY_EMBEDDING_PROVIDER` chosen once; if it differs from stored vectors, operator runs a **re-embed migration** over `memory_nodes` (batch job over existing rows via the same funnel). Column stays a single dimension per deployment. | Simplest. Requires the column to match the provider's dim → still a single `Vector(N)` per DB. Cross-provider within one DB not supported. |
| **B. Dimension-parameterized column** | Make the `Vector(N)` dimension configurable at schema-bootstrap; validate provider.dimensions == column dim at startup (fail-closed). | One schema knob; still one provider per DB, but no code edit to change dim. Schema-contract bump. |
| **C. Per-namespace / dimension-tagged vectors** | Store `embedding_model` + `embedding_dim` alongside each vector; filter similarity queries to same-model rows; possibly multiple vector columns. | Most flexible (multi-provider coexistence, gradual migration) but the largest schema + query-path change. |

**Recommendation:** ship **Option A** first (pinned provider + a re-embed CLI/async job). It
delivers the air-gapped capability with the least schema churn and a clear operator runbook.
Escalate to B/C only if a concrete multi-provider-in-one-DB requirement appears. Whichever is
chosen, the `1536` literals in §2.2 items 1 & 3 must be de-hardcoded (driven from the active
provider's `dimensions`), which is a schema-contract change (`memory_persistence.py` →
`SCHEMA_CONTRACT_VERSION` bump + baseline regen + the two test assertions per the schema protocol).

### 3.3 Config + tests + exit criteria

- Add `AINDY_EMBEDDING_PROVIDER: str = "openai"` to `Settings` + `AINDY/.env.example` (new
  Embedding group). Local-model settings (model name, device) namespaced `AINDY_EMBEDDING_*`.
- Recorded-cassette contract test per provider (mirror `test_contract_llm_openai.py` /
  AGENT-HARDEN-7 respx pattern); a real local-model smoke test guarded by the optional extra.
- **Exit criteria:** (1) `generate_embedding`/`_query` dispatch through `EmbeddingProvider`;
  (2) OpenAI remains the default with zero behavior change on existing deployments; (3) a local
  provider produces vectors and a documented re-embed path migrates an existing DB; (4) dimension
  literals de-hardcoded and validated fail-closed at startup; (5) `MEMORY-EMBEDDING-PROVIDER-1`
  closed, ECOGAP-3 Phase 1 closed.

---

## 4. Phase 2 — LLM hosted-provider breadth  ·  SECOND

**Goal:** chat/planning calls can target Azure OpenAI / Anthropic / Gemini / Bedrock / local in
addition to OpenAI + DeepSeek, using the abstraction that already exists.

### 4.1 Work (the seam is done — this is population)

- Implement concrete `LLMClient` clients per provider behind the existing Protocol; register them
  in `get_llm_client(provider)`'s dispatch.
- They compose for free with `FallbackLLMClient` / `resolve_provider_chain()` (open-primary-breaker
  failover already works) via `LLM_PROVIDER` + `LLM_FALLBACK_PROVIDERS`.
- Add per-provider timeout/retry settings symmetric with the existing `OPENAI_*` set (the
  `DEEPSEEK_API_KEY`-without-controls asymmetry noted in `MEMORY-EMBEDDING-PROVIDER-1` gets fixed
  here).
- Recorded-cassette contract test per provider (respx). Adopt at the call sites
  (planning / planner_backends) that AGENT-HARDEN-5 left as a deferred opt-in.
- **Decision at build time:** wrap `litellm` as a single meta-provider vs. hand-write each client.
  litellm buys breadth cheaply (Aider/SWE/ADK reach) at the cost of a heavy dependency and less
  control over the circuit-breaker/attestation seam. Default recommendation: hand-write the 2–3
  providers actually requested; reach for litellm only if breadth-for-its-own-sake is the goal.

### 4.2 Exit criteria

At least one net-new hosted provider selectable via `LLM_PROVIDER`, cassette-tested, composing
with the fallback chain; per-provider timeout/retry controls present; ECOGAP-3 Phase 2 closed.

---

## 5. Effort & risk

| Phase / piece | Effort | Primary risk |
|---|---|---|
| P1 · `EmbeddingProvider` seam + OpenAI default | S | Low — funnel already exists |
| P1 · local provider (sentence-transformers) | M | Optional-extra packaging; model download in air-gapped envs |
| P1 · dimensionality de-hardcode + re-embed migration | **M–L** | **Highest** — schema-contract bump + existing-data migration + read-path cast |
| P2 · per-provider LLM clients | S–M | Low — seam done; each provider's auth/wire quirks |
| P2 · litellm meta-provider (if chosen) | M | Heavy dep; weakens breaker/attestation control |

## 6. Triggers (either phase can start independently once scheduled)

- **Phase 1:** first operator request for a non-OpenAI embedding backend, or formal support of an
  offline / air-gapped / regulated deployment profile.
- **Phase 2:** first request for a specific non-OpenAI/DeepSeek chat provider.

## 7. Cross-references

- Embedding funnel + hardwiring: `AINDY/memory/embedding_service.py`
  (`generate_embedding` `:56`, `generate_query_embedding` `:140`, constants `:26-27`).
- Dimensionality sites: `memory/memory_persistence.py:41`, `embedding_service.py:26-27`,
  `db/dao/memory_node_dao.py:435`.
- Embedding write/read call sites: `memory/embedding_jobs.py:103`,
  `db/dao/memory_node_dao.py` (create/update/query), `memory/memory_capture_engine.py:209`,
  `runtime/flow_definitions_memory.py:191`, `routes/memory_router.py:429`.
- LLM seam (Phase 2): `AINDY/platform_layer/llm_client.py`
  (`LLMClient` `:18`, `CircuitBreakerLLMClient` `:36`, `FallbackLLMClient` `:97`,
  `get_llm_client` `:168`, `resolve_provider_chain` `:182`, `get_llm_client_chain` `:209`).
- Existing concrete clients: `openai_client.py`, `deepseek_client.py`.
- Cassette/contract test pattern (AGENT-HARDEN-7): `tests/unit/test_contract_llm_openai.py`,
  `tests/unit/test_llm_provider_fallback.py`.
- Schema-contract protocol (for the P1 dimension change): `CLAUDE.md` §"Schema contract version
  protocol"; `AINDY/db/schema_contract.py`.
- Tracking: `TECH_DEBT.md` §ECOGAP-3, §MEMORY-EMBEDDING-PROVIDER-1;
  `docs/runtime/ECOSYSTEM_CAPABILITY_GAPS.md` G3.
