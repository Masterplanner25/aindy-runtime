# A.I.N.D.Y. Runtime Re-Orientation Report

**Date:** 2026-05-23  
**Scope:** aindy-runtime (primary) + aindy-apps-monolith (boundary reference)  
**Prepared by:** Claude Sonnet 4.6 — full re-orientation pass

---

## System Snapshot

A.I.N.D.Y. (Artificial Intelligence Networked Dynamic Yielder) is a multi-agent, flow-engine-backed FastAPI runtime developed by Shawn Knight (GitHub: Masterplanner25) under the Infinity Algorithm / Masterplan Infinite Weave ecosystem. As of May 2026, the former monolith has been split into two active repos: `aindy-runtime` (this repo — the extracted installable infrastructure package, version 1.0.0) and `aindy-apps-monolith` (the app layer, client, Alembic history, and plugin manifest). Both repos passed their validated extraction signoffs on 2026-05-17 and the split is operationally coherent. The runtime is in Beta status (PyPI classifier 4 - Beta), suitable for trusted internal deployment, with a stable boot contract for `runtime-only` mode and an explicitly declared experimental tier for extension surfaces. Overall health is good: 220 of 221 tests pass, the boundary is clean, and all documented CI steps are implemented — with three gaps noted below.

---

## Split Architecture Map

### Declared Ownership Boundary

From `aindy-apps-monolith/README.md`:

> "It owns: `apps/`, `client/`, `aindy_plugins.json`, `alembic/`, app-profile tests and helpers, app-owned and shared app-facing docs. It does not vendor `AINDY/`. Runtime code, runtime-only entrypoints, and runtime-only docs live in the separate `aindy-runtime` repo."

From `aindy-runtime/README.md`:

> "`aindy-runtime` owns the runtime deployment contract: runtime-only boot and runtime entrypoints / the runtime manifest at `AINDY/runtime_plugins.json` / runtime packaging and release staging / runtime-owned health, readiness, and compatibility behavior / runtime deployment documentation under `docs/runtime/`. It does not own app deployment assets such as: repo-root `aindy_plugins.json` / `apps.bootstrap` / `alembic/` / `client/`."

From `docs/runtime/PUBLIC_API_CONTRACT.md`:

> "Apps may import only the modules listed in **Public Runtime API Modules**. Any `AINDY.*` module not listed in either section is internal runtime implementation and must not be imported by apps. New app imports from internal runtime modules are regressions. New runtime imports from `apps.*` are regressions."

### Runtime Module Tree

