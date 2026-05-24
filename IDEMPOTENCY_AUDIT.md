# A.I.N.D.Y. Runtime — Idempotency and Invariants Audit
**Date:** 2026-05-23
**Method:** Static analysis + targeted PostgreSQL tests (27 tests, 27 passed)
**Status:** Findings only — no fixes applied in this pass

---

## Executive Summary

The A.I.N.D.Y. runtime has strong idempotency guarantees in its platform-layer
mechanisms (schema bootstrap uses `checkfirst=True`, syscall registration is
documented last-write-wins with warnings, APScheduler jobs use
`replace_existing=True` at startup, and the scheduler wait-backup path uses
`db.merge()` for upsert semantics). The critical gaps are at the database
constraint layer: several tables that should enforce uniqueness at the DB level
rely on application-level guards only, creating TOCTOU windows under concurrent
writes. The most operationally significant findings are duplicate webhook
registrations (no `UNIQUE` on `callback_url`), duplicate platform API key names
(no `UNIQUE` on `(user_id, name)`), and duplicate `execution_units` rows for
the same source record (no `UNIQUE` on `(source_type, source_id)`). None of
these are show-stoppers in single-process operation, but they become correctness
hazards under concurrent API traffic or multi-instance deployments.

---

## Audit Scope

1. **Surface 1 — Schema Bootstrap** (`ensure_runtime_schema`, `reconcile_runtime_schema`)
2. **Surface 2 — Syscall Registration** (`VersionedSyscallRegistry`, `register_syscall`)
3. **Surface 3 — Scheduler and Job Registration** (APScheduler `add_job`, `EventBus`, `SchedulerEngine`)
4. **Surface 4 — API Endpoint Idempotency** (route-level patterns, DB constraint backing)
5. **Surface 5 — Flow Engine** (`flow_runs`, `execution_units`, `waiting_flow_runs`, event routing)
6. **Surface 6 — Platform Registry and Plugin Loading** (`registry.py`, `dynamic_flows`, `dynamic_nodes`, `webhook_subscriptions`)

---

## Findings

### IDEM-1 — Syscall registry is last-write-wins with no error on conflicting re-registration
**Surface:** Surface 2 — Syscall Registration
**Severity:** Medium
**Type:** Silent overwrite with possible incorrect handler active
**Description:**
`VersionedSyscallRegistry.__setitem__` (called by `register_syscall`) logs a
WARNING when a syscall name is re-registered with a *different* handler, but
does not raise an error. The last-registered handler silently wins. In a
multi-plugin environment where two domains accidentally register the same syscall
name with different handlers, the effective handler is determined by boot order,
not by any conflict-resolution policy. There is no startup guard that detects
duplicate syscall names across plugins. The in-memory-only nature of the
registry means this cannot be audited after the fact from the DB.
**Evidence:**
`AINDY/kernel/syscall_registry.py:253-263` — `__setitem__` warns and overwrites.
`AINDY/kernel/syscall_registry.py:1280-1296` — `register_syscall` docstring says
"Idempotent — registering the same name twice overwrites the entry." Test
`test_syscall_register_same_name_different_handler` confirmed last-write-wins behavior.
**Correct behavior:**
Re-registration of the same syscall name with a *different* handler should raise
`ValueError` at startup. Same handler re-registration should remain idempotent.
**Fix complexity:** Small

---

### IDEM-2 — webhook_subscriptions has no UNIQUE constraint on callback_url
**Surface:** Surface 6 — Platform Registry and Plugin Loading
**Severity:** High
**Type:** Missing DB-level uniqueness — application-level-only enforcement
**Description:**
The `webhook_subscriptions` table has only a UUID primary key. There is no
UNIQUE constraint on `(event_type, callback_url)` or `callback_url` alone.
Any concurrent or repeated POST to `/platform/webhooks` with the same URL and
event type creates additional rows, causing the same webhook to be called
multiple times per event. Platform loader re-registers all `is_active=True`
rows on startup, so after a restart a double-registered URL will be invoked
twice for every event for as long as the rows persist. The soft-delete pattern
(`is_active=False`) does not protect against this.
**Evidence:**
`AINDY/db/models/webhook_subscription.py` — no `__table_args__` with UNIQUE.
Test `test_webhook_subscriptions_same_url_twice` confirmed 2 rows inserted
with same `callback_url` without error.
**Correct behavior:**
A UNIQUE constraint on `(event_type, callback_url)` (or at minimum an
ON CONFLICT DO NOTHING / DO UPDATE insert path in the service layer) should
prevent duplicate active subscriptions for the same URL+event pair.
**Fix complexity:** Small (migration + ON CONFLICT in service)

