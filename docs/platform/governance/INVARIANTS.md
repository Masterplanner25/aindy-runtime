---
title: "Invariants (runtime-owned)"
last_verified: "2026-08-05"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Invariants

The **runtime-owned half** of the system invariants, authored for `aindy-runtime`
during the Bucket A migration (DOCS-BUCKET-A-1). These are invariants enforced by
**runtime/platform code** (`AINDY/...`): database configuration, the memory graph,
session/auth mechanisms, and startup safety. Section numbers in parentheses trace
back to the original combined monolith `INVARIANTS.md`.

Each invariant lists its enforcement location and mechanism. Enforcement locations
were re-verified against the current tree on 2026-06-27 — several differ from the
pre-split monolith (noted inline) because modules moved and the runtime now
bootstraps its schema from ORM metadata (`create_all`) rather than the monolith's
migration set.

**App-domain invariants live in `aindy-apps-monolith`**, not here — Masterplan/
Genesis activation and locking, canonical-metrics uniqueness, RippleTrace
DropPoint seeding, freelance/masterplan non-null columns, and the app route
surfaces that *consume* the runtime auth/rate-limit mechanisms below. See
`aindy-apps-monolith` → `docs/platform/governance/INVARIANTS.md` for that half.

Related runtime docs: [`../../runtime/EXECUTION_INVARIANTS.md`](../../runtime/EXECUTION_INVARIANTS.md)
covers execution/flow-engine invariants (WAIT/RESUME, idempotency, scheduler
semantics); this document covers data, storage, auth, and startup invariants.

---

## Database configuration

### (1) PostgreSQL requirement for `DATABASE_URL`
- `DATABASE_URL` must start with `postgres`.
- Enforcement: `AINDY/config.py: Settings.ensure_postgres` — Pydantic field validator
  raises `ValueError("DATABASE_URL must be a valid PostgreSQL URI")` if it does not.
- Violation: application fails to start (configuration validation error).
- Type: Application-enforced.

### (2) UTC timezone on DB connections
- Every SQLAlchemy engine connection sets its session timezone to UTC.
- Enforcement: `AINDY/db/database.py: set_utc` (`event.listens_for(engine, "connect")`)
  executes `SET TIME ZONE 'UTC';` on connect. Exceptions are swallowed (best-effort).
- Violation: timestamps could be stored in a non-UTC zone; time-based logic may drift.
- Type: Application-enforced; effective at DB level only when the `SET` succeeds.

### (2.1) Background lease timestamps use aware UTC
- The leader-election lease path normalizes timestamps to timezone-aware UTC before
  persistence and before any `expires_at` comparison.
- Enforcement: `AINDY/platform_layer/leadership.py: _as_utc()` (coerces naive values via
  `replace(tzinfo=timezone.utc)`), used by `try_acquire_lease()` and the refresh/release
  helpers on `background_task_leases`. _(Moved from the monolith's
  `services/task_services.py` when leadership election became runtime-owned — see
  TECH_DEBT.md `LEASE-1`.)_
- Violation: naive-vs-aware datetime comparison errors block scheduler leadership.
- Type: Application-enforced.

### (17) Per-request DB session isolation via `get_db`
- FastAPI routes using `Depends(get_db)` receive a fresh session that is closed after
  the request.
- Enforcement: `AINDY/db/database.py: get_db` — generator yields the session and closes
  it in a `finally` block.
- Violation: session leakage and cross-request contamination.
- Type: Application-enforced.

## Durable ledger

### (2.2) Required `SystemEvent` emission fails closed
- Critical execution and external-interaction paths must persist their required
  `SystemEvent` rows or fail the calling action.
- Enforcement: `AINDY/core/system_event_service.py: emit_system_event(..., required=True)`
  raises `SystemEventEmissionError` on persistence failure (after attempting a fallback
  `error.system_event_failure` record).
- Violation: critical state transitions complete with no durable ledger entry; execution
  becomes non-auditable.
- Type: Application-enforced.

## Memory graph

All memory-graph constraints live in `AINDY/memory/memory_persistence.py` (the ORM models
`MemoryNodeModel` / `MemoryLinkModel`), which is the schema source of truth — the runtime
bootstraps tables from this metadata via `create_all`.

### (4) Memory link uniqueness
- `memory_links` is unique across `(source_node_id, target_node_id, link_type)`.
- Enforcement: `AINDY/memory/memory_persistence.py` — `Index("uq_memory_links_unique", …, unique=True)`.
- Violation: duplicate links; graph ambiguity.
- Type: DB-enforced (unique index).

### (5) Memory links cannot self-reference
- `source_id` and `target_id` must differ.
- Enforcement: `AINDY/memory/memory_persistence.py: MemoryNodeDAO.create_link` raises
  `ValueError` if `source_id == target_id` (same guard in `AINDY/db/dao/memory_node_dao.py: create_link`).