| Package / Module | Purpose |
|---|---|
| `AINDY/_version.py` | Single version source (`1.0.0`); read by `pyproject.toml`, `/api/version`, and compatibility metadata |
| `AINDY/agents/` | Agent orchestration: coordinator, runtime execution, event service, message bus, tool registry, tool syscalls, guardrails, stuck-run detection |
| `AINDY/apscheduler/` | Thin APScheduler wrappers (background, cron, interval triggers) |
| `AINDY/auth/` | API key authentication middleware |
| `AINDY/cli.py` | CLI entrypoint shim |
| `AINDY/config.py` | Pydantic settings; loads environment into `settings` singleton |
| `AINDY/core/` | Execution pipeline, dispatcher, envelope, gate (`require_execution_unit`), guard, helper, flow-run rehydration, observability events, retry policy, request metrics |
| `AINDY/db/` | SQLAlchemy base, engine/session factory, model registry, runtime-owned ORM models (27 tables), DAOs, schema contract, schema ops CLI, mongo setup |
| `AINDY/exception_handlers.py` | FastAPI global exception normalization (HTTP, validation, unhandled) |
| `AINDY/kernel/` | Syscall dispatcher, syscall registry, syscall versioning, circuit breaker, event bus, resource manager, scheduler engine, scheduler subpackage (core/cross-instance/dispatch/engine/persistence/recovery/waits), redis wait registry, tenant context |
| `AINDY/main.py` | FastAPI app factory; lifespan context manager; all startup/shutdown orchestration |
| `AINDY/memory/` | Memory persistence, ingest service, embedding jobs, embedding service, ingest queue, scoring service, memory address space, capture engine, native bridge bindings |
| `AINDY/middleware.py` | Request logging, trace ID, request metrics middleware |
| `AINDY/nodus/` | Nodus script runtime bindings, embedding, memory bridge |
| `AINDY/platform_layer/` | Plugin/extension host, bootstrap contract, bootstrap graph, deployment contract, deployment profile enforcement, platform loader, plugin host, sandbox runner, sandbox certification, extension ABI/boundary/capabilities/execution model/policy/provenance/worker, registry, node registry, health service, LLM clients (OpenAI, DeepSeek), cache backend, rate limiter, metrics, OTEL, scheduler service, system state service, trace context, user IDs, watcher contract |
| `AINDY/plugins/nodes/` | Plugin node handler directory (currently empty except `__init__.py`) |
| `AINDY/routes/` | All HTTP route handlers: agent, auth, coordination, db_verify, flow, health, memory, memory_metrics, memory_trace, observability, platform (flows, keys, nodes, nodus, ops, queue, schemas, webhooks), platform_router, version, watcher |
| `AINDY/runtime/` | Flow engine (entrypoints, event router, node executor, registry, runner and sub-steps), flow definitions (core, engine, extended, memory, observability), execution loop, execution registry, memory subpackage, Nodus adapter/builtins/execution/compiler/runtime adapter/schedule service/security/trace service/worker |
| `AINDY/runtime_only.py` | Runtime-only boot selector; sets `AINDY_BOOT_MODE=runtime-only` before importing `AINDY.main` |
| `AINDY/runtime_plugins.json` | Runtime-owned manifest; declares `platform-only` profile with empty plugins list |
| `AINDY/schemas/` | Pydantic schemas (auth) |
| `AINDY/sdk/` | aindy SDK client library (not bundled in tests; 0% test coverage) |
| `AINDY/services/` | `auth_service.py` — user registration, login, token operations |
| `AINDY/startup.py` | Full startup sequence (1 480+ lines); schema bootstrap, profile enforcement, plugin loading, syscall registration, flow registration, state rehydration |
| `AINDY/utils/` | Text constraints, sanitize, normalize encoding, UUID utilities |
| `AINDY/watcher/` | Session/window watcher, classifier, signal emitter, session tracker (0% test coverage) |
| `AINDY/worker/` | Worker process: worker loop, health server, memory ingest worker, metric writer worker (0% test coverage) |

### Public Surfaces (from `docs/runtime/PUBLIC_RUNTIME_SURFACES.md`)

**Stable HTTP:**
- `GET /api/version` — compatibility, boot, provenance, public contract inventory
- `GET /health` — liveness, degraded conditions, trusted-Python inventory
- `GET /ready` — readiness with infrastructure dependency checks
- `GET /platform/syscalls` — versioned syscall catalog
- `POST /platform/syscall` — versioned syscall dispatch

**Experimental HTTP:**
- `/apps/agent/*`, `/apps/memory/*`, `/apps/coordination/*`
- `/platform/flows*`, `/platform/nodes*`, `/platform/nodus*`, `/platform/webhooks*`

**Stable Syscalls:** `sys.v1.memory.read/write`, `sys.v1.flow.run`, `sys.v1.event.emit`, `sys.v1.agent.execute`

**Stable Boot Contract:** `AINDY_BOOT_MODE=runtime-only` → `boot_profile=platform-only`

**Public Import API** (from `docs/runtime/PUBLIC_API_CONTRACT.md`): 38 explicitly declared stable module targets for app-side imports; see that doc for the full list. Everything else under `AINDY.*` is internal by default.

### What aindy-runtime Does NOT Own

- Repo-root `aindy_plugins.json` (app manifest)
- `apps.bootstrap` and all `apps.*` packages
- `alembic/` and migration history
- `client/` (frontend)
- App-profile tests and docs
- App-domain ORM models (tasks, analytics, masterplan, automation, ARM, search, freelance, rippletrace, authorship, autonomy, social)

### Boundary Violations

**None detected.**

Verified by `RUNTIME_SIGNOFF.md`:
```
rg -n "^\s*(from apps\.|import apps\.|from apps\b|import apps\b)" AINDY -g "*.py"
```
Result: no matches. The runtime-ci.yml also runs this check on every push/PR as a hard-failure step.

