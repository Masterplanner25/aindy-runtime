---
title: "Data Model Map"
last_verified: "2026-08-05"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Data Model Map

> **Scope (runtime/app split).** This document maps the **runtime-owned** data
> model as implemented in `aindy-runtime`. It was relocated from the pre-split
> monolith archive (Bucket A, Tier-2 surgery — `TECH_DEBT.md` `DOCS-BUCKET-A-1`).
> App-domain tables (freelance, masterplan, tasks, social, authorship, leadgen,
> research, ARM, rippletrace, analytics/metrics, network-bridge) were collapsed
> to a single pointer at the end of §1 — they are **app-owned** and now live in
> the **aindy-apps-monolith** repo. The runtime-vs-app ownership boundary is
> normative in [`../runtime/DB_OWNERSHIP_CONTRACT.md`](../runtime/DB_OWNERSHIP_CONTRACT.md);
> the runtime-owned model set is the source of truth in `AINDY/db/models/`
> (plus the Memory Bridge models in `AINDY/memory/memory_persistence.py`).

This document maps the current data model strictly as implemented in the
repository. If a property cannot be confirmed, it is marked as not explicitly
defined in the current implementation.

## 1. PostgreSQL / SQLAlchemy Models (runtime-owned)

Detailed entries below cover the runtime-owned tables carried over from the
original combined map and re-verified against current source. They are **not**
the complete runtime model set — see the **Coverage** note at the end of this
section for the runtime tables that are owned but not individually detailed
here.

### `AINDY/db/models/agent.py`

#### Agent (`agents`)
- Columns
- `id`: String, primary key, nullable: not explicitly set (primary key implies non-null), default: not defined
- `name`: String, nullable=False, unique=True
- `agent_type`: String, nullable=False
- `description`: Text, nullable=True
- `owner_user_id`: UUID, ForeignKey("users.id"), nullable=True, index=True
- `is_active`: Boolean, nullable=False, default=True
- `memory_namespace`: String, nullable=False, unique=True
- `created_at`: DateTime(timezone=True), nullable: not explicitly set, server_default=func.now()
- Primary key: `id`
- Unique constraints: `name`, `memory_namespace`
- Indexes: `owner_user_id` (index=True, `ix_agents_owner_user_id`)
- Foreign keys: `owner_user_id -> users.id`
- Relationships: None
- Purpose: One row per agent in the ecosystem. `memory_namespace` is a stable
  identifier that tags all memory nodes the agent creates. System agents are
  registered by namespace; custom agents are user-defined.

### `AINDY/db/models/background_task_lease.py`

#### BackgroundTaskLease (`background_task_leases`)
- Columns
- `id`: UUID, primary key, default=uuid.uuid4
- `name`: String, nullable=False, unique=True, index=True
- `owner_id`: String, nullable=False, index=True
- `acquired_at`: DateTime(timezone=True), nullable=False, server_default=func.now()
- `heartbeat_at`: DateTime(timezone=True), nullable=False, server_default=func.now()
- `expires_at`: DateTime(timezone=True), nullable=False
- Primary key: `id`
- Unique constraints: `name`
- Indexes: `name` (index=True, `ix_background_task_leases_name`), `owner_id` (index=True, `ix_background_task_leases_owner_id`)
- Foreign keys: None
- Relationships: None
- Purpose: Atomic single-leader election for background scheduler jobs
  (`LEASE-1`). Exactly one holder per `name`; renewed via `heartbeat_at` with
  failover on `expires_at`.

### `AINDY/db/models/memory_metrics.py`

#### MemoryMetric (`memory_metrics`)
- Columns
- `id`: Integer, primary key, index=True, nullable: not explicitly set (primary key implies non-null)
- `user_id`: UUID, ForeignKey("users.id"), nullable=False, index=True
- `task_type`: String, nullable=True, index=True
- `impact_score`: Float, nullable=False, default=0.0
- `memory_count`: Integer, nullable=False, default=0
- `avg_similarity`: Float, nullable=False, default=0.0
- `created_at`: DateTime, nullable=False, default=datetime.utcnow
- Primary key: `id`
- Unique constraints: Not explicitly defined in current implementation.
- Indexes: `id` (index=True), `user_id` (index=True, `ix_memory_metrics_user_id`), `task_type` (index=True, `ix_memory_metrics_task_type`)
- Foreign keys: `user_id -> users.id`
- Relationships: None

