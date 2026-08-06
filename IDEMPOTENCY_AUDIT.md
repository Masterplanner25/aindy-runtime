# A.I.N.D.Y. Runtime — Idempotency and Invariants Audit
**Date:** 2026-05-23
**Method:** Static analysis + targeted PostgreSQL tests (27 tests, 27 passed)
**Status:** IDEM-1, IDEM-2, IDEM-3, IDEM-4, IDEM-5, IDEM-8 fixed in Alembic split (2026-05-23)
**Last merged:** 2026-05-24 — incorporates findings from external Copilot conversation review.

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

### IDEM-1 — Syscall registry is last-write-wins with no error on conflicting re-registration ✓ FIXED
**Surface:** Surface 2 — Syscall Registration
**Severity:** Medium
**Fixed:** 2026-05-23 — `VersionedSyscallRegistry.__setitem__` now raises `ValueError` on conflicting re-registration with a different handler. Same-handler re-registration remains idempotent. (`AINDY/kernel/syscall_registry.py`)
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

### IDEM-2 — webhook_subscriptions has no UNIQUE constraint on callback_url ✓ FIXED
**Surface:** Surface 6 — Platform Registry and Plugin Loading
**Severity:** High
**Fixed:** 2026-05-23 — Alembic migration `0002` adds partial unique index `uq_webhook_subscriptions_event_url_active` on `(event_type, callback_url) WHERE is_active = true`. ORM `__table_args__` updated to match.
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

### IDEM-3 — platform_api_keys has no UNIQUE constraint on (user_id, name) ✓ FIXED
**Surface:** Surface 4 — API Endpoint Idempotency
**Severity:** Medium
**Fixed:** 2026-05-23 — Alembic migration `0002` adds partial unique index `uq_platform_api_keys_user_name_active` on `(user_id, name) WHERE is_active = true`. ORM `__table_args__` updated to match.
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

### IDEM-4 — execution_units has no UNIQUE constraint on (source_type, source_id) ✓ FIXED
**Surface:** Surface 5 — Flow Engine
**Severity:** Medium
**Fixed:** 2026-05-23 — Alembic migration `0002` adds partial unique index `uq_execution_units_source` on `(source_type, source_id) WHERE source_type IS NOT NULL AND source_id IS NOT NULL`. ORM `__table_args__` updated to match.
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

### IDEM-5 — dynamic_flows and dynamic_nodes have no UNIQUE constraint on name ✓ FIXED
**Surface:** Surface 6 — Platform Registry and Plugin Loading
**Severity:** Medium
**Fixed:** 2026-05-23 — Alembic migration `0002` creates named unique indexes `uq_dynamic_flows_name` and `uq_dynamic_nodes_name`. ORM models updated with explicit `Index(...)` in `__table_args__`.
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

### IDEM-8 — APScheduler stub (BackgroundScheduler) does not enforce replace_existing without ID match ✓ FIXED
**Surface:** Surface 3 — Scheduler and Job Registration
**Severity:** Low
**Fixed:** 2026-05-23 — Stub now raises `ConflictingIdError` when `add_job()` is called with a duplicate `id` and `replace_existing=False`, matching real APScheduler behavior. (`AINDY/apscheduler/schedulers/background.py`)
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

## Open Findings — Effect-Level Idempotency Layer

**Scope:** The IDEM-1 through IDEM-8 findings above address the DB-constraint and
registration layers of idempotency. Those guarantees are non-bypassable: PostgreSQL
UNIQUE constraint violations on `uq_webhook_subscriptions_event_url_active`,
`uq_platform_api_keys_user_name_active`, `uq_execution_units_source`,
`uq_dynamic_flows_name`, and `uq_dynamic_nodes_name` propagate as hard errors;
`ValueError` on syscall re-registration and `ConflictingIdError` in the APScheduler
stub are hard exceptions, not best-effort. The layer not yet addressed is
*effect-level* idempotency: whether a tool call or external action that succeeds but
whose response is lost will be retried safely without repeating the real-world effect.
The following findings define that gap.

---

