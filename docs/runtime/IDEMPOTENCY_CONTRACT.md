---
title: "Idempotency Contract"
last_verified: "2026-05-24"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Idempotency Contract

## Purpose and Scope

This document is the single canonical reference for how A.I.N.D.Y. prevents duplicate
execution of real-world side effects. It covers three enforcement layers, the
`EffectRecord` lifecycle, the `action_id` derivation contract, and the interaction with
the retry and execution contracts.

Scope: runtime-owned execution paths only. App-layer routes, Memory subsystem, and
MongoDB collections are excluded (see **What This Contract Does Not Cover**).

---

## Three Idempotency Layers

### Layer 1 — Database-Level Uniqueness Constraints

Partial unique indexes on runtime-owned tables prevent duplicate rows at the storage
layer regardless of application logic:

| Table | Index | Condition |
|---|---|---|
| `webhook_subscriptions` | `uq_webhook_subscriptions_url_event` | active rows only |
| `platform_api_keys` | `uq_platform_api_keys_key_hash` | non-revoked rows only |
| `execution_units` | `uq_execution_units_correlation_status` | pending/executing only |
| `dynamic_flows` | `uq_dynamic_flows_name_version` | non-deleted rows only |
| `dynamic_nodes` | `uq_dynamic_nodes_name_version` | non-deleted rows only |

These indexes are created by Alembic migration `0002_idempotency_constraints`. They are
the last line of defense: a runtime bug that bypasses application-level checks will still
fail at the DB constraint boundary.

### Layer 2 — Alembic Migration Idempotency

The Alembic migration chain uses a separate version table (`alembic_version_runtime`)
so runtime migrations never conflict with monolith migrations. Each migration script is
idempotent when run against an already-migrated database because `CREATE TABLE IF NOT
EXISTS` / `CREATE INDEX IF NOT EXISTS` guards are used throughout.

Migration chain: `0001` (empty baseline) → `0002` (idempotency constraints) → `0003`
(effect_records table) → `0004` (completed_at composite partial index for TTL cleanup).

### Layer 3 — Effect-Level Idempotency Gate (NF-5)

The gate in `SyscallDispatcher._dispatch()` prevents a handler from executing more than
once for the same logical operation on `EXACTLY_ONCE` syscalls. It sits between Step 2e
(deprecation check) and Step 3 (handler execution). See **EffectRecord Lifecycle** below.

---

## Required Invariants

1. **Every `EXACTLY_ONCE` syscall with a non-empty `execution_unit_id` must produce
   exactly one `EffectRecord` row before the handler executes.** The row is inserted with
   `status="pending"` by `_resolve_effect_record()` in `syscall_dispatcher.py` and
   committed immediately so it is durable across session close.

2. **A cache-hit response must return the stored `result_payload` without calling the
   handler.** When `_resolve_effect_record()` finds an existing row with
   `status="success"`, the dispatcher returns the cached envelope directly. The handler
   is not invoked.

3. **A handler exception must transition the `EffectRecord` to `status="failed"` and
   set `completed_at`.** `_complete_effect_record()` is called on all error exit paths
   (handler exception, non-dict return, stable schema mismatch). The session is committed
   before close.

4. **`AT_LEAST_ONCE` syscalls must not create any `EffectRecord` rows.** The gate is
   only entered when `eu.extra["retry_policy"]["execution_guarantee"] == "EXACTLY_ONCE"`.
   AT_LEAST_ONCE syscalls skip the gate entirely; no DB session is opened.

5. **A syscall without an `execution_unit_id` must skip the gate entirely.** The
   dispatcher captures `_orig_eu_id = context.execution_unit_id or ""` before
   `_resolve_trace_context()` synthesizes a UUID. If `_orig_eu_id` is empty, no gate
   session is opened and no `EffectRecord` is created.

6. **Gate DB failures must not fail the syscall.** The EU lookup is wrapped in
   `try/except`; on failure the gate is skipped (`_guarantee = "AT_LEAST_ONCE"`) and a
   warning is logged. Only the `EffectRecord` write itself is a hard invariant.

