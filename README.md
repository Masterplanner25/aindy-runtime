# aindy-runtime

`aindy-runtime` is the extracted runtime infrastructure package from the former monolith.

It contains the runtime code under `AINDY/`, the runtime-only manifests and entrypoints,
runtime-owned documentation, and the runtime contract test suite. It does not include
`apps/`, `apps.bootstrap`, or app-profile-only tests and docs.

Current release posture:

- suitable for trusted internal runtime deployments
- stable only for the explicitly declared public surfaces under `docs/runtime/`
- not a hardened third-party extension platform
- does not provide in-process sandboxing for trusted Python extensions
- `AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS=true` is a trusted-code override, not a safe third-party extension mode

## Quickstart

**Prerequisites:** Docker Desktop (or Docker Engine + Compose plugin v2.20+).

```bash
# 1. Clone
git clone https://github.com/Masterplanner25/aindy-runtime.git
cd aindy-runtime

# 2. Configure
cp AINDY/.env.example AINDY/.env
#    Open AINDY/.env and set at minimum:
#      SECRET_KEY  — generate: python3 -c "import secrets; print(secrets.token_hex(32))"
#      OPENAI_API_KEY

# 3. Start
docker compose up -d

# 4. Run migrations + wait for ready
#    (alembic upgrade head runs automatically inside the api container on boot)
#    Watch progress:
docker compose logs -f api

# 5. Verify
curl http://localhost:8000/ready    # → {"status": "ok", ...}

# 6. Visit the platform UI
#    http://localhost:8000/platform
```

**Production-shaped deployment** (Redis + distributed worker):
```bash
docker compose --profile full up -d
```

**With metrics** (Prometheus on port 9090):
```bash
docker compose --profile full --profile monitoring up -d
```

> **Note — database host inside compose:** The `DATABASE_URL` in `AINDY/.env`
> must use the compose service name as the host, not `localhost`:
> ```
> DATABASE_URL=postgresql://aindy:aindy@postgres:5432/aindy
> ```
> `postgres` resolves on the compose network; `localhost` does not.

> **Note — pgvector required:** The compose file uses `pgvector/pgvector:pg16`
> instead of the stock `postgres:16-alpine`. The runtime stores memory embeddings
> as `VECTOR(1536)` columns, which requires the PostgreSQL `pgvector` extension.
> `docker/init-pgvector.sql` runs `CREATE EXTENSION IF NOT EXISTS vector` on
> first initialization. If you bring your own PostgreSQL instance, run that
> statement once before first boot.

> **Note — published database ports:** `postgres` (5432), `redis` (6379), and
> `mongo` (27017) publish to the host for local development convenience. For
> production deployments on a cloud VM, remove the `ports:` blocks from those
> services or use a compose override file. See `TECH_DEBT: COMPOSE-PROD-PORTS-1`.

## Install

```bash
pip install aindy-runtime
```

**Import name:** The distribution name is `aindy-runtime` but the importable module is `AINDY`
(uppercase — it is an acronym). `import aindy_runtime` will not work.

```python
from AINDY._version import __version__  # correct
from AINDY.platform_layer.deployment_contract import deployment_contract_summary
# import aindy_runtime  ← ImportError
```

For local development (editable install from source):

```bash
python -m pip install -e .
```

For staged release builds:

```bash
python -m pip install -e .[release]
```

## CLI

```
aindy-runtime serve      Start the HTTP API server (requires DATABASE_URL)
aindy-runtime sandbox    Report sandbox capabilities and exit
aindy-runtime --help     Show help and exit
aindy-runtime --version  Show version and exit
```

## Run

Runtime-only API boot:

```bash
aindy-runtime serve
```

Minimum runtime environment:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
SECRET_KEY=...
OPENAI_API_KEY=sk-...
```

For local smoke tests only, SQLite remains opt-in and must be declared
explicitly:

```bash
DATABASE_URL=sqlite://
AINDY_ALLOW_SQLITE=1
SECRET_KEY=runtime-local-secret-key
OPENAI_API_KEY=sk-test-placeholder
```

Equivalent module and ASGI forms:

```bash
python -m AINDY.runtime_only serve
uvicorn AINDY.runtime_only:app
```

## Deployment Ownership

`aindy-runtime` owns the runtime deployment contract:

- runtime-only boot and runtime entrypoints
- the runtime manifest at `AINDY/runtime_plugins.json`
- runtime packaging and release staging
- runtime-owned health, readiness, and compatibility behavior
- runtime deployment documentation under `docs/runtime/`

It does not own app deployment assets such as:

- repo-root `aindy_plugins.json`
- `apps.bootstrap`
- `alembic/`
- `client/`

Those belong in `aindy-apps-monolith`.

## Supported Use Today

- runtime-only HTTP deployment with the documented deployment profiles
- first-party app integration through the documented trust and ownership model
- operator-managed use of experimental extension and orchestration surfaces

Not claimed by this repo today:

- third-party extension isolation
- sandboxed in-process plugin execution
- fully frozen external semantics for experimental HTTP, syscall-adjacent, or extension surfaces

## Branch And PR Model

Active contribution model for this repo:

- protected branch: `main`
- pull requests should target: `main`
- feature work should branch from the current `main`

This repo does not use the archived monolith `develop`-targeting flow.

## Verify

```bash
python -m pytest \
  tests/unit/test_runtime_only_test_fixtures.py \
  tests/unit/test_platform_only_startup.py \
  tests/unit/test_runtime_packaging.py \
  tests/unit/test_runtime_boundary.py \
  tests/unit/test_runtime_compatibility_metadata.py \
  tests/api/test_version_api.py \
  -m runtime_only -q