### NF-1 — No persistent EffectRecord — tool calls cannot be deduplicated after a lost response ✓ FIXED
**Severity:** should-fix
**Fixed:** 2026-05-24 — Alembic migration `0003` creates the `effect_records` table with UNIQUE constraint on `action_id`. ORM model `EffectRecord` added to `AINDY/db/models/effect_record.py`. Registered in `AINDY/db/models/__init__.py`. `SCHEMA_CONTRACT_VERSION` bumped to `"2026-05-24"`.
**Surface:** `runtime/nodus_adapter.py`, `kernel/syscall_dispatcher.py`, `core/execution_gate.py`
**Type:** Missing deduplication primitive at the tool-call layer
**Description:**
The current codebase records that a step ran and what it returned (`AgentStep`,
`ExecutionUnit.extra["retry_policy"]`, event emissions) but has no persistent record
keyed to the logical effect. If `nodus_adapter.py` retries a tool call after a lost
response, the runtime cannot distinguish "the effect already succeeded but we never
heard back" from "the effect has not yet run." `docs/runtime/EXECUTION_CONTRACT.md`
Invariant 7 requires `external.call.started/completed` events, but those are event
logs, not a queryable deduplication table.
**Evidence:**
`core/execution_gate.py` — `require_execution_unit()` creates an EU but no
per-effect record keyed to action identity.
`runtime/nodus_adapter.py` — `_execute_agent_step()` calls the tool and logs the
result but has no prior-success check keyed to action identity.
**Correct behavior:**
Add an `effect_records` table (migration `0003`):
```sql
CREATE TABLE effect_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id TEXT NOT NULL UNIQUE,  -- hash(action_type + input_hash + scope)
    action_type TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    execution_id UUID REFERENCES execution_units(id),
    step_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | success | failed
    result_payload JSONB,
    external_receipt JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX ix_effect_records_execution_id ON effect_records (execution_id);
```
Before any syscall handler that produces external side effects, check for an existing
`success` record and short-circuit. Persist `pending` before calling; update to
`success/failed` after.
**Fix complexity:** Medium (new ORM model + migration + service integration)

---

### NF-2 — No deterministic action_id — retries of the same step are not recognized as the same logical action ✓ FIXED
**Severity:** should-fix (prerequisite for NF-1)
**Fixed:** 2026-05-24 — `compute_action_id(action_type, input_payload, scope)` added to `AINDY/core/execution_gate.py`. Returns a deterministic sha256 hex digest from the canonical JSON of the three arguments (`sort_keys=True`). Importable as `from AINDY.core.execution_gate import compute_action_id`. Prerequisite for NF-1 (EffectRecord). No schema changes.
**Surface:** `runtime/nodus_adapter.py`, `core/execution_gate.py`
**Type:** Missing content-addressed key for effect deduplication
**Description:**
The runtime has `execution_id` (identifies a run) and `correlation_id` (traces a
request) but no content-addressed key for a specific tool invocation and its inputs.
Two retries of the same step with the same inputs have the same `execution_id` and
`step_id` but the runtime has no canonical way to recognize them as "the same logical
action." This key is the prerequisite for NF-1.
**Evidence:**
`runtime/nodus_adapter.py` — `_execute_agent_step(step, ...)` has no `action_id`
computation. `core/execution_gate.py` — `require_execution_unit()` accepts `eu_type`
and `extra` but produces no per-effect fingerprint.
**Correct behavior:**
Add a utility in `core/execution_gate.py`:
```python
import hashlib, json

def compute_action_id(action_type: str, input_payload: dict, scope: str) -> str:
    canonical = json.dumps(
        {"action_type": action_type, "input": input_payload, "scope": scope},
        sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
```
Compute before each external tool call in `nodus_adapter.py` from
`(step["tool"], step.get("input", {}), execution_unit_id)`. Pass to EffectRecord
(NF-1). No schema changes beyond NF-1.
**Fix complexity:** Small (pure function, no migration)

---

