---
title: "Schema Lifecycle"
last_verified: "2026-09-25"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Schema Lifecycle

This document defines the runtime-owned schema lifecycle contract for
`aindy-runtime`.

The runtime does not depend on monolith-owned Alembic assets. Schema lifecycle
is owned by `AINDY/db/schema_contract.py`.

## Supported Lifecycle Modes

The runtime distinguishes four operator-facing schema states:

- `blank_bootstrap`
  - no runtime-owned tables existed
  - the runtime created the full runtime-owned schema from packaged metadata
  - this path is automatic when `allow_bootstrap=True`
- `compatible`
  - the existing database matches the packaged runtime-owned schema contract
  - startup, worker readiness, and `/ready` proceed normally
- `upgrade_required`
  - the existing runtime-owned schema is missing additive-safe runtime assets
  - current runtime support is limited to:
    - creating missing runtime-owned tables
    - adding missing nullable columns
    - adding missing columns that have a database `server_default`
    - widening a bounded column to the packaged bound (`varchar(32)` →
      `varchar(128)`, or a `numeric` precision increase at the same scale). Every
      existing value fits, and PostgreSQL applies it without rewriting rows (FR-43)
  - startup does not apply this automatically unless the operator explicitly
    enables `AINDY_SCHEMA_RECONCILE=true`
- `incompatible_manual`
  - the existing schema has unsafe drift or a change the runtime will not
    mutate in place
  - examples:
    - column type mismatch, including a bound the packaged model LOWERS
      (a narrowing) or a `numeric` scale change
    - nullability mismatch
    - primary-key mismatch
    - missing non-null column without a safe DB-side default
  - startup fails closed and manual intervention is required

In addition to `schema_state`, the runtime now emits machine-readable:

- `schema_drift_classes`
  - concrete drift categories such as `additive_missing_table`,
    `additive_missing_column`, `additive_column_widen`, `unsupported_required_column`,
    `column_type_mismatch`, `column_nullability_mismatch`, or
    `primary_key_mismatch`
- `schema_remediation_categories`
  - `bootstrap`
  - `startup_reconcile`
  - `offline_migration`
  - `manual_repair`
- `schema_offline_migration_required`
  - whether the runtime expects an out-of-band migration or repair before
    restart
- `schema_inspection`
  - the runtime-owned inspection contract, including the safe module entrypoint
    `python -m AINDY.db.schema_ops inspect --format json`
- `schema_contract`
  - the current runtime-owned schema contract export, including table names,
    lifecycle states, and automatic-vs-manual drift boundaries

## Inspection Tooling

The runtime ships an inspection-only helper for operators:

```powershell
python -m AINDY.db.schema_ops inspect --format json
```

Properties:

- reads the current runtime-owned schema state
- emits the same contract version and drift metadata that `/health` exposes
- does not mutate the database
- exits with code `2` when `--require-compatible` is supplied and drift remains

Useful variants:

```powershell
python -m AINDY.db.schema_ops inspect --format text
python -m AINDY.db.schema_ops inspect --format json --require-compatible
python -m AINDY.db.schema_ops inspect --database-url postgresql://...
```

## Operator Workflow

### Blank database

- Start the runtime normally.
- The runtime bootstraps its runtime-owned schema automatically.
- `/health` reports schema `ok`.

### Existing compatible database

- Start the runtime normally.
- No schema mutation occurs.
- `/health` and `/ready` report schema `ok`.

### Existing database with additive runtime upgrade required

- Default behavior:
  - startup fails closed
  - `/health` reports schema unavailable with `schema_state=upgrade_required`
- inspect the exact drift with:
  - `python -m AINDY.db.schema_ops inspect --format json`
  - or `GET /health` / `GET /ready`
- Explicit runtime-owned reconcile:
  - set `AINDY_SCHEMA_RECONCILE=true`
  - restart the API or worker
  - the runtime applies additive-safe schema reconciliation and re-validates

This is intentionally explicit. The runtime does not silently mutate an
already-initialized production schema.

### Existing database with incompatible/manual drift

- Startup fails closed.
- `/health` reports `schema_state=incompatible_manual`.
- inspect the exact drift with:
  - `python -m AINDY.db.schema_ops inspect --format json`
  - or `GET /health`
- The runtime will not attempt to coerce the schema automatically.
- Inspect `schema_drift_classes` and `schema_remediation_categories`:
  - `offline_migration` means prepare and apply an out-of-band SQL migration
    while the runtime is stopped
  - `manual_repair` means the drift is broad enough that the operator must
    repair the schema shape directly before restart
- Operators must complete that out-of-band work before restart.

## Supported vs Unsupported Drift Classes

- Supported at startup:
  - `additive_missing_table`
  - `additive_missing_column`
  - `additive_column_widen`
- Supported only through explicit startup reconcile:
  - the same additive classes above, and only when
    `AINDY_SCHEMA_RECONCILE=true`
- Unsupported for startup-time mutation:
  - `unsupported_required_column`
  - `column_type_mismatch`
  - `column_nullability_mismatch`
  - `primary_key_mismatch`

## Current Safety Boundary

The runtime-owned reconcile path is additive only. It is not a general-purpose
migration engine and it does not claim to replace full migration planning for
destructive or shape-changing schema work.

What remains trusted:

- packaged runtime ORM metadata under `AINDY/db/models/`
- runtime-owned reconcile logic in `AINDY/db/schema_contract.py`

**What the drift check compares** (and therefore what `bootstrap-schema` vouches for
before it stamps `alembic_version_runtime` to head): tables, columns, type names, type
bounds (string length; `numeric` precision and scale), nullability and primary keys.
It does **not** compare indexes, constraints or data. Before FR-43 it did not compare
bounds either, so 2.22.0's widening of `system_events.source` was reported as "no table
changes" and `0020` was stamped over a `varchar(32)`. Bounds are not compared on SQLite,
which does not enforce a declared length.

What the runtime intentionally does not do:

- apply destructive migrations automatically
- coerce live column types (a widening is the one exception: it cannot lose a value)
- tighten nullability in place
- rewrite primary-key shape
- claim that unsupported drift can be fixed safely online

## Environment Controls

- `AINDY_ENFORCE_SCHEMA=true`
  - default safety gate
  - validates runtime-owned schema at startup
- `AINDY_SCHEMA_RECONCILE=true`
  - explicit opt-in for additive runtime-owned reconciliation on an already
    initialized schema
  - applies to API startup and worker readiness paths

Production guidance:

- keep `AINDY_ENFORCE_SCHEMA=true`
- enable `AINDY_SCHEMA_RECONCILE=true` only for a deliberate release operation
  that expects additive runtime-owned schema changes
- use `python -m AINDY.db.schema_ops inspect --format json --require-compatible`
  in release checks when you want a non-mutating compatibility gate