### `AINDY/db/models/memory_trace.py`

#### MemoryTrace (`memory_traces`)
- Columns
- `id`: UUID, primary key, default=uuid.uuid4
- `user_id`: UUID, ForeignKey("users.id"), nullable=False, index=True
- `title`: String, nullable=True
- `description`: Text, nullable=True
- `source`: String, nullable=True
- `extra`: JSONB, nullable=True
- `created_at`: DateTime, nullable=False, default=datetime.utcnow
- `updated_at`: DateTime, nullable=False, default=datetime.utcnow (onupdate=datetime.utcnow)
- Primary key: `id`
- Unique constraints: Not explicitly defined in current implementation.
- Indexes: `user_id` (index=True, `ix_memory_traces_user_id`)
- Foreign keys: `user_id -> users.id`
- Relationships: None

### `AINDY/db/models/memory_trace_node.py`

#### MemoryTraceNode (`memory_trace_nodes`)
- Columns
- `id`: UUID, primary key, default=uuid.uuid4
- `trace_id`: UUID, ForeignKey("memory_traces.id", ondelete="CASCADE"), nullable=False, index=True
- `node_id`: UUID, ForeignKey("memory_nodes.id", ondelete="CASCADE"), nullable=False, index=True
- `position`: Integer, nullable=False
- `created_at`: DateTime, nullable=False, default=datetime.utcnow
- Primary key: `id`
- Unique constraints: `uq_trace_position` on (`trace_id`, `position`)
- Indexes: `trace_id` (index=True, `ix_memory_trace_nodes_trace_id`), `node_id` (index=True, `ix_memory_trace_nodes_node_id`)
- Foreign keys: `trace_id -> memory_traces.id` (ondelete="CASCADE"), `node_id -> memory_nodes.id` (ondelete="CASCADE")
- Relationships: None

### `AINDY/db/models/request_metric.py`

#### RequestMetric (`request_metrics`)
- Columns
- `id`: Integer, primary key, index=True, nullable: not explicitly set (primary key implies non-null)
- `request_id`: String, nullable=True, index=True
- `trace_id`: String, nullable=True, index=True
- `user_id`: UUID, ForeignKey("users.id"), nullable=True, index=True
- `method`: String, nullable=False
- `path`: String, nullable=False, index=True
- `status_code`: Integer, nullable=False
- `duration_ms`: Float, nullable=False
- `created_at`: DateTime, nullable=False, default=datetime.utcnow, index=True
- Primary key: `id`
- Unique constraints: Not explicitly defined in current implementation.
- Indexes: `id` (index=True), `request_id` (index=True, `ix_request_metrics_request_id`), `trace_id` (index=True, `ix_request_metrics_trace_id`), `user_id` (index=True, `ix_request_metrics_user_id`), `path` (index=True, `ix_request_metrics_path`), `created_at` (index=True, `ix_request_metrics_created_at`)
- Foreign keys: `user_id -> users.id`
- Relationships: None

### `AINDY/db/models/system_health_log.py`

#### SystemHealthLog (`system_health_logs`)
- Columns
- `id`: Integer, primary key, index=True, nullable: not explicitly set (primary key implies non-null)
- `timestamp`: DateTime, nullable: not explicitly set, default=datetime.utcnow
- `status`: String(50), nullable: not explicitly set
- `components`: JSON, nullable: not explicitly set
- `api_endpoints`: JSON, nullable: not explicitly set
- `avg_latency_ms`: Float, nullable: not explicitly set
- Primary key: `id`
- Unique constraints: Not explicitly defined in current implementation.
- Indexes: `id` (index=True)
- Foreign keys: None
- Relationships: None

### `AINDY/db/models/user.py`