---

## Entrypoints & Boot Modes

| Entrypoint | Form | Boot Mode | Min Env Vars | What It Enables |
|---|---|---|---|---|
| `aindy-runtime` | console script | `runtime-only` (profile: `platform-only`) | `DATABASE_URL`, `AINDY_BOOT_MODE=runtime-only`, `SECRET_KEY`, `OPENAI_API_KEY` | Runtime-only HTTP surface; no `apps.*` loaded; baseline agent/memory tools only |
| `python -m AINDY.runtime_only` | module | same as above | same | Equivalent to `aindy-runtime` |
| `uvicorn AINDY.runtime_only:app` | ASGI | same as above | same | Equivalent; for ASGI server wiring |
| `aindy-runtime-api` | console script | whichever `AINDY_BOOT_MODE` is set | `DATABASE_URL`, `SECRET_KEY`, `OPENAI_API_KEY` | Generic API entrypoint; if `AINDY_BOOT_MODE=runtime-only` → platform-only; if app manifest is present without mode override → `default-apps` (full monolith profile) |
| `uvicorn AINDY.main:app` | ASGI | same as `aindy-runtime-api` | same | Equivalent ASGI form |

**SQLite smoke mode** (local only):
```
DATABASE_URL=sqlite://  AINDY_ALLOW_SQLITE=1  AINDY_BOOT_MODE=runtime-only
SECRET_KEY=...  OPENAI_API_KEY=sk-test-placeholder
```

**Deployment profiles** (from `docs/runtime/DEPLOYMENT_PROFILES.md`):

| Profile | Process role | Execution mode | Required infrastructure |
|---|---|---|---|
| `single-instance` | API | `thread` | PostgreSQL, runtime schema; Redis optional |
| `distributed-api` | API | `distributed` | PostgreSQL, Redis, event bus, durable queue, ≥1 worker |
| `distributed-worker` | Worker | `distributed` | PostgreSQL, Redis, durable queue |

### Startup Sequence

From `docs/runtime/RUNTIME_BEHAVIOR.md` and `AINDY/startup.py`:

1. Reset runtime state, publish initial startup status
2. Resolve and validate active deployment profile; enforce `SECRET_KEY`, Redis, event-bus guards
3. Initialize cache backend (`AINDY_CACHE_BACKEND`)
4. Verify Mongo connectivity; record degraded state if unavailable and optional
5. Validate queue backend and worker expectations; fail fast in production if unsafe
6. **Schema bootstrap**: auto-create runtime tables on blank DB; enforce schema contract if `AINDY_ENFORCE_SCHEMA=true`; allow additive reconcile only if `AINDY_SCHEMA_RECONCILE=true`
7. Acquire background-task leadership; start APScheduler on leader only
8. Register syscall handlers, canonical flow nodes, and flows
9. Restore dynamic platform registrations from DB; surface incomplete restore as unsafe degraded
10. Start event-bus subscriber; record WAIT/RESUME propagation mode
11. Rehydrate waiting execution state; fail fast in production if incomplete
12. Publish startup-complete state

### Plugin Loading

The runtime reads `AINDY/runtime_plugins.json`, which resolves `platform-only` to an empty plugins list. No `apps.*` bootstrap module is loaded in runtime-only mode. When the app manifest (`aindy_plugins.json`) is present and no explicit mode is set, `apps.bootstrap` loads via `AINDY.platform_layer.registry`.

### Schema Bootstrap Implementation Status

**Fully implemented** in `AINDY/db/schema_contract.py`. The `ensure_runtime_schema()` function is called at startup with `allow_bootstrap=True` (blank DB → auto-create) and `allow_reconcile=False` by default (upgrade-required → fail closed unless `AINDY_SCHEMA_RECONCILE=true`). This is not a README stub — the code is real and used in production startup.

---

## Schema & Database Layer

### Database Backends

- **Primary:** PostgreSQL (production)
- **Optional:** SQLite (smoke/test only; requires `AINDY_ALLOW_SQLITE=1`; explicitly rejected in production unless `AINDY_ALLOW_SQLITE` is set)

### Runtime-Owned ORM Models and Tables