---

### IDEM-3 — platform_api_keys has no UNIQUE constraint on (user_id, name)
**Surface:** Surface 4 — API Endpoint Idempotency
**Severity:** Medium
**Type:** Missing DB-level uniqueness — application-level-only enforcement
**Description:**
`platform_api_keys` enforces UNIQUE only on `key_hash` (correct — prevents
credential collisions). The `name` column has no uniqueness constraint. A
user who POSTs `/platform/keys` twice with the same name (e.g. `"dev-key"`)
gets two live API keys, both active. Listing keys returns both rows under the
same name, making it ambiguous which key the user intends to revoke. The
startup `_ensure_dev_api_key()` guard in `startup.py` checks by `key_hash`
rather than by `name`, so the startup idempotency path is correct, but the
general POST path at the HTTP level has no name deduplication.
**Evidence:**
`AINDY/db/models/api_key.py` — `name` column has `nullable=False` but no `unique=True`.
DB constraint query: only `platform_api_keys_pkey` (on `id`) and the implicit
UNIQUE on `key_hash` appear.
Test `test_platform_api_keys_no_unique_on_name` confirmed 2 rows with
`name='dev-key'` for the same user.
**Correct behavior:**
Add UNIQUE on `(user_id, name)` or enforce name uniqueness per user in the
service layer with a pre-check + `ON CONFLICT`.
**Fix complexity:** Small (migration + service check)

---

### IDEM-4 — execution_units has no UNIQUE constraint on (source_type, source_id)
**Surface:** Surface 5 — Flow Engine
**Severity:** Medium
**Type:** Missing DB-level uniqueness — application-level-only enforcement
**Description:**
`execution_units` is intended to have one row per originating record
(FlowRun, AgentRun, etc.). The model defines `Index("ix_eu_source", "source_type", "source_id")`
for query performance but no UNIQUE constraint. A race condition in any code
path that calls `ExecutionUnitService.create_for_flow_run()` without a
prior-existence check can create duplicate EU rows for the same FlowRun.
Multiple EU rows for the same source cause incorrect lifecycle accounting in
`ResourceManager`, inflated `syscall_count` in observability, and ambiguous
status transitions in the scheduler.
**Evidence:**
`AINDY/db/models/execution_unit.py:160-169` — `__table_args__` defines only
regular indexes, no UNIQUE constraint.
DB constraint query: no UNIQUE constraints on `execution_units`.
Test `test_execution_units_no_unique_on_source_id` confirmed 2 rows with the
same `source_id` are accepted.
**Correct behavior:**
Add `UniqueConstraint("source_type", "source_id")` (allowing NULLs, which PG
handles correctly), or enforce at service layer with SELECT-FOR-UPDATE before
INSERT.
**Fix complexity:** Medium (migration + service refactor to use upsert or advisory lock)

---