### NF-3 — `is_retryable_error()` exists in retry_policy.py but is not wired into any retry loop ✓ FIXED
**Severity:** should-fix
**Fixed:** 2026-05-24 — `is_retryable_error()` called in `_handle_node_status()` RETRY branch (`AINDY/runtime/flow_engine/runner_steps.py`) and in `_execute_agent_step()` retry loop (`AINDY/runtime/nodus_adapter.py`). Non-transient errors (404, 401, 403, permission, invalid, not found, blocked by policy) now short-circuit remaining retry attempts.
**Surface:** `runtime/flow_engine.py`, `runtime/nodus_adapter.py`
**Type:** Self-declared gap — function written but not called
**Description:**
`docs/runtime/RETRY_POLICY.md` §"Error classification" explicitly states:
"`is_retryable_error(error: str | None) -> bool` returns `False` for error strings
containing: `permission`, `unauthorized`, `forbidden`, `not found`, `404`, `401`,
`403`, `invalid`, `blocked by policy`. **Current execution loops do not call this
function yet.** It is the central place to add the check when a caller wants to
short-circuit retries on non-transient errors." A 404 or permission error currently
exhausts all retry attempts needlessly, adding latency and unnecessary syscall overhead.
**Evidence:**
`core/retry_policy.py` — `is_retryable_error()` function defined and documented.
`runtime/flow_engine.py` — `PersistentFlowRunner.resume()` retry branch does not
call `is_retryable_error()`.
`runtime/nodus_adapter.py` — `_execute_agent_step()` retry loop
(`for attempt in range(1, max_attempts + 1)`) does not call `is_retryable_error()`.
**Correct behavior:**
In both retry loops, add before the retry decision:
```python
from core.retry_policy import is_retryable_error

# after catching exception:
if not is_retryable_error(str(exc)):
    break  # non-transient — don't waste remaining attempts
```
Two-line addition in each retry path. No schema changes.
**Fix complexity:** Small (two-line change in each of two files)

---

### NF-4 — RetryPolicy has no formal execution guarantee label (EXACTLY_ONCE / AT_MOST_ONCE / AT_LEAST_ONCE) ✓ FIXED
**Severity:** nice-to-have
**Fixed:** 2026-05-24 — `execution_guarantee: str = "AT_LEAST_ONCE"` field added to `RetryPolicy` dataclass (`AINDY/core/retry_policy.py`). `AGENT_HIGH_RISK.execution_guarantee` set to `"EXACTLY_ONCE"`. All other named constants inherit the default `"AT_LEAST_ONCE"`. `_resolve_policy_for_eu()` in `AINDY/core/execution_gate.py` now persists `execution_guarantee` into `ExecutionUnit.extra["retry_policy"]` (JSONB — no DB migration).
**Surface:** `core/retry_policy.py`, `core/execution_gate.py`
**Type:** Implicit semantics not named or surfaced
**Description:**
`RetryPolicy` encodes `high_risk_immediate_fail=True` (implicitly AT_MOST_ONCE) and
`max_attempts=1` (implicitly EXACTLY_ONCE for high-risk) but these semantics are not
named or surfaced. An operator configuring a new execution type has no way to express
"this must never repeat" except by learning the `high_risk_immediate_fail` convention.
Connecting the label to NF-1's EffectRecord would make EXACTLY_ONCE enforcement automatic.
**Evidence:**
`core/retry_policy.py` — `RetryPolicy` dataclass has no `execution_guarantee` field.
`docs/runtime/RETRY_POLICY.md` — `AGENT_HIGH_RISK` constant (`max_attempts=1,
high_risk_immediate_fail=True`) implicitly encodes EXACTLY_ONCE semantics without
naming them.
**Correct behavior:**
Add field to `RetryPolicy`:
```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    backoff_ms: int = 0
    exponential_backoff: bool = False
    high_risk_immediate_fail: bool = False
    execution_guarantee: str = "AT_LEAST_ONCE"  # "EXACTLY_ONCE" | "AT_MOST_ONCE" | "AT_LEAST_ONCE"
```
Set `AGENT_HIGH_RISK` to `execution_guarantee="EXACTLY_ONCE"`. Persist into
`ExecutionUnit.extra` (JSONB — no DB migration). When NF-1 lands, check this field to
decide whether EffectRecord dedup is mandatory.
**Fix complexity:** Small (dataclass field + constant update + EU extra pass-through)

---

