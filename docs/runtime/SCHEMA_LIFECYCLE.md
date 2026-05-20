---
title: "Schema Lifecycle"
last_verified: "2026-05-20"
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
  - startup does not apply this automatically unless the operator explicitly
    enables `AINDY_SCHEMA_RECONCILE=true`
- `incompatible_manual`
  - the existing schema has unsafe drift or a change the runtime will not
    mutate in place
  - examples:
    - column type mismatch
    - nullability mismatch
    - primary-key mismatch
    - missing non-null column without a safe DB-side default
  - startup fails closed and manual intervention is required

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
- Explicit runtime-owned reconcile:
  - set `AINDY_SCHEMA_RECONCILE=true`
  - restart the API or worker
  - the runtime applies additive-safe schema reconciliation and re-validates

This is intentionally explicit. The runtime does not silently mutate an
already-initialized production schema.

### Existing database with incompatible/manual drift

- Startup fails closed.
- `/health` reports `schema_state=incompatible_manual`.
- The runtime will not attempt to coerce the schema automatically.
- Operators must repair the schema out of band before restart.

## Current Safety Boundary

The runtime-owned reconcile path is additive only. It is not a general-purpose
migration engine and it does not claim to replace full migration planning for
destructive or shape-changing schema work.

What remains trusted:

- packaged runtime ORM metadata under `AINDY/db/models/`
- runtime-owned reconcile logic in `AINDY/db/schema_contract.py`

What the runtime intentionally does not do:

- apply destructive migrations automatically
- coerce live column types
- tighten nullability in place
- rewrite primary-key shape

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
