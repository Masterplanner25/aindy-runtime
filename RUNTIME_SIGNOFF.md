# Runtime Repo Signoff

Date: 2026-05-17
Operator: Codex
Runtime repo path: `C:\dev\aindy-runtime`

## Structure

- `AINDY/` present
- `AINDY/runtime_plugins.json` present
- `apps/` absent
- `client/` absent
- `alembic/` absent
- repo-root `aindy_plugins.json` absent
- root-level `routes/` absent by design; runtime route ownership stays under `AINDY.routes.*`

## Packaging

- package name: `aindy-runtime`
- console scripts:
  - `aindy-runtime -> AINDY.runtime_only:main`
  - `aindy-runtime-api -> AINDY.main:main`
- editable install with test extras succeeded:
  - `python -m pip install -e .[test] --no-build-isolation`

## Runtime Boundary

- no direct `AINDY -> apps.*` imports found in `AINDY/*.py`
- startup and health code boot without local `apps/`
- runtime route compatibility is internal to `AINDY.routes.*`

Command run:

```bash
rg -n "^\s*(from apps\.|import apps\.|from apps\b|import apps\b)" AINDY -g "*.py"
```

Result: no matches

## Runtime-Only Boot

Validated entrypoints:

- `python -c "... import AINDY.runtime_only as rt; print(rt.app is not None)"` -> `True`
- runtime-only API app imports and serves `/health`, `/ready`, `/api/version`

Observed `/api/version` runtime payload in runtime-only mode:

- `boot_mode`: `runtime-only`
- `boot_profile`: `platform-only`
- `boot_profile_source`: `AINDY_BOOT_MODE`
- `app_plugins_loaded`: `False`
- `app_plugin_count`: `0`

Observed runtime-only smoke details:

- `/health` -> `200`
- `/ready` -> `503` against a bare SQLite URL with no initialized schema
- `/api/version` -> `200`
- runtime-owned route prefixes present for `/apps/agent/*` and `/apps/memory/*`
- baseline runtime agent tools: `memory.recall`, `memory.write`

## Runtime Test Validation

Command run:

```bash
python -m pytest tests/unit/test_runtime_packaging.py tests/unit/test_runtime_compatibility_metadata.py tests/unit/test_runtime_boundary.py tests/unit/test_platform_only_startup.py tests/api/test_version_api.py tests/unit/test_runtime_only_test_fixtures.py -m runtime_only -q
```

Result:

- Passed: `17`
- Failed: `0`
- Skipped: `0`

## Remaining Cautions

- `/ready` remains schema-sensitive and returned `503` in the raw smoke check without initialized DB tables. This is an operational readiness behavior, not a runtime-only boot failure.
- runtime import/startup still requires the documented minimal environment (`DATABASE_URL`, secrets, provider keys).
- `AINDY.routes.platform.schemas.NodusFlowRequest` still emits the existing Pydantic field-shadowing warning for `register`.

## Decision

Go with caution.

The extracted runtime repo is structurally correct, packageable, git-initialized, and passes the runtime-owned validation subset. The remaining caution is readiness behavior against an uninitialized database, which should be treated as an expected operational prerequisite rather than a split blocker.