### IDEM-5 — dynamic_flows and dynamic_nodes have no UNIQUE constraint on name
**Surface:** Surface 6 — Platform Registry and Plugin Loading
**Severity:** Medium
**Type:** Missing DB-level uniqueness — application-level-only enforcement
**Description:**
`dynamic_flows` and `dynamic_nodes` tables have only UUID primary keys. Neither
has a UNIQUE constraint on `flow_name` / `node_name`. The platform loader
(`platform_loader.py`) restores all rows from the DB on startup. If the same
flow or node name is inserted twice (e.g. by a race in POST /platform/flows),
the in-memory `FLOW_REGISTRY` / `NODE_REGISTRY` will register the duplicate
names via the loader, silently overwriting the first with the second. The
service layer has no conflict check. After restart the registry will end up
with whichever DB row is last-loaded (undefined order), making the effective
flow definition nondeterministic.
**Evidence:**
DB constraint query: `dynamic_flows` and `dynamic_nodes` have only `_pkey`
PRIMARY KEY constraints.
Tests `test_dynamic_flows_no_unique_on_flow_name` and
`test_dynamic_nodes_no_unique_on_node_name` confirmed absence of UNIQUE.
**Correct behavior:**
Add UNIQUE on `flow_name` in `dynamic_flows` and on `node_name` in
`dynamic_nodes`, with ON CONFLICT DO UPDATE (upsert) in the service layer.
**Fix complexity:** Small (migration + upsert in platform_loader insert paths)

---

### IDEM-6 — Schema contract version check has no concurrent bootstrap race guard
**Surface:** Surface 1 — Schema Bootstrap
**Severity:** Low
**Type:** TOCTOU window on blank DB
**Description:**
`ensure_runtime_schema` first calls `inspect_runtime_schema` (read), then
conditionally calls `reconcile_runtime_schema` which calls
`Base.metadata.create_all(checkfirst=True)`. SQLAlchemy's `checkfirst=True`
calls `inspector.has_table()` per table in a non-atomic loop. If two processes
simultaneously start against a completely blank database, both see no tables
and both attempt `create_all`. PostgreSQL's CREATE TABLE is internally
idempotent via `IF NOT EXISTS`, but between the initial inspection and the
DDL there is a TOCTOU window. In practice this is only triggered on first
deployment of a blank DB and has no impact on the steady-state compatible path.
`checkfirst=True` is confirmed used on all `create_all` and individual
`table.create()` calls.
**Evidence:**
`AINDY/db/schema_contract.py:528,549` — both `create_all` and `table.create`
use `checkfirst=True`.
Test `test_ensure_runtime_schema_check_first_flag` confirmed `checkfirst=True`
in source.
**Correct behavior:**
For hardened deployments: acquire a DB-level advisory lock before the first
`ensure_runtime_schema` call, or use a single-writer bootstrap pattern. For
normal deployments the current behavior is acceptable (worst case: one process
fails with a benign duplicate-table error during initial deploy).
**Fix complexity:** Medium (advisory lock in bootstrap path)

---

### IDEM-7 — Syscall registry is not persisted — domain handlers lost on process crash between bootstrap phases
**Surface:** Surface 2 — Syscall Registration
**Severity:** Low
**Type:** Non-persistent in-memory state
**Description:**
The syscall registry (`SYSCALL_REGISTRY`) is a module-level dict. Built-in
syscalls are populated at import time; domain syscalls are added by
`register_all_domain_handlers()` in startup Phase 8. If startup crashes between
Phase 7 (background services start) and Phase 8, any already-running background
task that dispatches a domain syscall will receive `Unknown syscall` errors
because the registry is partially populated. This is inherent to the in-memory
architecture and is not a regression; it is documented here as a known limitation.
**Evidence:**
`AINDY/kernel/syscall_registry.py:949` — `SYSCALL_REGISTRY = VersionedSyscallRegistry()`
at module level; no DB backing.
`AINDY/startup.py:1077-1081` — `_register_domain_handlers()` called in Phase 8.
**Correct behavior:**
Current behavior is acceptable for the startup model. A startup health check
(`_verify_required_syscalls_registered` in Phase 8) mitigates this for
production by refusing to serve traffic until all required syscalls are
registered.
**Fix complexity:** Architectural (would require syscall persistence or startup sequencing change)

---

