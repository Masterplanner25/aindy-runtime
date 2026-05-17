---
title: "Runtime CI Ownership"
last_verified: "2026-05-17"
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

## Current Gaps

Remaining gaps in runtime CI are intentional or still deferred:

- no Redis/PostgreSQL/Mongo service-matrix job in default runtime CI
- no runtime-owned Docker image build workflow yet
- no separate long-running integration tier beyond the extracted
  `runtime_only` pytest slice

Add those only if they validate runtime-owned behavior without depending on
`apps/` or app-profile fixtures.
