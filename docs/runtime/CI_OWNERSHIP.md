---
title: "Runtime CI Ownership"
last_verified: "2026-05-23"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Runtime CI Ownership

This document defines which GitHub Actions checks are authoritative for
`aindy-runtime`.

## Authoritative Runtime Checks

Primary workflow:

- `.github/workflows/runtime-ci.yml`

These checks are runtime-owned and should live in `aindy-runtime`:

- runtime Python lint for `AINDY/` and runtime-owned `tests/`
- runtime docs validation for `docs/runtime/`
- runtime import-boundary guard against `apps.*`
- runtime-only `/api/version` and `/health` smoke validation
- runtime-only pytest coverage for the extracted runtime test tree
- runtime package build verification (`python -m build`, `twine check`)
- console-entrypoint verification for `aindy-runtime` and `aindy-runtime-api`

Related non-publishing workflow:

- `.github/workflows/release-staging.yml`
  - manual-only artifact build and staged release verification
  - not a normal push/PR status check

## Checks That Do Not Belong Here

These are not runtime-owned and should not move back into `aindy-runtime` CI:

- `apps.bootstrap` or app-profile pytest
- cross-app import boundary scans
- app manifest ownership validation
- app Alembic migration execution
- frontend unit tests, Playwright, or client build checks
- app or monolith Docker image checks

## Historical Monolith Checks

The archived combined repo previously bundled some of the following into one CI
matrix:

- frontend tests and E2E
- app bootstrap validation
- app-profile and integration tests
- monolith Docker build
- multi-service integration checks tied to the combined checkout

Those checks are historical in that repo and are no longer authoritative for
runtime signoff.

## Integration Test Tier

A PostgreSQL/Redis integration tier is wired in `runtime-ci.yml` as the
`integration-postgres` job:

- runs against pgvector:pg15 + Redis 7 service containers
- executes `tests/integration/` via `pytest.integration.ini`
- verifies schema bootstrap via `AINDY.db.schema_contract.ensure_runtime_schema()`
- uploads a `coverage-integration` XML artifact
- `continue-on-error: true` — non-blocking on PR, but tracked for regressions

Local service stack: `docker-compose -f docker-compose.test.yml up -d`

Integration tier markers:

| Marker | Requires |
|--------|----------|
| `integration` | Postgres DATABASE_URL |
| `redis` | REDIS_URL |
| `multi_instance` | fakeredis installed |
| `mongo` | MONGO_URL |
| `postgres` | PostgreSQL DATABASE_URL |

## Coverage Floors

Measured on 2026-05-23 against PostgreSQL (pg16) + Redis 7 via docker-compose.test.yml:

| Suite | Marker filter | Local TOTAL | CI TOTAL | Floor |
|-------|--------------|-------------|----------|-------|
| Unit (SQLite) | `runtime_only` | 43% | ~39% | **35%** |
| Integration (Postgres+Redis) | `integration and not mongo` | 31% | — | — |
| Combined | `runtime_only or integration` | 42% | — | — |

The local vs CI discrepancy (~4%) is attributed to platform-specific code paths on Linux.
The floor is set with a 4% buffer below the CI baseline (39% − 4% → 35%).

The `runtime_only` suite enforces `--cov-fail-under=35` in the `runtime-contracts` CI job.
The integration suite does not yet enforce a floor (set `--cov-fail-under` in `integration-postgres`
once the combined baseline stabilises above 50%).

## Current Gaps

Remaining gaps in runtime CI are intentional or still deferred:

- no runtime-owned Docker image build workflow yet
- coverage floor for the integration suite is not yet enforced
  (set `--cov-fail-under` in the `integration-postgres` job once a stable
  baseline is established under Postgres)

Add new checks only if they validate runtime-owned behavior without depending
on `apps/` or app-profile fixtures.