- Violation: self-referential links.
- Type: Application-enforced.

### (6) Memory links require existing nodes
- Both `source_id` and `target_id` must exist in `memory_nodes`.
- Enforcement: `AINDY/memory/memory_persistence.py: MemoryNodeDAO.create_link` (and the DAO
  variant) check node existence and raise `ValueError` if either is missing.
- Violation: links pointing at nonexistent nodes.
- Type: Application-enforced.

### (7) Memory link foreign keys
- `memory_links.source_node_id` and `memory_links.target_node_id` are foreign keys to
  `memory_nodes.id` (`ON DELETE CASCADE`).
- Enforcement: `AINDY/memory/memory_persistence.py: MemoryLinkModel` ORM `ForeignKey(...)`
  columns (realized at table creation).
- Violation: links could reference missing nodes; orphaned links on node deletion.
- Type: DB-enforced (foreign key constraints).

### (11c) Memory non-null columns
- `memory_nodes.content` and `memory_nodes.node_type`, and `memory_links.source_node_id`,
  `target_node_id`, `link_type`, `strength` are `nullable=False`.
- Enforcement: `AINDY/memory/memory_persistence.py` (`nullable=False` columns).
- Violation: insert/update failures at the DB level.
- Type: DB-enforced.

### (12) Memory node/link UUID defaults
- `memory_nodes.id` and `memory_links.id` default to a generated UUID.
- Enforcement: `AINDY/memory/memory_persistence.py` — ORM `default=uuid.uuid4`.
  _(In the split runtime this is a Python-side ORM default, **not** the monolith's DB-side
  `gen_random_uuid()`.)_
- Violation: inserts without explicit IDs could fail or produce null IDs.
- Type: Application-enforced (ORM default).

### (13) Memory node `updated_at` maintenance
- `memory_nodes.updated_at` is set on insert and refreshed on every update.
- Enforcement: `AINDY/memory/memory_persistence.py` — `Column(..., server_default=func.now(),
  onupdate=func.now())`. _(In the split runtime this is an ORM `onupdate`, **not** the
  monolith's PL/pgSQL `trg_update_memory_nodes_updated_at` trigger; it is therefore enforced
  on ORM-mediated updates.)_
- Violation: `updated_at` would not reflect row updates.
- Type: Application-enforced (ORM `onupdate`).

### (14) Memory node tag indexing
- `memory_nodes.tags` (JSONB) is indexed for containment/tag queries.
- Enforcement: `AINDY/memory/memory_persistence.py` — `Index("ix_memory_nodes_tags_gin", tags,
  postgresql_using="gin")`. _(Replaces the monolith's `content` tsvector full-text index,
  which is **not** present in the split runtime; tag filtering uses this GIN index, and
  semantic search uses the pgvector `embedding` column.)_
- Violation: tag-filtered retrieval degrades to sequential scans.
- Type: DB-enforced when the index is created (PostgreSQL only).

### (27) Memory `node_type` enforcement
- A non-null `memory_nodes.node_type` must be one of
  `{"decision", "outcome", "insight", "relationship"}` (and `memory_type` one of
  `{"decision", "outcome", "failure", "insight"}`).
- Enforcement: `AINDY/memory/memory_persistence.py: validate_node_type`
  (`@event.listens_for(MemoryNodeModel, "before_insert"/"before_update")`) raises `ValueError`
  at the ORM layer; `AINDY/routes/memory_router.py` additionally enforces it via a Pydantic
  `Literal[...]` on `CreateNodeRequest`, returning HTTP 422 before the DAO is reached.
- Violation: unconstrained `node_type` values break type-filtered recall and search.
  _(See TECH_DEBT.md `MEM-NODETYPE-1`, closed 2026-06-27, for the default-value regression
  this guard surfaced.)_
- Type: Application-enforced (ORM event) + API-enforced (Pydantic). No DB CHECK constraint.

### (28) Asynchronous embedding write safety
- `MemoryNodeDAO.save()` persists a node immediately with `embedding_status="pending"` and
  enqueues embedding generation asynchronously; the request never blocks on the embedding
  call, and a later embedding failure leaves the node saved.
- Enforcement: `AINDY/db/dao/memory_node_dao.py: save`, `AINDY/memory/embedding_jobs.py:
  enqueue_embedding`, and the background ingest worker; retrieval falls back to non-vector
  paths while embeddings are unavailable.
- Violation: memory writes would reintroduce request-path latency and fail on embedding errors.
- Type: Application-enforced.

## Auth & rate-limiting mechanisms

The **mechanisms** below are runtime-owned. Runtime route groups (e.g. `memory_router`,
agent, and `/platform/*` surfaces) opt in directly; app route groups in
`aindy-apps-monolith` opt in via the same dependencies — those app surfaces are cataloged
in the app-owned invariants doc, not here.