From `AINDY/db/schema_contract.py` and `docs/runtime/DB_OWNERSHIP_CONTRACT.md`:

| Category | Tables |
|---|---|
| Identity and platform access | `users`, `user_identities`, `api_keys` |
| Agent runtime persistence | `agents`, `agent_registry`, `agent_runs`, `agent_events` |
| Execution, waits, and scheduler | `execution_units`, `flow_runs`, `waiting_flow_runs`, `background_task_leases`, `job_logs`, `event_edges`, `nodus_scheduled_jobs`, `nodus_trace_events` |
| Memory and observability | `memory_metrics`, `memory_node_history`, `memory_traces`, `memory_trace_nodes`, `request_metrics`, `system_events`, `system_health_logs`, `system_state_snapshots` |
| Runtime dynamic platform state | `capabilities`, `dynamic_flows`, `dynamic_nodes`, `webhook_subscriptions` |

Total runtime-owned tables: 27 (enumerated from `AINDY/db/models/` + `AINDY/memory/memory_persistence.py`).

### Migration Ownership

- `aindy-runtime`: owns blank-database bootstrap and additive startup reconcile for runtime-owned tables through `AINDY/db/schema_contract.py`
- `aindy-apps-monolith`: owns the Alembic migration tree (`alembic/alembic/versions/`) for the combined deployed database
- The runtime has no dependency on `alembic.ini` or any monolith migration script

### Schema Reconciliation Flags

Both flags are **fully implemented** in `AINDY/db/schema_contract.py` and wired into `AINDY/startup.py`:

- `AINDY_ENFORCE_SCHEMA=true` — default safety gate; validates runtime-owned schema at startup; startup fails closed if schema is incompatible
- `AINDY_SCHEMA_RECONCILE=true` — explicit opt-in for additive startup reconcile (missing tables and additive-safe missing columns only); applied to both API startup and worker readiness paths

Four schema states are classified and reported: `blank_bootstrap`, `compatible`, `upgrade_required`, `incompatible_manual`. Machine-readable `schema_drift_classes`, `schema_remediation_categories`, and `schema_offline_migration_required` are exposed on `/health` and `/ready`.

### SQLAlchemy Session Isolation Issue (test_agent_api.py)

**Moved to monolith — not present in runtime test suite.** The prior known issue involved `testing_session_factory` vs `db_session` transaction visibility in `test_agent_api.py`. That file does not exist in `aindy-runtime/tests/`. The runtime test suite uses its own `tests/fixtures/db.py` fixture infrastructure, which has not exhibited this problem in any current test run.

---

## Test Suite State

### Collection

**221 tests collected** across 23 files; no collection errors.

| File | Count |
|---|---|
| `tests/api/test_platform_syscall_contract.py` | 1 |
| `tests/api/test_version_api.py` | 2 |
| `tests/unit/test_agent_planning_contract.py` | 8 |
| `tests/unit/test_agent_runtime_guardrails.py` | 8 |
| `tests/unit/test_deployment_profiles.py` | 21 |
| `tests/unit/test_extension_abi.py` | 12 |
| `tests/unit/test_extension_boundary_contract.py` | 10 |
| `tests/unit/test_extension_hardening.py` | 25 |
| `tests/unit/test_extension_ownership.py` | 11 |
| `tests/unit/test_extension_provenance.py` | 6 |
| `tests/unit/test_platform_only_startup.py` | 10 |
| `tests/unit/test_plugin_host.py` | 10 |
| `tests/unit/test_plugin_sandbox_certification.py` | 10 |
| `tests/unit/test_route_execution_guard.py` | 11 |
| `tests/unit/test_runtime_boundary.py` | 1 |
| `tests/unit/test_runtime_compatibility_metadata.py` | 1 |
| `tests/unit/test_runtime_degraded_modes.py` | 10 |
| `tests/unit/test_runtime_only_test_fixtures.py` | 3 |
| `tests/unit/test_runtime_packaging.py` | 3 |
| `tests/unit/test_runtime_public_contract.py` | 13 |
| `tests/unit/test_runtime_schema_contract.py` | 15 |
| `tests/unit/test_sandbox_runner.py` | 17 |
| `tests/unit/test_syscall_contract.py` | 13 |