### IDEM-8 — APScheduler stub (BackgroundScheduler) does not enforce replace_existing without ID match
**Surface:** Surface 3 — Scheduler and Job Registration
**Severity:** Low
**Type:** Stub vs production behavioral difference
**Description:**
The bundled `AINDY/apscheduler/schedulers/background.py` is a minimal stub used
in tests and fallback paths. When `replace_existing=True`, it filters existing
jobs by `id`. When `replace_existing=False`, a second `add_job` with the same
`id` appends a duplicate. In production (real APScheduler), `replace_existing=False`
with a duplicate ID raises `ConflictingIdError`. The stub silently creates
a duplicate. Since all startup `add_job` calls use `replace_existing=True`
(confirmed by test), this only surfaces in test code that creates jobs without
`replace_existing=True`.
**Evidence:**
`AINDY/apscheduler/schedulers/background.py:19-27` — stub allows duplicates
when `replace_existing=False`.
Test `test_apscheduler_add_job_same_id_replace_existing_false` confirmed 2 jobs
with same id.
Test `test_startup_uses_replace_existing_true` confirmed all startup `add_job`
calls use `replace_existing=True`.
**Correct behavior:**
The stub behavior difference is acceptable since production uses real APScheduler.
However, test-only `add_job` calls that omit `replace_existing=True` could mask
scheduling bugs. Consider enforcing `replace_existing=True` as default in the stub.
**Fix complexity:** Small

---

## Confirmed Idempotent Surfaces

| Surface | Mechanism | Test Confirmed |
|---|---|---|
| `ensure_runtime_schema` on populated DB | Returns `compatible`, no DDL executed | Yes |
| `reconcile_runtime_schema` on compatible DB | Returns as-is without issuing DDL | Yes |
| `create_all` / `table.create` | Uses `checkfirst=True` throughout | Yes (static) |
| Syscall re-registration, same handler | Silent overwrite, no error | Yes |
| APScheduler `add_job` with `replace_existing=True` | Existing job replaced, not duplicated | Yes |
| All startup `add_job` calls | All use `replace_existing=True` | Yes (static) |
| `register_router` duplicate call | Idempotent: no duplicate appended | Yes |
| `EventBus.start_subscriber()` | Second call is a no-op; thread unchanged | Yes |
| `get_scheduler_engine()` | Returns module singleton | Yes |
| `_persist_wait_backup` (waiting_flow_runs) | Uses `db.merge()` — upsert semantics | Yes (static) |
| `waiting_flow_runs` PK on `run_id` | DB UNIQUE via PK enforces one wait per run | Yes |
| `platform_api_keys` UNIQUE on `key_hash` | Duplicate credential hash rejected at DB | Yes |
| `flow_runs` PK on `id` | Duplicate run ID rejected at DB | Yes |
| `execution_units` PK on `id` | Duplicate EU ID rejected at DB | Yes |

---

## Invariants Confirmed

### DB-level (enforced by PostgreSQL constraints)
| Table | Invariant | Constraint |
|---|---|---|
| `flow_runs` | Unique run ID | PRIMARY KEY (`id`) |
| `execution_units` | Unique EU ID | PRIMARY KEY (`id`) |
| `waiting_flow_runs` | One wait entry per flow run | PRIMARY KEY (`run_id`) |
| `platform_api_keys` | Unique credential hash | UNIQUE (`key_hash`) |
| `agents` | Unique agent name | UNIQUE (`name`) |
| `agents` | Unique memory namespace | UNIQUE (`memory_namespace`) |
| `memory_trace_nodes` | Unique position within a trace | UNIQUE (`trace_id`, `position`) |
| `user_identity` | One identity record per user | UNIQUE (`user_id`) |

### Application-level only (no DB constraint backing)
| Table | Missing Constraint | Risk |
|---|---|---|
| `webhook_subscriptions` | UNIQUE (`event_type`, `callback_url`) | Duplicate delivery on same event |
| `platform_api_keys` | UNIQUE (`user_id`, `name`) | Ambiguous key listing/revocation |
| `execution_units` | UNIQUE (`source_type`, `source_id`) | Duplicate EU per FlowRun possible |
| `dynamic_flows` | UNIQUE (`flow_name`) | Nondeterministic flow after restart |
| `dynamic_nodes` | UNIQUE (`node_name`) | Nondeterministic node after restart |
| `flow_runs` | No business-key UNIQUE | Concurrent duplicate flows possible |

