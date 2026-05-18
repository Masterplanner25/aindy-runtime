# Runtime Repo Secrets

This document defines the secrets and configuration expectations for
`aindy-runtime`.

Use it for:

- GitHub Actions configuration in this repo
- staged release workflow expectations
- runtime deployment environment setup

## GitHub Actions In This Repo

Current workflows:

- `.github/workflows/runtime-ci.yml`
- `.github/workflows/release-staging.yml`

### Secrets Required For CI

None.

Current runtime CI uses safe placeholder values in workflow `env` for:

- `OPENAI_API_KEY`
- `DEEPSEEK_API_KEY`
- `SECRET_KEY`
- `PERMISSION_SECRET`
- `AINDY_API_KEY`
- `DATABASE_URL`
- `MONGO_URL`

These are intentionally mocked or local-only in CI:

- no real OpenAI or DeepSeek key is required
- no GitHub Actions secret is required for runtime-only test execution
- SQLite in-memory is used for CI smoke and test runs

### Secrets Required For Release Staging

None for the current staged workflow.

`release-staging.yml`:

- builds artifacts
- verifies version/compatibility metadata
- runs `twine check`
- uploads artifacts

It does **not** publish to an external package index, so no PyPI token or other
publishing credential is required yet.

## Runtime Deployment Secrets

These are runtime-owned deployment environment requirements, not GitHub Actions
requirements.

Minimum real runtime deployment values:

- `SECRET_KEY`
  - strong random secret for JWT/session signing
- `PERMISSION_SECRET`
  - strong random secret for permission validation
- `AINDY_API_KEY`
  - internal API/service key
- `DATABASE_URL`
  - required runtime database connection

Common provider/service values:

- `OPENAI_API_KEY`
  - required only if deployed runtime features actually call OpenAI
- `DEEPSEEK_API_KEY`
  - required only if deployed runtime features actually call DeepSeek
- `MONGO_URL`
  - required only if deployed runtime features require Mongo-backed behavior
- `ALLOWED_ORIGINS`
  - required for real browser-facing deployments

Runtime-only boot does not require an app manifest secret.

## Safe Placeholder Guidance

Placeholder values such as:

- `sk-test-placeholder`
- `ds-test-placeholder`
- `ci-runtime-secret-key`

are acceptable in CI only because runtime CI avoids real external provider
calls and does not represent a production deployment.

Do not reuse CI placeholder values in deployed environments.