#### User (`users`)
- Columns
- `id`: UUID (postgresql UUID), primary key, default=uuid.uuid4, nullable: not explicitly set (primary key implies non-null)
- `email`: String, unique=True, index=True, nullable=False
- `username`: String, unique=True, index=True, nullable=True
- `hashed_password`: String, nullable=False
- `is_active`: Boolean, nullable=False, default=True
- `is_admin`: Boolean, nullable=False, default=False
- `token_version`: Integer, nullable=False, default=0, server_default="0"
- `is_verified`: Boolean, nullable=False, default=False, server_default="false", `info={"reconcile_backfill": "true"}`
- `verified_at`: DateTime(timezone=True), nullable=True, `info={"reconcile_backfill": "COALESCE(created_at, now())"}`
- `created_at`: DateTime(timezone=True), nullable: not explicitly set, server_default=func.now()
- Primary key: `id`
- Unique constraints: `email` (unique=True), `username` (unique=True)
- Indexes: `email` (index=True), `username` (index=True)
- Foreign keys: None
- Relationships:
- `api_keys = relationship("PlatformAPIKey", back_populates="user", cascade="all, delete-orphan")`
- Cascade rules: `all, delete-orphan` on `User.api_keys`
- Purpose: Stores authenticated platform users. Created by `register_user()` in
  `AINDY/services/auth_service.py`. Password is stored as a bcrypt hash;
  plaintext is never persisted. `is_admin` is grant-only (see admin-bootstrap
  constraint); `token_version` backs JWT invalidation — and is also what makes a
  password-reset token single-use, since consuming one bumps the version.
- **`reconcile_backfill` on the verification columns (FR-8).** `server_default` governs
  rows written *afterwards*; an account created before verification existed was never
  given a chance to confirm. Alembic `0014` grandfathers those rows, but the `alembic/`
  tree is not shipped in the wheel, so a wheel install would otherwise leave every
  pre-existing account unverified — a latent lockout the moment
  `AINDY_REQUIRE_VERIFIED_LOGIN` is enabled. The `info` declaration makes
  `bootstrap-schema --reconcile` apply the same backfill on every install shape.
  See `schema_contract._render_backfill_sql`.

### `AINDY/db/models/user_identity.py`

#### UserIdentity (`user_identity`)
- Table args
- `UniqueConstraint` on (`user_id`) named `uq_user_identity_user`
- Columns
- `id`: String, primary key, default: `str(uuid.uuid4())`, nullable: not explicitly set (primary key implies non-null)
- `user_id`: UUID, ForeignKey("users.id"), nullable=False, unique=True, index=True
- `tone`: String, nullable=True
- `communication_notes`: Text, nullable=True
- `preferred_languages`: JSON, nullable: not explicitly set, default=list
- `preferred_tools`: JSON, nullable: not explicitly set, default=list
- `avoided_tools`: JSON, nullable: not explicitly set, default=list
- `risk_tolerance`: String, nullable=True
- `speed_vs_quality`: String, nullable=True
- `decision_notes`: Text, nullable=True
- `learning_style`: String, nullable=True
- `detail_preference`: String, nullable=True
- `learning_notes`: Text, nullable=True
- `observation_count`: Integer, nullable: not explicitly set, default=0
- `last_updated`: DateTime(timezone=True), nullable=True
- `evolution_log`: JSON, nullable: not explicitly set, default=list
- `created_at`: DateTime(timezone=True), nullable: not explicitly set, server_default=func.now()
- Primary key: `id`
- Unique constraints: `uq_user_identity_user` (user_id unique)
- Indexes: `user_id` (index=True)
- Foreign keys: `user_id -> users.id`
- Relationships: None
- Purpose: Stores per-user identity preferences and evolution history inferred by
  `IdentityService`. Value sets are constrained in application code
  (`VALID_TONES`, `VALID_RISK_TOLERANCE`, `VALID_SPEED_VS_QUALITY`,
  `VALID_LEARNING_STYLES`, `VALID_DETAIL_PREFERENCES`).

### Coverage — runtime-owned tables not individually detailed here

