# aindy-runtime

`aindy-runtime` is the standalone runtime package extracted from the former monolith.

It contains the runtime code under `AINDY/`, the runtime-only manifests and entrypoints,
runtime-owned documentation, and the runtime contract test suite. It does not include
`apps/`, `apps.bootstrap`, or app-profile-only tests and docs.

## Install

```bash
python -m pip install -e . --no-deps --no-build-isolation
```

For staged release builds:

```bash
python -m pip install -e .[release] --no-deps --no-build-isolation
```

## Run

Runtime-only API boot:

```bash
aindy-runtime
```

Minimum runtime environment:

```bash
DATABASE_URL=sqlite://
AINDY_BOOT_MODE=runtime-only
SECRET_KEY=...
AINDY_API_KEY=...
PERMISSION_SECRET=...
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=ds-...
```

Equivalent module and ASGI forms:

```bash
python -m AINDY.runtime_only
uvicorn AINDY.runtime_only:app
```

Generic API entrypoint:

```bash
aindy-runtime-api
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

Runtime CI scope in `.github/workflows/runtime-ci.yml` is intentionally narrow:

- install the runtime package and test extras in editable mode
- assert runtime code does not import `apps.*`
- verify `aindy-runtime` and `aindy-runtime-api` console scripts
- smoke `GET /health` and `GET /api/version` in runtime-only mode
- run only the extracted runtime-owned pytest suite

Staged release flow in `.github/workflows/release-staging.yml` is intentionally
non-publishing:

- verify runtime version and compatibility metadata
- build wheel and sdist artifacts
- run `twine check`
- upload artifacts for inspection

## Docs

Runtime-owned documentation lives under `docs/runtime/`.

Release staging guidance lives in `docs/runtime/RELEASE_STAGING.md`.

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