7. **The `action_id` is the deduplication key and must be deterministic.** Given the
   same `(action_type, input_payload, scope)` tuple, `compute_action_id()` must always
   produce the same SHA-256 hex digest. See **action_id Contract** below.

8. **`EffectRecord.action_id` must be unique across all rows.** Enforced by the
   `uq_effect_records_action_id` unique index on the `effect_records` table (migration
   0003). A race between two concurrent retries attempting to insert the same `action_id`
   results in a unique-constraint violation — the losing insert is rolled back and the
   in-band recovery path in `_resolve_effect_record()` decides whether to return the
   cached result, reset a stale/failed row, or degrade to AT_LEAST_ONCE for a live
   concurrent call. See **Stale Pending Recovery** below.

---

## EffectRecord Lifecycle

```
                  [syscall dispatched, EXACTLY_ONCE, eu_id present]
                                      |
                         _resolve_effect_record()
                                      |
               +----------------------+----------------------+
               |                                             |
         record not found                          record found, status="success"
               |                                             |
       INSERT pending row                         RETURN cached result_payload
       (commit immediately)                       (handler NOT called)
               |
    handler executes
               |
     +----------+----------+
     |                     |
  success              exception / non-dict / schema-mismatch
     |                     |
  UPDATE status=success  UPDATE status=failed
  SET result_payload     SET completed_at=now()
  SET completed_at=now() (commit immediately)
  (commit immediately)
     |
  return success envelope
```

State transition table:

| From state | Trigger | To state | Side effect |
|---|---|---|---|
| _(absent)_ | first dispatch, cache miss | `pending` | `INSERT` committed |
| `pending` | handler returns dict | `success` | `result_payload` persisted, `completed_at` set |
| `pending` | handler raises | `failed` | `completed_at` set |
| `pending` | EXACTLY_ONCE handler returns non-dict | `failed` + `SyscallContractViolation` raised | `completed_at` set; exception propagates to caller |
| `pending` | AT_LEAST_ONCE handler returns non-dict | `failed` (error envelope returned) | `completed_at` not set; no EffectRecord interaction |
| `success` | subsequent dispatch | `success` | no write; cached payload returned |
| `failed` | subsequent dispatch (non-race) | handler runs; row updated in-place | `_complete_effect_record` sets `status`=success/failed, `completed_at`=now() |
| `failed` | concurrent-insert race recovery | `pending` (reset) | `status` reset, `completed_at` cleared, `created_at` refreshed; commit |
| `pending` (stale, > 15 min old) | concurrent-insert race recovery | `pending` (reset) | `created_at` refreshed, `completed_at` cleared; commit |
| `pending` (fresh, ≤ 15 min old) | concurrent-insert race — live call in flight | `pending` (unchanged) | gate degrades to AT_LEAST_ONCE for this call; warning logged |

---

### Stale Pending Recovery

A `pending` row with `completed_at IS NULL` and `created_at` older than
`STALE_PENDING_THRESHOLD_SECONDS` (900 s = 15 min) is considered abandoned — the
handler was interrupted mid-execution and never called `_complete_effect_record`.

**How recovery works (in-band, at insert time):**

When `_resolve_effect_record()` attempts to `INSERT` a new pending row and receives an
`IntegrityError` on `uq_effect_records_action_id`, it:

1. Rolls back the failed insert (within the same session).
2. Re-queries the existing row by `action_id`.
3. Branches on the found row's state:
   - **`status="success"`** — the concurrent call already succeeded; return the cached
     `result_payload` (identical to the normal cache-hit path).
   - **`status="pending"` and `created_at >= now() - 900s`** — a live call is in
     flight. The gate degrades to `AT_LEAST_ONCE` for this invocation: return
     `(False, None)` and log a warning. Strict at-most-once under concurrent retry
     requires application-layer advisory locking.
   - **`status="pending"` and `created_at < now() - 900s`** — stale abandoned row.
     Reset in-place: `status="pending"`, `completed_at=NULL`,
     `created_at=now()`. Commit. The dispatcher takes ownership of the slot and
     the handler executes normally.
   - **`status="failed"`** — prior failure, not a live call. Reset in-place
     (same as stale pending). Consistent with the non-race retry-after-failure
     behavior: failed rows do not permanently block future attempts.