The runtime owns additional ORM models under `AINDY/db/models/` that predate or
postdate the original combined map and are **not** expanded above. They are
canonical in source and cataloged by category in
[`../runtime/DB_OWNERSHIP_CONTRACT.md`](../runtime/DB_OWNERSHIP_CONTRACT.md)
(§"Runtime-Owned Models"):

- **Platform access:** `api_key` (`platform_api_keys`).
- **Agent runtime persistence:** `agent_registry`, `agent_run`, `agent_event`,
  `agent_step` (`agent_steps`), `agent_trust_settings`,
  `agent_capability_mapping` (`agent_capability_mappings`).
- **Execution, waits, scheduler:** `execution_unit`, `flow_run`,
  `waiting_flow_run`, `job_log`, `event_edge`, `effect_record` (idempotency
  gate), `effect_reversal` (`effect_reversals` — append-only compensation ledger
  behind `sys.v1.agent.undo`, AGENT-HARDEN-3), `flow_history` (append-only
  per-run event fold backing durable continuation), `nodus_scheduled_job`,
  `nodus_workflow` (`nodus_workflows` — registered `.nd` workflow source, RTR-1).
- **Observability / system state:** `system_event`, `system_state_snapshot`,
  `event_outcome` (`event_outcomes`).
- **Dynamic platform state:** `capability`, `dynamic_flow`, `dynamic_node`,
  `webhook_subscription`.

As of 2026-08-05 the runtime owns **36 tables** in `Base.metadata`. This list is
maintained by hand; to re-derive it, enumerate `Base.metadata.tables` after importing
`AINDY.db.model_registry` and `AINDY.memory.memory_persistence`.

The Memory Bridge models (`memory_nodes`, `memory_links`) are detailed in §5.

### App-owned models — see `aindy-apps-monolith`

The pre-split combined map interleaved app-domain tables with runtime tables.
Those tables are **app-owned** and now live in the **aindy-apps-monolith** repo
under the owning app packages. They are intentionally **not** documented here.
Summary of the collapsed domains (canonical list:
[`../runtime/DB_OWNERSHIP_CONTRACT.md`](../runtime/DB_OWNERSHIP_CONTRACT.md)
§"App-Owned Models"):

| Domain (app package) | Representative tables (pre-split names) |
|---|---|
| `apps/tasks` | `tasks` and task-adjacent planning tables |
| `apps/analytics` | `calculation_results`, `canonical_metrics`, and the metrics-model tables (`engagements`, `ai_efficiencies`, `impacts`, `efficiencies`, `revenue_scalings`, `execution_speeds`, `attention_values`, `engagement_rates`, `business_growths`, `monetization_efficiencies`, `ai_productivity_boosts`, `lost_potentials`, `decision_efficiencies`) |
| `apps/masterplan` | `master_plans`, `genesis_sessions` |
| `apps/arm` | `arm_runs`, `arm_logs`, `arm_configs` |
| `apps/search` | `leadgen_results`, `research_results` |
| `apps/freelance` | `freelance_orders`, `client_feedback`, `revenue_metrics` |
| `apps/rippletrace` | `drop_points`, `pings` |
| `apps/authorship` | `authors` |
| `apps/network_bridge` | `bridge_user_events` |
| `apps/social` | Mongo-backed; Pydantic models only (`SocialProfile`, `SocialPost`, …) — no SQLAlchemy table (see §4) |

> The combined deployment still loads these into the **same** `Base.metadata`
> via `apps.bootstrap` (one shared metadata object — see
> `DB_OWNERSHIP_CONTRACT.md` §"Model Registration After The Split"); ownership,
> not a separate SQLAlchemy base, is the boundary.

## 2. Relationship Mapping (runtime-owned)

Only runtime-owned relationships declared via SQLAlchemy `relationship()` are
listed. App-domain relationships (e.g., `ARMRun.logs`, `FreelanceOrder` ↔
`ClientFeedback`, `MasterPlan` ↔ `CanonicalMetricDB`) live with their owning
apps in `aindy-apps-monolith`.

- User (`users`) 1-to-many PlatformAPIKey (`platform_api_keys`) via
  `User.api_keys` / `PlatformAPIKey.user`, cascade `all, delete-orphan`.