### (21) JWT authentication mechanism
- Protected route groups require a valid JWT Bearer token; missing/invalid/expired tokens
  are rejected with HTTP 401 before the route body runs.
- Enforcement: `AINDY/services/auth_service.py: get_current_user` — `HTTPBearer` extracts the
  token, `decode_access_token()` verifies the HS256 signature and expiry against `SECRET_KEY`,
  raising `HTTPException(401)` on failure. `get_current_user` also accepts an
  `X-Platform-Key` header as an alternative to a Bearer JWT. Injected via router-level
  `dependencies=[Depends(get_current_user)]`.
- **A valid signature is not sufficient (2.0.0).** `decode_access_token` additionally requires
  `purpose == "access"`; a correctly-signed token minted for another purpose — password
  reset, email verification — is rejected. It returns the *same generic 401* as a bad
  signature, deliberately, so the failure does not tell an attacker which condition failed.
  This is defence in depth: the primary control is that each token class is signed with a
  domain-separated key.
- Public exceptions — **the auth surface grew from 2 routes to 7 in 2.0.0**; a locked-out
  user must be able to reach the recovery routes without a token:

  | Route | Auth |
  |---|---|
  | `POST /auth/register`, `/login` | public |
  | `POST /auth/password/forgot`, `/password/reset`, `/verify-email` | public — by necessity |
  | `POST /auth/logout`, `/password/change` | Bearer JWT (`get_current_user`) |
  | `POST /auth/admin/invalidate-sessions/{user_id}` | admin (`require_admin_principal`) |

  Health routes remain public.
- Violation: unauthenticated access to protected endpoints.
- Type: Application-enforced.

### (22) API-key authentication mechanism
- Service-to-service routes require a valid `X-API-Key` header matching `AINDY_API_KEY`.
- Enforcement: `AINDY/services/auth_service.py: verify_api_key` compares the header to
  `settings.AINDY_API_KEY` and raises `HTTPException(401)` on mismatch. Runtime surface:
  `/db/verify` (`AINDY/routes/db_verify_router.py`). (App surfaces such as
  `/network_bridge/*` consume the same mechanism — cataloged in the app doc.)
- Violation: credential-free access to the schema-inspection / handshake endpoints.
- Type: Application-enforced.

### (23) Rate-limiter primitive
- The shared SlowAPI `Limiter` used to rate-limit AI/expensive endpoints is runtime-owned.
- Enforcement: `AINDY/platform_layer/rate_limiter.py: limiter` (`Limiter(...)`); endpoints
  apply `@limiter.limit(...)` and SlowAPI returns HTTP 429 with `Retry-After` when exceeded.
  The specific AI endpoints and their per-IP limits are app-owned (see the app doc).
- Violation: unconstrained callers could exhaust provider quotas and incur unbounded cost.
- Type: Application-enforced.

## Startup safety

### (29) Startup schema guard
- The runtime refuses to start when its schema does not match the schema contract.
- Enforcement: `AINDY/startup.py: _enforce_schema_guard` calls `ensure_runtime_schema()` and
  raises `RuntimeError` on drift (Phase 5 of startup). _(Replaces the monolith's
  `AINDY/main.py` `alembic current == alembic heads` check; the runtime uses the schema
  contract — see `AINDY/db/schema_contract.py` and CLAUDE.md "Schema contract version
  protocol".)_
- Violation: the app could run against a stale or divergent schema.
- Type: Application-enforced (startup).

## Documented but not enforced at code level (20)
- Session isolation **beyond** routes (e.g. across background threads) is a usage pattern,
  not a mechanism — `get_db` only governs request-scoped sessions.
- ORM-defined constraints are realized only once `create_all` / migrations have run; they
  are not enforced before schema bootstrap.

## Cross-boundary note

### (15) Author system identity seeding — now app-owned
- The monolith seeded an `author-system` identity at startup
  (`AINDY/main.py: ensure_system_identity`). That hook is **not present in the split
  runtime** — the `authors` table and its seeding are app-owned (`apps/authorship`,
  aindy-apps-monolith). Listed here only to record that the runtime no longer owns this
  invariant.

---

## Appendix: DB inspection commands (runtime tables)

Run against the PostgreSQL database to verify runtime-owned constraints and indexes. Adjust
the schema name if not `public`.

### Memory link unique index
```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'memory_links' AND indexname = 'uq_memory_links_unique';
```

### Memory link foreign keys
```sql
SELECT conname, conrelid::regclass AS table_name, confrelid::regclass AS ref_table,
       pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'memory_links'::regclass AND contype = 'f';
```

### Memory node tag GIN index
```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'memory_nodes' AND indexname = 'ix_memory_nodes_tags_gin';
```

### Memory node not-null columns (sample)
```sql
SELECT column_name, is_nullable, data_type
FROM information_schema.columns
WHERE table_name = 'memory_nodes' AND column_name IN ('content', 'node_type', 'tags');
```