---

## Risk Matrix

| Finding | Severity | Surface | Fix Complexity | Priority |
|---|---|---|---|---|
| IDEM-2 — webhook duplicate subscriptions | High | Surface 6 | Small | 1 |
| IDEM-4 — execution_units duplicate source | Medium | Surface 5 | Medium | 2 |
| IDEM-5 — dynamic_flows/nodes no UNIQUE name | Medium | Surface 6 | Small | 3 |
| IDEM-1 — syscall last-write-wins no error | Medium | Surface 2 | Small | 4 |
| IDEM-3 — platform_api_keys name not unique | Medium | Surface 4 | Small | 5 |
| IDEM-6 — schema concurrent bootstrap TOCTOU | Low | Surface 1 | Medium | 6 |
| IDEM-8 — APScheduler stub duplicate behavior | Low | Surface 3 | Small | 7 |
| IDEM-7 — syscall registry not persisted | Low | Surface 2 | Architectural | 8 |

---

## Recommended Fix Order

1. **IDEM-2** — Add `UNIQUE(event_type, callback_url)` to `webhook_subscriptions` +
   ON CONFLICT DO NOTHING in the webhook registration service. Prevents duplicate
   event delivery, which can cause non-idempotent side effects in third-party systems.

2. **IDEM-5** — Add `UNIQUE(flow_name)` to `dynamic_flows` and `UNIQUE(node_name)` to
   `dynamic_nodes`. Switch platform loader INSERT paths to ON CONFLICT DO UPDATE.
   Low migration risk; prevents nondeterministic behavior after restart.

3. **IDEM-3** — Add `UNIQUE(user_id, name)` to `platform_api_keys`. Add pre-check in
   `api_key_service.create_key()`. Low migration risk.

4. **IDEM-1** — Change `register_syscall` to raise `ValueError` when re-registering
   the same name with a *different* handler. Same-handler re-registration stays
   idempotent. Add a startup post-bootstrap check for syscall name collisions.

5. **IDEM-4** — Add `UNIQUE(source_type, source_id)` (partial index excluding NULLs)
   to `execution_units`. Requires careful migration given existing data may already
   have `source_id=NULL` rows.

6. **IDEM-6** — For hardened multi-instance deployments: add DB advisory lock
   (`pg_try_advisory_lock`) around the blank-database bootstrap path.

7. **IDEM-8** — Set `replace_existing=True` as the default in the APScheduler stub
   to match production APScheduler's enforcement model.

8. **IDEM-7** — No immediate fix; document the startup phase ordering requirement
   and ensure production health checks block traffic until Phase 8 syscall
   registration is complete.

---

## What Was NOT Audited

- **App-layer routes** (`apps/*/routes/`) — only `AINDY/routes/` and platform-layer
  patterns were audited; domain-specific POST endpoints were not checked individually.
- **Memory subsystem** (`memory_nodes`, `memory_links`) — not included in this pass;
  requires separate audit focused on content-addressed write semantics.
- **Nodus scheduled jobs** (`nodus_scheduled_jobs` table) — DB-level uniqueness on
  job name not confirmed; only table existence and column structure reviewed.
- **Race conditions in concurrent HTTP requests** — analysis was static; no load
  testing or concurrent request simulation was performed.
- **MongoDB collections** — Mongo was unavailable (`AINDY_SKIP_MONGO_PING=1`) and
  its schema is unstructured; social/analytics features using Mongo were excluded.
- **Alembic migration idempotency** — individual migration scripts were not reviewed
  for `IF NOT EXISTS` / `IF EXISTS` guards.
- **Agent runs and agent steps tables** — no UNIQUE constraints beyond PK were
  checked for business-key columns.
- **Cross-tenant isolation invariants** — tenant isolation is enforced at the syscall
  dispatcher level (`_resolve_tenant_user_id`) but was not end-to-end tested at the
  DB level.