- MemoryTrace (`memory_traces`) 1-to-many MemoryTraceNode (`memory_trace_nodes`)
  by foreign key (`memory_trace_nodes.trace_id`, ondelete CASCADE); no ORM
  `relationship()` declared — the link is FK-only.
- No many-to-many relationships are explicitly defined among runtime-owned
  models.

## 3. Alembic Migration Alignment

The runtime owns its **own** Alembic environment, distinct from the combined
monolith tree. It tracks state in `alembic_version_runtime` (not the monolith's
`alembic_version`) and bootstraps blank databases from packaged ORM metadata via
`AINDY/db/schema_contract.py`.

**Runtime Alembic chain** (`alembic/versions/`) — head is **`0014`**:
- `0001_runtime_baseline` — runtime-owned baseline.
- `0002_idempotency_constraints` — idempotency / EffectRecord constraints.
- `0003_effect_records` — `effect_records` table (idempotency gate).
- `0004_effect_records_completed_at_index` — `completed_at` index for TTL cleanup.
- `0005_execution_units_wall_time_ms` — `execution_units.wall_time_ms` column.
- `0006_nodus_workflows` — `nodus_workflows` table (RTR-1 registered `.nd` workflows).
- `0007_agent_runs_wait_state` — agent-run wait state columns.
- `0008_effect_reversals` — `effect_reversals` compensation ledger (AGENT-HARDEN-3).
- `0009_drop_nodus_trace_events` — drops the dead `NodusTraceEvent` trace path (RTR-1).
- `0010_agent_runs_flow_run_id_index` — `ix_agent_runs_flow_run_id` (RTR-3).
- `0011_effect_records_attribution_columns` — EffectRecord attribution (MEB program).
- `0012_flow_history_sequence_number` — `flow_history` sequence number (durable continuation).
- `0013_nodus_scheduled_job_misfire_policy` — per-job misfire policy (ECOGAP-5a).
- `0014_users_email_verification` — `users.is_verified` / `verified_at`, plus the
  grandfathering backfill for accounts predating verification.

> **The head revision is also a packaged constant.** `RUNTIME_ALEMBIC_HEAD_REVISION` in
> `AINDY/db/alembic_head.py` must be bumped alongside any new revision — the `alembic/`
> scripts directory lives at the repo root and is **not** shipped in the wheel
> (`packages.find` is `AINDY*`), so `aindy-runtime bootstrap-schema` cannot read the head
> from the scripts at install time. `tests/unit/test_runtime_alembic_head.py` fails if the
> constant drifts from the actual head.

All runtime migrations are idempotent (`IF NOT EXISTS` / `IF EXISTS` guards) and
table-existence-guarded for blank-database safety (ALEMBIC-FRESH-DB-1).

> **Combined-monolith migration history is app-owned.** The pre-split migration
> list (e.g. `mb2embed0001` pgvector enablement, the Sprint 5 `user_id`
> backfills, MAS path columns, etc.) belongs to the deployment-specific Alembic
> tree owned by **aindy-apps-monolith**, which installs `aindy-runtime` as a
> dependency and represents runtime schema changes in its own migration history.
> See `DB_OWNERSHIP_CONTRACT.md` §"Migration Ownership" and
> `docs/runtime/SCHEMA_LIFECYCLE.md` for the operator workflow.

> **Migration Reminder:** SQLAlchemy models alone do not alter a live database.
> Apply migrations explicitly (`alembic upgrade head`) after any model change,
> and follow the schema-contract version protocol (CLAUDE.md) for any change
> under `AINDY/db/models/` or `AINDY/memory/memory_persistence.py`.

## 4. MongoDB Collections — app-owned

The runtime ships only the Mongo connection helper (`AINDY/db/mongo_setup.py`)
and declares **no** collections. All MongoDB document usage — the `profiles` and
`posts` collections written by the Social Layer (`social_router.py`) and updated
by task-completion logic (`task_services.py`) — is **app-owned** and lives in
**aindy-apps-monolith**. The backing Pydantic shapes (`SocialProfile`,
`SocialPost`, `Connection`, `FeedItem`) are app-owned as well; no SQLAlchemy
tables are declared for them. No schema validation is defined on the
collections.