### Documented CI Verification Suite (`-m runtime_only`)

```
python -m pytest \
  tests/unit/test_runtime_only_test_fixtures.py \
  tests/unit/test_platform_only_startup.py \
  tests/unit/test_runtime_packaging.py \
  tests/unit/test_runtime_boundary.py \
  tests/unit/test_runtime_compatibility_metadata.py \
  tests/api/test_version_api.py \
  -m runtime_only -q
```

**Result: 19 passed, 1 skipped, 2 warnings** (20 collected)

Note: the `RUNTIME_SIGNOFF.md` baseline (2026-05-17) recorded 17 passed / 0 skipped. The current run shows 19 passed / 1 skipped, indicating 2 additional tests were added since the extraction signoff.

### Full Suite

**Result: 220 passed, 1 skipped, 10 warnings** (221 collected) — 98.63s

No failures. No collection errors.

### Coverage

**Overall: 38%** (`pytest --cov=AINDY --cov-report=term-missing`)

Modules below 60% (selected):

| Module | Coverage | Notes |
|---|---|---|
| `AINDY/sdk/aindy_sdk/*` | 0% | Entire SDK untested |
| `AINDY/worker/worker_loop.py` | 0% | Worker process untested |
| `AINDY/worker/health_server.py` | 0% | Worker health server untested |
| `AINDY/watcher/*` | 0% | All watcher components untested |
| `AINDY/runtime/nodus_worker.py` | 0% | Nodus worker untested |
| `AINDY/nodus/runtime/*` | 0% | Nodus runtime bindings untested |
| `AINDY/core/execution_pipeline/*` (most files) | 0–26% | Execution pipeline lightly tested |
| `AINDY/startup.py` | 53% | Startup sequence partially covered |
| `AINDY/services/auth_service.py` | 25% | Auth service lightly tested |
| `AINDY/utils/text_constraints.py` | 14% | Utility lightly tested |

Well-covered modules (≥80%): `AINDY/config.py`, `AINDY/schemas/`, `AINDY/db/schema_contract.py` (effectively), `AINDY/_version.py`

### Smoke Check (README Validated Split Check)

**Failed locally** — `prometheus_client` not installed in the local development environment. The test `conftest.py` stubs `prometheus_client` for pytest, but the raw smoke command (`python -c "..."`) runs outside pytest and hits a real `ModuleNotFoundError: No module named 'prometheus_client'` when importing `AINDY.main`. In CI, `AINDY/requirements.txt` installs `prometheus-fastapi-instrumentator>=6.1.0`, which brings `prometheus_client` as a transitive dep. This is a local dev environment gap, not a CI or code defect.

The `RUNTIME_SIGNOFF.md` (2026-05-17) documents the smoke check passing with:
- `boot_profile: platform-only`
- `app_plugins_loaded: False`
- `app_plugin_count: 0`
- `runtime_package.name: aindy-runtime`

### Test Categories

| Category | Files | Status |
|---|---|---|
| `runtime_only` marker (documented CI subset) | 6 files | Healthy — 19 passed |
| Unit — extension/plugin/sandbox | 9 files | Healthy — all pass |
| Unit — deployment/schema/syscall/agent | 7 files | Healthy — all pass |
| API contract | 2 files | Healthy — 3 passed |
| Integration (postgres service matrix) | None | **Missing** — intentional per CI_OWNERSHIP.md |
| Worker/watcher/SDK | None | **Missing** — 0% coverage on these subsystems |

---

## CI/CD State

### `runtime-ci.yml` — Automatic push/PR check