**Why in-band rather than a background sweeper:** recovery happens at the point of
contention, in the same transaction window, without any scheduler dependency.

**Threshold rationale:** 15 minutes is chosen to exceed the maximum expected handler
wall-clock time (including any downstream call timeouts) plus a safety margin.
The value lives in `AINDY/kernel/syscall_dispatcher.py` as
`STALE_PENDING_THRESHOLD_SECONDS = 900`.

---

## action_id Contract

`compute_action_id(action_type, input_payload, scope)` in `AINDY/core/execution_gate.py`:

```python
canonical = json.dumps(
    {"action_type": action_type, "input": input_payload, "scope": scope},
    sort_keys=True,
    separators=(",", ":"),
)
return hashlib.sha256(canonical.encode()).hexdigest()
```

Rules:

- **`action_type`** — the fully-qualified syscall name (e.g. `sys.v1.memory.write`).
- **`input_payload`** — the raw `payload` dict passed to `dispatch()`. Must be
  JSON-serialisable. Key order is normalised by `sort_keys=True`.
- **`scope`** — `str(context.execution_unit_id)` at the time of dispatch (the original
  EU id, before trace context synthesis). Two retries of the same EU id with the same
  payload produce the same `action_id`.
- The digest is a 64-character lowercase hex string (SHA-256).
- **Do not change this algorithm.** Any change invalidates all existing `EffectRecord`
  rows in production.

---

## Execution Guarantee Labels

| Label | Meaning | EffectRecord created? | Handler call on retry |
|---|---|---|---|
| `AT_LEAST_ONCE` | Default. Handler may be called multiple times. | No | Yes (always) |
| `EXACTLY_ONCE` | Handler must execute at most once per `(name, payload, eu_id)`. | Yes | No (cache hit) |

The label is stored in `RetryPolicy.execution_guarantee` (default `"AT_LEAST_ONCE"`) and
serialised into `ExecutionUnit.extra["retry_policy"]["execution_guarantee"]` by
`_resolve_policy_for_eu()` in `AINDY/core/execution_gate.py`.

The only named constant that currently sets `execution_guarantee="EXACTLY_ONCE"` is
`AGENT_HIGH_RISK` in `AINDY/core/retry_policy.py`.

`EXACTLY_ONCE` handlers must return a `dict`. A non-dict return is treated as a hard
contract violation: the `EffectRecord` is finalized as `"failed"` and
`SyscallContractViolation` is raised from `dispatch()`, propagating to the caller.
`AT_LEAST_ONCE` handlers that return a non-dict produce a normal error envelope; no
`SyscallContractViolation` is raised and no `EffectRecord` is created.

---

## Interaction with the Execution Contract

The Execution Contract (`EXECUTION_CONTRACT.md`) requires every execution to produce a
durable `ExecutionUnit` record before work begins. The idempotency gate depends on this:

- The gate reads `eu.extra["retry_policy"]["execution_guarantee"]` from the
  `ExecutionUnit` row. If no EU exists for the given `execution_unit_id`, the gate
  defaults to `AT_LEAST_ONCE` (graceful skip, logged as a warning).
- The `EffectRecord.execution_id` FK references `execution_units.id` with
  `ON DELETE SET NULL`, so EU deletion does not cascade to `EffectRecord` rows.

The gate adds a Step 2f between Step 2e (deprecation check) and Step 3 (handler
execution) in `SyscallDispatcher._dispatch()`. It does not alter the observable
execution contract shape: the response envelope format, trace propagation, quota
enforcement, and observability events are unchanged.

---

## Interaction with Retry Semantics

The retry contract (`RETRY_POLICY.md`) defines when a failed operation is retried and
how many times. The idempotency contract defines what happens when a retry reaches the
syscall dispatcher.

Interaction rules:

- `is_retryable_error(error)` in `AINDY/core/retry_policy.py` gates whether a retry is
  scheduled. Non-transient errors (permission denied, 404, 401, 403, invalid, blocked by
  policy) are not retried. This check runs in the Nodus agent step loop
  (`nodus_adapter.py`) and the flow node retry loop (`runner_steps.py`).
- When a retry IS scheduled and the syscall is `EXACTLY_ONCE`, the gate will find the
  existing `EffectRecord`. If the prior attempt succeeded, the cached result is returned
  without re-executing. If the prior attempt failed, a new execution attempt is allowed.
- `AGENT_HIGH_RISK` sets both `max_attempts=1` (no retry) and
  `execution_guarantee="EXACTLY_ONCE"`. In practice this means EXACTLY_ONCE syscalls
  under `AGENT_HIGH_RISK` are both not retried AND protected against concurrent
  duplicate execution.
- `backoff_ms=0` in all current policy constants. The idempotency gate does not
  introduce any delay.

---

## What This Contract Does Not Cover

- **App-layer routes** (`apps/*/routes/`) — domain POST endpoints that call service
  methods directly are not gated by the `EffectRecord` mechanism unless they go through
  `SyscallDispatcher.dispatch()`.
- **Memory subsystem** (`memory_nodes`, `memory_links`) — content-addressed write
  semantics require a separate audit.
- **MongoDB collections** — unstructured; excluded from the idempotency audit.
- **Nodus scheduled jobs** (`nodus_scheduled_jobs`) — DB-level uniqueness on job name
  not confirmed; separate audit required.
- **Race conditions between concurrent HTTP requests** — the unique index on
  `action_id` enforces structural uniqueness, but the pending-insert → handler →
  complete sequence is not atomic. A concurrent retry arriving between insert and
  complete will see a `pending` record and be allowed to proceed. Callers requiring
  strict at-most-once semantics must implement advisory locking at the application layer.
- **EffectRecord retention policy** — TTL cleanup is implemented but operates on
  finalized rows only. See **Retention and Cleanup** below.

---

## Retention and Cleanup

`effect_records` grows unboundedly without a deletion policy. A scheduled job handles
TTL cleanup automatically.

### Cleanup job

`_cleanup_expired_effect_records()` in `AINDY/platform_layer/scheduler_service.py` runs
every `EFFECT_RECORD_CLEANUP_INTERVAL_HOURS = 24` hours. It deletes finalized rows
(status ≠ `pending`, `completed_at IS NOT NULL`) older than
`EFFECT_RECORD_TTL_DAYS = 90` days in batches of up to
`EFFECT_RECORD_DELETE_BATCH_SIZE = 10_000` rows per commit, looping until fewer than
one full batch remains.

### Invariants

- **Pending rows are never deleted**, regardless of age. A pending row may still have
  a live handler running; deleting it would cause a spurious re-execution on the next
  retry.
- **Stale pending warning** — rows with `status = 'pending'` and `created_at` older
  than 1 hour trigger a `logger.warning` at each cleanup run. These rows indicate stuck
  or abandoned handlers and should be investigated manually.
- **Batched commits** — each batch is committed before the next is fetched, so a crash
  mid-run leaves partial progress rather than rolling back the entire job.

### Supporting index (migration 0004)

`ix_effect_records_completed_at_status` is a composite partial index on
`(completed_at, status) WHERE completed_at IS NOT NULL`, created by
`alembic/versions/0004_effect_records_completed_at_index.py`. This index makes the
cleanup query's filter selective at production volume without touching the hot
`action_id` lookup path.

### Observability

At the start of each run the job logs a scan line:

```
[effect_record_cleanup] scan: total=<N> pending=<P> eligible=<E>
```

At the end:

```
[effect_record_cleanup] done: deleted=<D> elapsed_ms=<T>
```

Row-count monitoring must be set up manually — there is no automated alert. Add a
dashboard panel or startup log line that surfaces the total row count so unbounded
growth is detected without polling. (tracked in TECH_DEBT.md as IDEM-9, now closed)

---

## Enforcement and Verification