## 5. Memory Bridge Schema (runtime-owned)

Defined in `AINDY/memory/memory_persistence.py` (relocated from
`AINDY/services/` in the split). Re-verified against current source.

### `memory_nodes`
- Model: `MemoryNodeModel`
- Columns
- `id`: UUID (postgresql UUID), primary key, default=uuid.uuid4
- `content`: Text, nullable=False
- `tags`: JSONB, nullable=False, default=list
- `node_type`: String(50), nullable=False
- `source`: String(255), nullable=True
- `source_agent`: String, nullable=True, index=True
- `is_shared`: Boolean, nullable=False, default=False
- `visibility`: String(16), nullable=False, default="private", index=True
- `user_id`: UUID, ForeignKey("users.id"), nullable=True, index=True
- `owner_run_id`: UUID, nullable=True, index=True — delegation-scoped private memory (RTR-4c).
  NULL means the node is visible on the normal paths (every pre-existing node). A non-NULL
  value scopes the node to one agent run; enforcement is gated behind
  `AINDY_DELEGATION_PRIVATE_MEMORY` (default off). No FK — the referenced run may be
  reaped independently.
- `source_event_id`: UUID, ForeignKey("system_events.id"), nullable=True, index=True
- `root_event_id`: UUID, ForeignKey("system_events.id"), nullable=True, index=True
- `causal_depth`: Integer, nullable=False, default=0
- `impact_score`: Float, nullable=False, default=0.0
- `memory_type`: String(32), nullable=False, default="insight", index=True
- `created_at`: DateTime, nullable=False, server_default=func.now()
- `updated_at`: DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
- `extra`: JSONB, nullable=False, default=dict
- `embedding`: Vector(1536), nullable=True
- `embedding_pending`: Boolean, nullable=False, default=True, index=True
- `embedding_status`: String(16), nullable=False, default="pending", index=True
- `success_count`: Integer, nullable=False, default=0
- `failure_count`: Integer, nullable=False, default=0
- `usage_count`: Integer, nullable=False, default=0
- `last_used_at`: DateTime(timezone=True), nullable=True
- `last_outcome`: String, nullable=True
- `weight`: Float, nullable=False, default=1.0
- `path`: String(512), nullable=True, index=True — full MAS path `/memory/{tenant}/{namespace}/{addr_type}/{node_id}`
- `namespace`: String(128), nullable=True, index=True — logical namespace segment
- `addr_type`: String(128), nullable=True, index=True — type segment (named `addr_type` to avoid Python `type` keyword collision)
- `parent_path`: String(512), nullable=True, index=True — parent path for tree queries
- Indexes
- `ix_memory_nodes_tags_gin` on `tags` using GIN
- column indexes on `source_agent`, `visibility`, `memory_type`,
  `embedding_pending`, `embedding_status`, `user_id`, `owner_run_id`,
  `source_event_id`, `root_event_id`, `path`, `namespace`, `addr_type`,
  `parent_path`
- Unique constraints: Not explicitly defined in current implementation.
- Foreign keys: `user_id -> users.id`, `source_event_id -> system_events.id`, `root_event_id -> system_events.id`
- Node type enforcement (ORM `before_insert` / `before_update` listener):
  - `node_type`: `VALID_NODE_TYPES = {"decision", "outcome", "insight", "relationship"}` — non-null values outside this set raise `ValueError`.
  - `memory_type`: `VALID_MEMORY_TYPES = {"decision", "outcome", "failure", "insight"}` — non-null values outside this set raise `ValueError`.