| Documented Step | Implemented? | Notes |
|---|---|---|
| Ruff lint on `AINDY/` and `tests/` | **Yes** | Separate `lint` job; uses `AINDY/ruff.toml` config; runs `ruff check AINDY tests` |
| Runtime doc frontmatter validation (`docs/runtime/`) | **Yes** | Separate `runtime-docs` job; validates `title`, `last_verified`, `api_version`, `status`, `owner` keys |
| Editable install with test extras | **Yes** | `pip install -r AINDY/requirements.txt` then `pip install -e .[test] --no-deps` |
| Import boundary assertion (`apps.*`) | **Yes** | `grep -R -n -E` for `from apps.|import apps.` in `AINDY/`; hard-fail if found |
| Console script verification | **Yes** | Python inline check against `importlib.metadata.entry_points` for both `aindy-runtime` and `aindy-runtime-api` |
| Health and version smoke (`GET /health`, `GET /api/version`) | **Yes** | Python inline using `TestClient`; asserts `boot_mode`, `boot_profile`, `app_plugins_loaded`, `app_plugin_count`, `runtime_package.name`, `recommended_runtime_requirement` |
| Full `runtime_only` pytest suite | **Yes** | `pytest tests -m runtime_only -q` |
| Wheel and sdist build + `twine check` | **Yes** | Separate `package-build` job; `python -m build` + `twine check dist/*` |

**All 8 documented CI steps are implemented.** The `runtime-contracts` job depends on `[lint, runtime-docs]` before running. `package-build` depends on `[lint]` only.

### `release-staging.yml` — Manual only (`workflow_dispatch`)

**Confirmed manual-only.** Steps: install release extras → verify staged version metadata → build sdist and wheel → `twine check` → upload artifacts. Does not publish to PyPI.

### CI Gaps and Flags

| Issue | Impact |
|---|---|
| **Action versions not pinned to SHAs** | All 5 action references use floating tags (`@v4`, `@v5`, `@v4`) — not SHA-pinned. Supply chain risk. Affected: `actions/checkout@v4`, `actions/setup-python@v5`, `actions/cache@v4`, `actions/upload-artifact@v4` | 
| **No Redis/PostgreSQL/Mongo service matrix** | Intentional per `docs/runtime/CI_OWNERSHIP.md` ("Remaining gaps are intentional or still deferred") — but means no integration-tier coverage in CI |
| **No runtime-owned Docker image build** | Also intentional/deferred per `CI_OWNERSHIP.md` |
| **`runtime-only` smoke uses `raise_server_exceptions=False`** | Means server-side errors return 200-shaped payloads without failing the build; the assertions on payload keys partially compensate, but assertion gaps could hide regressions |

---

## Dependency & Configuration Hygiene

### Runtime Dependencies (from `pyproject.toml`)

All runtime dependencies are **exactly pinned** (e.g. `fastapi==0.119.0`, `SQLAlchemy==2.0.44`, `pydantic==2.12.3`) with the exception of four loosely-pinned packages:

| Package | Declared | Pin Status | Notes |
|---|---|---|---|
| `opentelemetry-api` | `>=1.20.0` | Loose lower-bound | Critical observability dep; no upper bound |
| `opentelemetry-sdk` | `>=1.20.0` | Loose lower-bound | Same concern |
| `opentelemetry-instrumentation-fastapi` | `>=0.41b0` | Loose beta lower-bound | Beta range with no upper bound |
| `opentelemetry-exporter-otlp-proto-grpc` | `>=1.20.0` | Loose lower-bound | Same concern |
| `prometheus-fastapi-instrumentator` | `>=6.1.0` | Loose lower-bound | No upper bound |
| `python-json-logger` | `>=2.0.7` | Loose lower-bound | Minor concern |

All other 50+ direct deps are exactly pinned.

### Test / Dev Extras (from `pyproject.toml`)

| Package | Version |
|---|---|
| `pytest` | `==9.0.2` |
| `pytest-asyncio` | `==1.3.0` |
| `pytest-cov` | `==7.0.0` |
| `pytest-mock` | `==3.15.1` |

### Release Extras

| Package | Version |
|---|---|
| `build` | `==1.3.0` |
| `twine` | `==6.2.0` |

### Environment Variables

**Required for any boot:**

| Variable | Notes |
|---|---|
| `DATABASE_URL` | PostgreSQL URL (production); SQLite `sqlite:///:memory:` for tests only |
| `SECRET_KEY` | JWT signing key |
| `OPENAI_API_KEY` | Required for LLM calls; `sk-test-placeholder` accepted in tests |
| `DEEPSEEK_API_KEY` | Required for DeepSeek model calls |
| `AINDY_API_KEY` | Platform API key for internal service-to-service auth |
| `PERMISSION_SECRET` | Permission signing secret |
| `ALLOWED_ORIGINS` | CORS origin allowlist |