```

Runtime CI scope in `.github/workflows/runtime-ci.yml` now covers the
runtime-owned push/PR baseline:

- lint runtime-owned Python code with Ruff
- validate runtime-doc frontmatter under `docs/runtime/`
- install the runtime package and test extras in editable mode
- assert runtime code does not import `apps.*`
- verify the `aindy-runtime` console script
- smoke `GET /health` and `GET /api/version` in runtime-only mode
- run the full extracted runtime-owned pytest suite (`tests -m runtime_only`)
- build wheel and sdist artifacts and run `twine check`

GitHub Actions note:

- `runtime-ci.yml` is the automatic push/PR check for `main`
- `release-staging.yml` is intentionally manual-only (`workflow_dispatch`) and
  will not appear as a normal push/PR status check until it is dispatched

Staged release flow in `.github/workflows/release-staging.yml` is intentionally
non-publishing:

- verify runtime version and compatibility metadata
- build wheel and sdist artifacts
- run `twine check`
- upload artifacts for inspection

Checks intentionally left out of the runtime repo because they remain
app- or monolith-owned:

- app bootstrap and app-profile tests
- cross-app import boundary checks
- app-database Alembic migration execution for app-owned tables
- frontend, Playwright, and client build checks
- Docker image and full monolith service-matrix validation

## Runtime Schema Bootstrap

The extracted runtime is self-hostable for its own database surface.

- startup, worker boot, and readiness checks use packaged runtime ORM metadata
  as the schema contract
- on a blank database, the runtime bootstraps runtime-owned tables directly from
  that packaged metadata
- on an additive-safe but out-of-date schema, startup requires explicit
  `AINDY_SCHEMA_RECONCILE=true` before mutating an initialized database
- on incompatible drift, startup fails closed when `AINDY_ENFORCE_SCHEMA=true`
- app-owned tables and the monolith Alembic history remain app-repo concerns

## Docs

Runtime-owned documentation lives under `docs/runtime/`.

Release staging guidance lives in `docs/runtime/RELEASE_STAGING.md`.

CI ownership guidance lives in `docs/runtime/CI_OWNERSHIP.md`.

Deployment topology guidance lives in `docs/runtime/DEPLOYMENT_PROFILES.md`.

Manual GitHub branch-protection and review settings guidance lives in
`docs/runtime/GITHUB_SETTINGS_CHECKLIST.md`.

## Validated Split Check

Validated on `2026-05-17` in the extracted repo:

```bash
python -m pytest \
  tests/unit/test_runtime_only_test_fixtures.py \
  tests/unit/test_platform_only_startup.py \
  tests/unit/test_runtime_packaging.py \
  tests/unit/test_runtime_boundary.py \
  tests/unit/test_runtime_compatibility_metadata.py \
  tests/api/test_version_api.py \
  -m runtime_only -q
python -c "import os, json; os.environ.update({'AINDY_BOOT_MODE':'runtime-only','DATABASE_URL':'sqlite://','MONGO_URL':'','AINDY_ALLOW_SQLITE':'1','OPENAI_API_KEY':'sk-test-placeholder','DEEPSEEK_API_KEY':'ds-test-placeholder','SECRET_KEY':'runtime-integration-secret','AINDY_API_KEY':'runtime-integration-api-key','PERMISSION_SECRET':'runtime-integration-permission-secret','AINDY_SKIP_MONGO_PING':'1','SKIP_MONGO_PING':'1'}); from fastapi.testclient import TestClient; import AINDY.main as main; payload=TestClient(main.app, raise_server_exceptions=False).get('/api/version').json(); print(json.dumps(payload['runtime'], sort_keys=True)); print(json.dumps(payload['compatibility'], sort_keys=True))"
```

Observed result:

- runtime-only `/api/version` reported `boot_profile=platform-only`
- `app_plugins_loaded` was `False`
- compatibility metadata reported `runtime_package.name=aindy-runtime`