- pgvector extension required: `CREATE EXTENSION IF NOT EXISTS vector` (provisioned by the deployment's Alembic tree / `docker/init-pgvector.sql`).

### `memory_links`
- Model: `MemoryLinkModel`
- Columns
- `id`: UUID (postgresql UUID), primary key, default=uuid.uuid4
- `source_node_id`: UUID, ForeignKey("memory_nodes.id", ondelete="CASCADE"), nullable=False
- `target_node_id`: UUID, ForeignKey("memory_nodes.id", ondelete="CASCADE"), nullable=False
- `link_type`: String(50), nullable=False
- `strength`: String(20), nullable=False, default="medium"
- `weight`: Float, nullable=False, default=0.5
- `created_at`: DateTime, nullable=False, server_default=func.now()
- Indexes
- `ix_memory_links_source` on `source_node_id`
- `ix_memory_links_target` on `target_node_id`
- `uq_memory_links_unique` unique index on (`source_node_id`, `target_node_id`, `link_type`)
- Link-type constraints: Not explicitly defined in current implementation.

### `memory_metrics`
See §1 — `MemoryMetric` (`AINDY/db/models/memory_metrics.py`).

### `memory_node_history`
- Model: `MemoryNodeHistory` (`AINDY/db/models/memory_node_history.py`)
- Columns
  - `id`: String, primary key, default `str(uuid.uuid4())`
  - `node_id`: UUID, ForeignKey("memory_nodes.id", ondelete="CASCADE"), nullable=False, index=True
  - `changed_at`: DateTime(timezone=True), server_default=func.now(), nullable=False
  - `changed_by`: String, nullable=True
  - `previous_content`: Text, nullable=True
  - `previous_tags`: JSON, nullable=True
  - `previous_node_type`: String, nullable=True
  - `previous_source`: String, nullable=True
  - `change_type`: String, nullable=False
  - `change_summary`: Text, nullable=True
- Indexes
  - `ix_memory_node_history_node_changed` on (`node_id`, `changed_at`)
- Purpose: Append-only change history for MemoryNode updates (stores previous
  values only). Triggered by explicit `MemoryNodeDAO.update()`; not by initial
  creation, embedding updates, or resonance score calculations.

## 5.5 Symbolic Ingest (Operational)

- Ingest service: `AINDY/memory/memory_ingest_service.py` reads `memorytraces/`
  and `memoryevents/` files and creates `memory_traces`, `memory_trace_nodes`,
  and `memory_nodes` with provenance recorded in `extra`.

## 6. Cross-Database Boundaries

- **PostgreSQL** is used by all runtime-owned SQLAlchemy models in
  `AINDY/db/models/` and by the Memory Bridge models in
  `AINDY/memory/memory_persistence.py`. App-owned PostgreSQL tables extend the
  same shared `Base.metadata` via `apps.bootstrap` (aindy-apps-monolith).
- **MongoDB** usage is **app-owned** (Social Layer + task-completion metrics in
  aindy-apps-monolith). The runtime provides only the connection helper
  (`AINDY/db/mongo_setup.py`) and owns no collections.
- Cross-database coordination (writing MongoDB documents alongside PostgreSQL
  memory records, e.g. social content capture and task-completion metric
  snapshots) occurs in **app-owned** routers/services and is documented in
  aindy-apps-monolith.

## 7. Known Structural Risks (runtime scope)

- **Missing foreign keys:** several runtime models rely on application logic for
  referential integrity rather than DB-level FKs (e.g. `background_task_leases`,
  `system_health_logs`).
- **Lack of cascades:** runtime cascades are defined for `User.api_keys`
  (`all, delete-orphan`) and via FK `ondelete="CASCADE"` on
  `memory_trace_nodes`, `memory_links`, and `memory_node_history`. Other related
  runtime data has no cascade configuration at the ORM level.
- **Unindexed lookup fields:** beyond the explicit `index=True` columns and the
  GIN index on `memory_nodes.tags`, ad-hoc lookups may lack supporting indexes.
- **Migration ownership split:** runtime and combined-app migrations live in
  separate Alembic trees (`alembic_version_runtime` vs `alembic_version`).
  Reconciliation across them is explicit and operator-gated
  (`AINDY_SCHEMA_RECONCILE=true`); the runtime fails closed on unsafe drift. See
  `DB_OWNERSHIP_CONTRACT.md` §"Migration Ownership".
- **Implicit constraints enforced only in application logic:** e.g. Memory
  Bridge `node_type` / `memory_type` validation (ORM event listener in
  `AINDY/memory/memory_persistence.py`) and `UserIdentity` preference value sets.