**Boot mode control:**

| Variable | Default | Notes |
|---|---|---|
| `AINDY_BOOT_MODE` | unset (app-profile if manifest present) | `runtime-only` forces platform-only profile |
| `AINDY_DEPLOYMENT_PROFILE` | inferred from `EXECUTION_MODE` | `single-instance`, `distributed-api`, `distributed-worker` |
| `EXECUTION_MODE` | `thread` | `thread` or `distributed` |

**Schema control:**

| Variable | Default | Notes |
|---|---|---|
| `AINDY_ENFORCE_SCHEMA` | `true` (implied; `false` in test env) | Validates runtime schema at startup |
| `AINDY_SCHEMA_RECONCILE` | `false` | Opt-in for additive startup reconcile |
| `AINDY_ALLOW_SQLITE` | unset | Must be `1`/`true` to allow SQLite; rejected in production |

**Optional infrastructure:**

| Variable | Notes |
|---|---|
| `MONGO_URL` | MongoDB connection string; empty means Mongo-backed features are degraded |
| `MONGO_REQUIRED` | `true` makes Mongo unavailability fatal |
| `REDIS_URL` | Required for `distributed-api` and `distributed-worker` profiles |
| `AINDY_SKIP_MONGO_PING` | `1` in tests to skip Mongo connectivity check |
| `AINDY_CACHE_BACKEND` | `memory` or Redis-backed; `memory` rejected for `distributed-api` |
| `AINDY_EVENT_BUS_ENABLED` | Required `true` for `distributed-api` |

**`.env.example` or equivalent:** **Absent.** Not present in the repo root or any subdirectory. This is an onboarding gap — new operators must derive required variables from the README and `conftest.py`.

### Hardcoded Values Flagged

- `AINDY/conftest.py` test stub for `prometheus_client` uses hardcoded class names — minor, test-only
- `AINDY/db/schema_contract.py` has `SCHEMA_CONTRACT_VERSION = "2026-05-20"` — should be bumped with schema changes; there is no automated enforcement
- `AINDY/ruff.toml` ignores `F401`, `F403`, `F821`, `F841` — broad suppression of import and undefined-name rules; reduces lint signal on real issues

---

## AISO & Knowledge Infrastructure

### Current State

| Artifact | Status |
|---|---|
| `llms.txt` | **Absent** — not present at repo root or in `docs/` |
| `robots.txt` | **Absent** |
| `sitemap.xml` | **Absent** |
| JSON-LD schema markup | **Absent** — no structured data in any doc |
| Canonical A.I.N.D.Y. definition | **Absent from docs** — no README section defining the project name, acronym, or system purpose |
| Creator attribution (Shawn Knight) | **Absent** — `pyproject.toml` authors field is `platform-team`; no mention of Shawn Knight or Masterplanner25 in any doc or config file |
| Ecosystem context | **Absent** — no reference to Infinity Algorithm or Masterplan Infinite Weave in runtime docs |

### AISO Gap Assessment

The runtime repo has no AISO-optimized artifacts. An AI crawler encountering this repo would find:
- A package named `aindy-runtime` with description "Installable AINDY runtime infrastructure package for trusted internal deployments"
- GitHub URLs pointing to `github.com/Masterplanner25/aindy-runtime`
- No canonical definition of "AINDY", no creator name, no ecosystem context

This means attributional indexing is likely to be weak or incorrect. The minimum viable AISO baseline would be: a `llms.txt` at the repo root, creator and ecosystem attribution in `pyproject.toml` and `README.md`, and a canonical project definition paragraph.

---

## Resolved Since Last Known State (April 2026)