### NF-5 — No idempotency gate at the syscall/handler boundary
**Severity:** should-fix
**Surface:** `kernel/syscall_dispatcher.py`
**Type:** Missing enforcement point — EffectRecord exists but is never checked pre-dispatch
**Description:**
`kernel/syscall_dispatcher.py` has ordered dispatch steps (Step 5: tenant isolation,
Step 6: quota check, then handler executes). There is no Step 6.5 that checks for a
prior successful EffectRecord before invoking the handler. Without this gate, even after
NF-1 lands the runtime will still make the handler call before checking for a prior
result. This gate must be a hard block (not try/except) — the OS layer is non-fatal by
design, but an idempotency gate must be non-bypassable.
**Evidence:**
`docs/runtime/OS_ISOLATION_LAYER.md` §1 — dispatch step sequence documented; no
EffectRecord lookup step present between quota check and handler call.
`kernel/syscall_dispatcher.py` — dispatcher calls handler after Step 6 with no
prior-success check.
**Correct behavior:**
In `SyscallDispatcher.dispatch()`, after Step 6 (quota check):
```python
# Step 6.5: idempotency gate (only for EXACTLY_ONCE syscalls)
if policy.execution_guarantee == "EXACTLY_ONCE":
    cached = _check_effect_record(action_id)
    if cached:
        return cached  # short-circuit; no handler call
    _create_effect_record_pending(action_id, ...)
```
Requires NF-1 (EffectRecord table), NF-2 (`action_id` computation), and NF-4
(`execution_guarantee` label) to be complete first.
**Fix complexity:** Medium (depends on NF-1, NF-2, NF-4)

---

**Scope boundary — declarative pre/postcondition contracts:** The Tiered Contract
enforces *capability-scoped* access control (Tier 1 / Tier 2 boundary, capability tokens,
`validate_run_scope()`, `check_tool_permission()`) rather than business-logic
preconditions. Declarative per-step pre/postconditions (e.g., "account_has_funds" before
a charge, "payment_confirmed" after) target domain-specific business correctness and
require a domain-aware condition evaluator. That is out of scope for a general-purpose
agent execution runtime. NF-1 through NF-5 address effect-level deduplication, which is
the correct scope boundary.

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
- **Redis-backed global concurrent execution limits** — `docs/runtime/OS_ISOLATION_LAYER.md`
  §3 documents that `ResourceManager` is a per-process in-memory singleton;
  `MAX_CONCURRENT_PER_TENANT` is enforced within a single process only. Global
  enforcement requires Redis-backed atomic counters (`can_execute()` / `mark_started()` /
  `mark_completed()`). This is tracked as a known gap in the OS isolation layer and is
  out of scope for this idempotency audit.

---

## Recommended Next Actions

Ordered by prerequisite dependency. PR-1 is standalone; PR-2 through PR-5 form a chain.

**PR-1 — Wire `is_retryable_error()` into both retry loops** *(NF-3)*
Two-line change each in `runtime/flow_engine.py` and `runtime/nodus_adapter.py`. The
function already exists in `core/retry_policy.py`. Zero schema changes. Prevents
retry storms on auth and 404 errors. Standalone — no dependencies on other PRs.

**PR-2 — Implement `compute_action_id()` utility** *(NF-2)*
Add to `core/execution_gate.py`. No schema changes. Pure function:
`sha256(json.dumps({"action_type", "input", "scope"}, sort_keys=True))`. Unblocks
PR-3 and PR-5 without itself requiring a DB migration.

**PR-3 — Add `effect_records` table (migration `0003`)** *(NF-1)*
New ORM model in `AINDY/db/models/` + Alembic migration. Primary key + UNIQUE on
`action_id`. Introduces the "what happened in the world" record separate from
`ExecutionRecord`. Requires PR-2. No changes to existing tables.

**PR-4 — Add `execution_guarantee` field to `RetryPolicy`** *(NF-4)*
Extend the `RetryPolicy` dataclass in `core/retry_policy.py` with
`execution_guarantee: str = "AT_LEAST_ONCE"`. Set `AGENT_HIGH_RISK.execution_guarantee
= "EXACTLY_ONCE"`. Persist into `ExecutionUnit.extra` (JSONB — no DB migration).
No dependencies on other PRs; provides the label hook for PR-5.

**PR-5 — Add idempotency gate in `SyscallDispatcher.dispatch()`** *(NF-5)*
Insert EffectRecord check after Step 6 (quota check) in `kernel/syscall_dispatcher.py`
for EXACTLY_ONCE syscalls. Hard gate (not try/except). Short-circuits repeated calls
and returns the cached result. Requires PR-2, PR-3, and PR-4 to be complete.