The idempotency contract is verified at three levels:

### Unit tests (`tests/unit/test_idempotency_gate.py`)

Twelve tests (marked `runtime_only`) cover: gate skipped for AT_LEAST_ONCE; cache hit
short-circuit; handler called on miss; EffectRecord updated to failed on exception; gate
skipped when no EU id; deterministic action_id; EXACTLY_ONCE non-dict raises
`SyscallContractViolation`; AT_LEAST_ONCE non-dict returns error envelope; stale-pending
in-band recovery; concurrent live-pending degrades to AT_LEAST_ONCE; concurrent-success
race returns cached payload; failed-record race recovery. All use mocked sessions — no
live DB required.

### Integration tests (`tests/integration/test_schema_contract.py`)

- `test_alembic_version_runtime_at_head` — asserts `alembic_version_runtime` = `0004`.
- `test_effect_records_table_exists` — asserts the table is present in the schema.
- `test_effect_records_action_id_unique_index_exists` — asserts the unique index exists.

### Integration tests (`tests/integration/test_idempotency_gate_e2e.py`)

- `test_in_band_stale_pending_recovery_e2e` — commits a stale pending row, simulates
  the TOCTOU concurrent-insert race against a real Postgres instance, and asserts the
  row is reset in-place (`created_at` refreshed, `completed_at` cleared).

### Unit tests (`tests/unit/test_effect_record_cleanup.py`)

Six tests (no live DB required) cover: zero-eligible no-op; finalized rows deleted;
batch loop iterates until partial batch; single full batch drives a second iteration;
stale-pending warning logged; exception caught without propagating.

### Integration tests (`tests/integration/test_effect_record_cleanup_e2e.py`)

- `test_effect_record_cleanup_deletes_expired_rows` — inserts an expired success row, a
  pending row, and a recent success row against real Postgres; calls
  `_cleanup_expired_effect_records()`; asserts the expired row is deleted and the other
  two survive.

### Schema drift check (`scripts/check_schema_version.py`)

Hashes all files in `AINDY/db/models/` plus `AINDY/memory/memory_persistence.py` and
compares against `scripts/schema_version_baseline.json`. Fails CI if models change
without a `SCHEMA_CONTRACT_VERSION` bump in `AINDY/db/schema_contract.py`.

### Schema version (`AINDY/db/schema_contract.py`)

`SCHEMA_CONTRACT_VERSION = "2026-05-24.1"` — bumped when the
`ix_effect_records_completed_at_status` partial index was added to `effect_record.py`.
`scripts/schema_version_baseline.json` regenerated accordingly.

### Live Postgres verification (Phase 1 — 2026-05-24)

Verified against a real Postgres instance (pgvector/pg15, tmpfs):

- Migration chain 0001 → 0002 → 0003 applied cleanly from a blank database.
- `effect_records` schema matches the ORM model: 11 columns, correct types, FK with
  `ON DELETE SET NULL`, `gen_random_uuid()` server default, both indexes present.
- Alembic version table at revision `0003`.
- Integration tier: 35 passed, 9 skipped.
- E2E gate scenarios against live DB: 5/5 passed (EXACTLY_ONCE double-dispatch,
  AT_LEAST_ONCE no-record, handler failure, absent EU id, is_retryable_error wiring).

---

## Open Operational Questions

1. **IDEM-6 (deferred)** — Multi-instance deployments during cold start: if two
   instances call the blank-database bootstrap path simultaneously, both may attempt to
   `CREATE TABLE` the same tables. `checkfirst=True` in `create_all` mitigates but does
   not fully eliminate the race. A `pg_try_advisory_lock` around the bootstrap path
   would close this gap. (tracked in TECH_DEBT.md as IDEM-6)

2. **IDEM-7 (deferred)** — Syscall registration is not complete until Phase 8 of
   startup. Traffic that arrives between DB-ready and syscall-registry-ready may dispatch
   against an incomplete registry. Health checks must block ingress until registration is
   complete; current health endpoint does not assert this. (tracked in TECH_DEBT.md as IDEM-7)