| Prior Known Issue | Status |
|---|---|
| **SQLAlchemy session isolation (test_agent_api.py)** — `testing_session_factory` vs `db_session` transaction visibility | **Not applicable — moved to monolith.** `test_agent_api.py` does not exist in the runtime test suite. The runtime uses `tests/fixtures/db.py` with its own isolation model. No session isolation failures observed in current test runs. |
| **Alembic ModuleNotFoundError in CI** | **Resolved — not applicable to runtime repo.** The runtime has no Alembic dependency. Schema lifecycle is self-contained in `AINDY/db/schema_contract.py`. The monolith retains the Alembic tree. |
| **pytest pythonpath misconfiguration** | **Resolved.** `pytest.ini` correctly sets `pythonpath = . AINDY` and `testpaths = tests`. All 221 tests collect and run without path errors. |
| **Ruff lint errors in test files (test_syscall_handlers.py)** | **Not applicable — file absent.** `test_syscall_handlers.py` does not exist in the runtime test suite. Its replacement is `tests/unit/test_syscall_contract.py` (13 tests, all passing). The ruff config in `AINDY/ruff.toml` broadly suppresses several error classes (`F401`, `F403`, `F821`, `F841`). |
| **async_execution_context missing module** | **Present — module exists.** `AINDY/platform_layer/async_execution_context.py` is in the file tree. However, it has **0% test coverage** and is not part of any currently running test. Whether the original import error has been resolved cannot be confirmed by test execution alone. |

---

## Top 10 Open Items

Ranked by operational impact:

1. **Smoke check fails in local dev due to missing `prometheus_client`** — `prometheus-fastapi-instrumentator` is in `AINDY/requirements.txt` but is not installed as a transitive dep when doing a bare `pip install -e .[test]`. The README smoke command fails with `ModuleNotFoundError` outside of pytest. This blocks any developer trying to manually validate a runtime boot from the README instructions.

2. **Overall test coverage at 38%** — The worker process (`AINDY/worker/`), watcher subsystem (`AINDY/watcher/`), SDK (`AINDY/sdk/`), and Nodus runtime (`AINDY/nodus/runtime/`) all have 0% coverage. These are non-trivial production subsystems with no runtime contract tests at all.

3. **No `.env.example`** — Operators must read `README.md`, `pytest.ini`, and `tests/conftest.py` in parallel to assemble the required environment. There is no single reference file showing the minimum required vars with safe placeholder values.

4. **GitHub Action versions not pinned to SHAs** — All five action references in `runtime-ci.yml` and `release-staging.yml` use floating major-version tags (`@v4`, `@v5`). A tag re-point on any of these actions would silently change CI behavior. SLSA / supply chain guidance recommends SHA pinning.

5. **No AISO artifacts and no creator attribution** — `llms.txt`, `robots.txt`, `sitemap.xml`, and JSON-LD are all absent. The project name acronym (A.I.N.D.Y.) has no canonical definition in any doc. Shawn Knight / Masterplanner25 / Infinity Algorithm / Masterplan Infinite Weave appear nowhere in the runtime repo metadata, README, or `pyproject.toml`. AI crawlers cannot correctly attribute this project.

6. **Five OpenTelemetry + prometheus deps are loosely pinned (`>=`)** — In a fully-pinned dependency set, these six packages stand out. A minor release of `opentelemetry-sdk` or `prometheus-fastapi-instrumentator` could break the runtime without CI catching it, since CI installs from the floating lower-bound range.

7. **`async_execution_context` has 0% test coverage and unconfirmed import status** — The module exists at `AINDY/platform_layer/async_execution_context.py` but is not exercised by any current test. The prior known issue (missing module) may have been fixed by creating the file, but no test validates its API contract.

8. **`SCHEMA_CONTRACT_VERSION = "2026-05-20"` has no automated bump enforcement** — The schema contract version is a hardcoded string that operators use to validate schema freshness in `/health` and `/ready` payloads. There is no CI step that detects when an ORM model changes without bumping this version.

9. **No Redis/PostgreSQL/Mongo integration test tier in CI** — The `CI_OWNERSHIP.md` explicitly calls this out as an intentional deferred gap. All current tests run against SQLite in-memory. Behaviors that differ between SQLite and PostgreSQL (FK cascades, JSONB, UUID, advisory locks, row-level locking) are not validated on the target backend in any automated CI run.

10. **SDK (`AINDY/sdk/`) is in the runtime package but has no tests and unclear release contract** — The SDK is included under `AINDY/sdk/` and is packaged with the runtime (via `setuptools.packages.find include = ["AINDY*"]`). It has 0% test coverage, its own `pyproject.toml` inside the runtime tree, and no declared relationship to the runtime release staging process. It is either an accidental inclusion or an unstated work-in-progress surface.
