# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Commands

```bash
# Install (editable + test deps)
pip install -e ".[test]"

# Unit tests — no external services, SQLite in-memory
pytest tests/unit/ -v
pytest tests/unit/test_syscall_contract.py::test_name -v   # single test

# Runtime-only CI subset (fastest, no DB required)
pytest -m runtime_only -q

# Sandbox escape suite — requires Docker, Linux containers mode, NO database needed
pytest -m sandbox_escape -v
SANDBOX_ESCAPE_IMAGE=python:3.12-alpine pytest -m sandbox_escape -v  # custom image

# Integration tests — require live Postgres + Redis
pytest -c pytest.integration.ini -v
docker compose -f docker-compose.test.yml up -d            # spin up deps

# Lint (runs from repo root; config in AINDY/ruff.toml)
ruff check AINDY/
ruff format AINDY/

# Regenerate schema baseline after any model change
python scripts/check_schema_version.py

# Run the server locally (requires DATABASE_URL + SECRET_KEY in env)
aindy-runtime serve
uvicorn AINDY.runtime_only:app   # equivalent ASGI form

# Docker compose — full stack
docker compose up -d                                        # api + postgres + redis + mongo
docker compose --profile full up -d                        # + worker
docker compose --profile full --profile monitoring up -d   # + Prometheus
NGINX_CONF=nginx.tls.conf \
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile full --profile proxy up -d                     # + nginx TLS, all internal ports closed
```

---

## Architecture

### Layer model

```
AINDY/
├── kernel/           # Core primitives: SyscallDispatcher, SyscallRegistry, EventBus,
│                     #   SchedulerEngine, CircuitBreaker, ResourceManager, TenantContext
├── platform_layer/   # Runtime services: LLM clients (OpenAI/DeepSeek), metrics, OTel,
│                     #   rate limiter, cache backend, extension ABI + sandbox runner,
│                     #   scheduler_service (APScheduler maintenance jobs)
├── core/             # Execution pipeline middleware, RetryPolicy, DistributedQueue,
│                     #   SystemEventService, RequestMetricWriter, ResumeWatchdog
├── runtime/          # Flow engine (DAG executor), Nodus script execution,
│                     #   memory loop, flow definitions
├── agents/           # Agent runtime, planner backends, tool registry,
│                     #   AgentCoordinator, AutonomousController
├── memory/           # MemoryNode persistence, MemoryAddressSpace (MAS), embedding
│                     #   pipeline, scoring, memory traces
├── db/               # SQLAlchemy models, Alembic env, DAO layer, schema contract
├── routes/           # FastAPI routers — auth, flows, agents, memory, platform/*
├── worker/           # Background worker processes (memory ingestion, metric writing)
└── nodus/            # Nodus language stdlib (memory.nd) and runtime adapter
```

### Request → execution pipeline

Every route handler runs inside `ExecutionPipeline` (`core/execution_pipeline/pipeline.py`), a middleware-like wrapper that: sets ContextVars (trace_id, pipeline_active), claims/releases an `ExecutionUnit` in the DB, records Prometheus metrics, captures memory signals from the response, and emits `SystemEvent` records. Handlers interact with the kernel only via `SyscallDispatcher.dispatch()`.

### Syscall system (`sys.v1.domain.action`)

`SyscallDispatcher` (`kernel/syscall_dispatcher.py`) is the single entry point for all capability calls. Every dispatch: validates syscall exists in `SYSCALL_REGISTRY`, enforces the caller's `SyscallContext.capabilities`, checks tenant isolation and resource quota, validates input/output schemas, runs the idempotency gate for `EXACTLY_ONCE` handlers (EffectRecord in DB), wraps the handler in an OTel span, and returns a uniform `{status, data, trace_id, duration_ms, error}` envelope.

### Flow execution and WAIT/RESUME

`FlowRun` rows move through a state machine (`pending → executing → waiting → completed/failed`). The `SchedulerEngine` (`kernel/scheduler/`) runs a priority queue (high/normal/low lanes). When a node calls `sys.v1.event.wait`, the flow suspends: `FlowRun.status` → `waiting`, a callback is registered in the in-memory `_waiting` dict, and `EventBus.publish()` broadcasts the wait via Redis pub/sub to all instances. When `publish_event(event_type)` fires later, the engine re-enqueues the matching flow. On restart, `flow_run_rehydration.py` re-registers all `waiting` rows so no flow is lost.

### Nodus script execution

`nodus_worker.py` (`runtime/`) compiles and runs `.nodus` / `.nd` scripts via `nodus-lang`. It injects `DeferredMemoryBuiltins` (recall/search/write backed by the `memory_context` dict from the flow) and a `WorkerWaitSignal` exception that propagates WAIT semantics back to the flow engine. Memory writes are deferred — collected as a list and committed after the script finishes.

### Agent execution

`execute_run()` (`agents/agent_runtime/execution.py`) is the entry point. It checks `AgentRun.status == "approved"`, validates the scoped capability token, resolves tools via `tool_registry.py`, optionally routes through `AgentCoordinator` for multi-agent delegation, then calls `execute_agent_run_via_nodus()` which compiles the agent objective into a Nodus execution context and runs it through the flow-backed execution path.

### Memory system

Memory nodes live in `memory_nodes` (PostgreSQL, `Vector(1536)` embedding column via pgvector). Writes go through `memory_ingest_service.py` → background embedding queue → `memory_ingest_worker.py` → OpenAI text-embedding API → pgvector upsert. Retrieval is hybrid: vector similarity + tag filter + `MemoryAddressSpace` path queries (`/memory/{tenant}/{namespace}/{type}/{id}`). Scoring uses impact score, usage count, and causal depth. `memory_scoring_service.py` ranks results; a Rust native scorer (`memory/native/`) is an optional performance path compiled via Maturin.

---

## Schema contract version protocol

Any change to a file under `AINDY/db/models/` or `AINDY/memory/memory_persistence.py`
requires three follow-up steps — in this order — or CI fails:

1. Bump `SCHEMA_CONTRACT_VERSION` in `AINDY/db/schema_contract.py`.
   - Use `"YYYY-MM-DD"` for the first change on a given date.
   - Use `"YYYY-MM-DD.1"`, `"YYYY-MM-DD.2"`, … for subsequent changes on the same date.
2. Regenerate the baseline: `python scripts/check_schema_version.py`
   - Exit 0 with "Schema version baseline updated." confirms success.
3. Update the two hardcoded version-string assertions in
   `tests/unit/test_runtime_schema_contract.py` (grep `schema_contract_version`).

---

## Alembic migration conventions

- All migrations use `IF NOT EXISTS` / `IF EXISTS` guards — every migration must be
  idempotent when run against a schema already at that revision.
- The runtime uses `alembic_version_runtime` (not the monolith's `alembic_version`).
- Migration naming: `NNNN_short_description.py`, e.g. `0004_effect_records_completed_at_index.py`.
- `downgrade()` must drop what `upgrade()` created. For index-only migrations, `DROP INDEX IF EXISTS` is sufficient.
- Current chain: `0001` → `0002` → `0003` → `0004`.

**Blank-database safety (ALEMBIC-FRESH-DB-1):** In Docker compose deployments, `alembic
upgrade head` runs before the server starts, so before `_enforce_schema_guard` / `create_all`
creates any tables. Any migration that touches a specific table in DML (`UPDATE`, `DELETE`) or
DDL (`CREATE TABLE`, `CREATE INDEX ON`) must wrap that statement in a table-existence guard:

```sql
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_tables
    WHERE tablename='my_table' AND schemaname='public'
  ) THEN
    -- DML or DDL here
  END IF;
END $$
```

On a blank database the block skips; the server's Phase 5 `_enforce_schema_guard` then
bootstraps the full schema from ORM metadata via `create_all`. On an existing deployment the
block runs normally. `IF NOT EXISTS` on the index name alone is NOT sufficient — if the table
doesn't exist, `CREATE INDEX ... ON missing_table` still raises `UndefinedTable`.

---

## Scheduler job pattern (`scheduler_service.py`)

Reference implementation: `_cleanup_stale_logs` and `_cleanup_expired_effect_records`.

**Function signature:**
```python
def _my_job() -> None:
    """One-line description."""
    try:
        from AINDY.db.database import SessionLocal
        # other imports inside try block
        db = SessionLocal()
        # ... work ...
        db.commit()
        db.close()
    except Exception as exc:
        logger.error("[my_job] failed: %s", exc)
```

- All imports go inside the `try` block.
- `SessionLocal()` opened inside `try`, never at module level.
- `db.commit()` and `db.close()` inside `try` (before the except).
- Use `logger.error` for fatal job failures; `logger.warning` for recoverable/non-fatal issues.

**Registration in `_register_system_jobs`:**
```python
scheduler.add_job(
    _my_job,
    trigger=IntervalTrigger(hours=MY_JOB_INTERVAL_HOURS),
    id="my_job",
    name="Human-readable name",
    replace_existing=True,
    coalesce=True,
    max_instances=1,
)
```

**Unit test patching:**
Jobs import `SessionLocal` inside the function body, so patch at the source:
```python
with patch("AINDY.db.database.SessionLocal", return_value=mock_db):
    _my_job()
```
Patching `AINDY.platform_layer.scheduler_service.SessionLocal` will fail with `AttributeError`.

---

## EffectRecord rules

- `_resolve_effect_record` and `_complete_effect_record` use `db.commit()`, not `db.flush()`.
  This is load-bearing: EffectRecord state must be durable across session close.
- Pending rows are never eligible for deletion — the TTL cleanup job hard-excludes them.
- Status values: `"pending"` | `"success"` | `"failed"`.

---

## Agent approve path — invariants and known gaps

`approve_run()` (`AINDY/agents/agent_runtime/approvals.py`) guards the
`pending_approval → approved` transition with an atomic SQLAlchemy CAS:

```python
rows = db.execute(
    sqla_update(AgentRun)
    .where(AgentRun.id == run_id, AgentRun.status == "pending_approval")
    .values(status="approved", ...)
    .execution_options(synchronize_session=False)
).rowcount
if rows == 0:
    db.expire(run); db.refresh(run)
    return compat._run_to_dict(run)  # already-approved or terminal — no re-execute
```

**CAS fires only from `pending_approval`.** A process crash mid-execution leaves the run
stranded in `approved` with no retry path — the watchdog/reaper in AGENT-APPROVE-001b must
recover orphaned `approved` states. Do not add a second CAS guard here; fix belongs in 001b.

**The approve path bypasses `SyscallDispatcher` entirely.** No EffectRecord idempotency
gate is available for approve. Do not assume syscall-level idempotency applies here.

**Unit test patching:** `execute_run` is re-exported via `AINDY/agents/agent_runtime/__init__.py`,
so patch at:
```python
patch("AINDY.agents.agent_runtime.execute_run", ...)          # correct
# NOT: patch("AINDY.agents.agent_runtime.execution.execute_run", ...)
```
`mint_token` and `record_agent_event` are imported directly in `approvals.py`:
```python
patch("AINDY.agents.agent_runtime.approvals.mint_token", ...)
patch("AINDY.agents.agent_runtime.approvals.record_agent_event", ...)
```

---

## Platform UI — SPA routing invariants

The platform SPA is served by `_SPAStaticFiles` (a `StaticFiles` subclass) mounted at
`/platform` in `AINDY/routing.py`.

**Asset 404 discrimination:** `_SPAStaticFiles.get_response()` falls back to `index.html`
only when the path does NOT start with `assets/`. Vite emits all static files under
`assets/`; a 404 there is a real missing file, not a client-side route. Do not change
this to an unconditional fallback — `/platform/assets/does-not-exist.js` must return
404, not 200+HTML.

**PlatformGuard invariants (platform/src/PlatformApp.tsx):**
- `/login` must remain outside the `PlatformGuard` layout route. The guard renders
  `<Navigate to="/login" replace />` (React Router — respects `basename="/platform"`,
  no `window.location`). Do not reintroduce `window.location.href` or `redirectToApp`.
- The authenticated-but-not-admin branch must render a terminal component (`<NotAdmin />`),
  NOT navigate. Any `<Navigate>` here causes a redirect loop through the guard.
- `VITE_APP_BASE_URL` is removed and must not be reintroduced as a load-bearing redirect
  target. It may be documented as a future federation hook but must not drive any navigation.

**`VITE_API_BASE_URL`** is the build-time base for all API calls (defaults to `""`
— empty string, resolved at runtime as a relative URL against the current origin).
It is distinct from the removed `VITE_APP_BASE_URL`. PLATFORM-UI-ENV-1 is closed
(2026-06-05): the `"http://localhost:8000"` hardcoded fallback was replaced with `""`
in `@aindy/ui-kit` `src/api/_core.js`; `platform/vite.config.ts` now includes
`server.proxy` entries for local dev so `VITE_API_BASE_URL` is not required. Set it
explicitly only when deploying the API at a non-standard origin.

---

## Platform UI — build chain

`@aindy/ui-kit` is published to npm (`registry.npmjs.org`) as `@aindy/ui-kit@1.0.2`.
The local source lives at `C:\dev\aindy-ui-kit\src\`. The installed package in
`platform/node_modules/@aindy/ui-kit/` is a compiled bundle only (`dist/index.js`,
`dist/index.cjs`) — no source files. Editing the source repo has no effect on the
running bundle until you rebuild and replace it.

**Docker build is self-contained.** The Dockerfile `ui-builder` stage runs `npm ci`
and `npm run build` from the registry-pinned `@aindy/ui-kit`. A fresh
`docker compose build --no-cache` from a clean clone requires no prior local UI build.

**Local dev loop when ui-kit source changes:**
1. Edit source in `C:\dev\aindy-ui-kit\src\`
2. `npm run build` in `C:\dev\aindy-ui-kit` → regenerates `dist/`
3. Copy new dist into `platform/node_modules/@aindy/ui-kit/dist/`:
   ```powershell
   Copy-Item -Path C:\dev\aindy-ui-kit\dist\* `
     -Destination C:\dev\aindy-runtime\platform\node_modules\@aindy\ui-kit\dist\ `
     -Recurse -Force
   ```
4. `npm run build` in `platform/` → writes new bundles to `AINDY/platform/dist/`
5. Restart the `api` container: `docker compose restart api`

**`bootIdentity` unwrap invariant:** `AuthContext` calls `bootIdentity` on page load
to populate `system.runtime.boot_mode` (used by `PlatformHomeRedirect` to choose
`/agent` vs `/flows`). `bootIdentity` must call `.then(unwrapEnvelope)` — if it
returns the raw envelope `{ data: {...} }`, `useSystem()` cannot read `boot_mode`
and the post-login redirect silently misfires. Same applies to `loginUser` and
`registerUser` — all three must unwrap. Source: `C:\dev\aindy-ui-kit\src\api\auth.js`.

---

## Admin bootstrap — grant-only constraint

`AINDY_BOOTSTRAP_ADMIN_EMAIL` and `aindy-runtime auth promote-admin <email>` are both
grant-only operations. They set `is_admin=True`; they never set `is_admin=False`. Unsetting
the env var must not revoke admin from any user.

**First-registered-user-gets-admin is explicitly forbidden.** `POST /auth/register` is
public and unauthenticated. On any non-localhost deployment the first caller wins, which
is a privilege-escalation race. Do not implement this under any framing — not as a
"convenience default," not as "only if no admin exists."

The correct operator flow is: register via `POST /auth/register` → promote via env var
(requires restart) or `aindy-runtime auth promote-admin <email>` (no restart needed).

`_bootstrap_admin_email()` in `startup.py` runs as Phase 5.5 (after schema guard, before
dev key bootstrap). It opens a DB session inside a try/finally, is idempotent (logs
`already admin, no-op` on subsequent boots), and logs an INFO message when the email is
set but no matching user exists yet.

---

## `AINDY.routes` namespace shadow — import hazard for tests

`AINDY/routes/__init__.py` re-exports sub-router objects under the same names as the submodules:

```python
from AINDY.routes.health_router import router as health_router  # APIRouter object
```

This means **`from AINDY.routes import health_router` returns the `APIRouter` object, not the module**. Any attribute access like `health_router._check_syscall_registry_status()` raises `AttributeError: 'APIRouter' object has no attribute '_check_syscall_registry_status'`. The same failure happens with `import AINDY.routes.health_router as _hr` because Python resolves the package attribute first.

**Workaround for tests that need module-level functions:**

```python
# Option A — direct function import (cleanest):
from AINDY.routes.health_router import _check_syscall_registry_status

# Option B — sys.modules bypass (needed if module isn't yet imported):
import sys, importlib
_hr = sys.modules.get("AINDY.routes.health_router") or importlib.import_module("AINDY.routes.health_router")
result = _hr._check_syscall_registry_status()
```

The same shadow exists for every router exported from `AINDY/routes/__init__.py`
(`observability_router`, `flow_router`, etc.).

---

## PLATFORM_ROUTERS prefix structure

`PLATFORM_ROUTERS` in `AINDY/routes/__init__.py` contains child routers with bare prefixes
(`/flows`, `/observability`, `/db`). In `AINDY/routing.py` they are registered as:

```python
app.include_router(route, prefix="/platform", ...)
```

So the effective HTTP paths are `/platform/flows`, `/platform/observability`, `/platform/db`.

`platform_router` (prefix `/platform` already baked in) is registered separately and carries
direct routes like `GET /platform/syscalls`. **Do not look for `/platform/syscalls` in
`PLATFORM_ROUTERS` — it lives on `platform_router.routes`.**

---

## SyscallContractViolation guard

`SyscallDispatcher.dispatch()` has a broad `except Exception` handler (belt-and-suspenders
error envelope). Any exception type that must propagate out of `dispatch()` needs an
explicit guard placed **before** the broad handler:

```python
except SyscallContractViolation:
    raise
except Exception as exc:  # belt-and-suspenders
    ...
```

Add the same pattern for any future exception type that callers are expected to catch.

---

## `docs/runtime/` — required YAML frontmatter

Every `*.md` file under `docs/runtime/` must start with a YAML frontmatter block containing all five required keys or CI fails (`Runtime Docs Validation` job):

```markdown
---
title: "Document Title"
api_version: "1.0"
last_verified: "YYYY-MM-DD"
status: current
owner: "platform-team"
---
```

**Missing any key → `Runtime Docs Validation` exits 1 and blocks merge.** This bit us when `SANDBOX_ESCAPE_AUDIT.md` was created without `api_version`/`last_verified` and `MACOS_CONTAINER_POLICY.md` had no frontmatter at all. Always add all five keys when creating a new doc in this directory.

---

## Branch protection — `main`

`main` is protected. Direct pushes by anyone (including admin) are blocked — `enforce_admins: true`.

**Required status checks (must pass before merge):**
- `Runtime Lint` — ruff check
- `Runtime Docs Validation` — frontmatter check on `docs/runtime/`
- `Runtime Contracts` — unit tests, schema contract, smoke

**Full CI pipeline** (required before version tag — see `docs/runtime/RELEASE_CHECKLIST.md`):
- `Integration Tests (PostgreSQL + Redis)`
- `Platform UI Build`
- `Runtime Package Build`
- `Install Smoke Test`

---

## `pytest.mark.integration` — skip hazard for Docker-only tests

`pytest.mark.integration` triggers a global conftest guard (`tests/conftest.py`) that **skips the entire test when `DATABASE_URL` is not a live PostgreSQL URL**. This fires even in the default dev environment where `DATABASE_URL=sqlite:///:memory:`.

Tests that only need Docker (not a database) — such as the sandbox escape suite — **must NOT carry `pytest.mark.integration`**. Using that marker on Docker-only tests causes them to be silently skipped in the standard dev environment with no obvious error message.

**Rule:** Sandbox / Docker-only tests use `pytest.mark.sandbox_escape` exclusively. Never add `pytest.mark.integration` to any test that doesn't actually open a database connection.

```python
# CORRECT — Docker-only test
pytestmark = pytest.mark.sandbox_escape

# WRONG — silently skips when DATABASE_URL=sqlite://
pytestmark = [pytest.mark.sandbox_escape, pytest.mark.integration]
```

This bit us during C3 Phase 0: all 17 escape tests were silently skipped on first run because the files initially carried both markers.

---

## TECH_DEBT.md — IDEM-* numbering

Entries are numbered sequentially. Do not reuse a number.

- IDEM-1 through IDEM-6: open or closed idempotency audit findings.
- **IDEM-7**: Syscall registry not-ready window visibility — closed 2026-06-04. Added `SYSCALL_REGISTRY_MIN_COUNT = 17` to `syscall_registry.py` and `_check_syscall_registry_status()` wired into `/health/deep`.
- **IDEM-8**: APScheduler stub fix — closed 2026-05-23. Do not reassign this number.
- **IDEM-9**: EffectRecord TTL cleanup — closed 2026-05-24.
- Next available: **IDEM-10**.

When closing an entry, change `Status: Deferred — Low Priority` to `Status: CLOSED (YYYY-MM-DD)`
and replace the description with what was implemented and any remaining gap.

---

## CLI entry point — import-chain hazard

`aindy-runtime` (`AINDY/runtime_only.py`) uses module-level `__getattr__` to lazily
load the FastAPI `app`. This is load-bearing: it prevents `--help`, `--version`, and
`sandbox` from pulling in `AINDY.main` → `AINDY.db` → `database.py`, which calls
`create_engine(DATABASE_URL)` at import time and crashes when `DATABASE_URL` is unset.

**Do not add module-level imports to `runtime_only.py` that reach `AINDY.main` or
`AINDY.db`.** If you need something at module scope that touches either chain, the
`__getattr__` pattern must cover it, or the import must move inside the function that
needs it.

The same hazard applies to `_run_sandbox_check()` in `runtime_only.py`. Any import from
`AINDY.platform_layer.*` added to that function must be verified not to pull in
`AINDY.db` transitively. Known unsafe: `AINDY.platform_layer.health_service` (imports
`AINDY.db.schema_contract` at module level, line 48). The existing guard wraps it in
try/except; new additions need the same treatment or a verified-safe import.

---

## `_maybe_wrap_runtime_callback` — subprocess isolation hazard

`registry.py:_maybe_wrap_runtime_callback()` routes registered callbacks (trigger
evaluators, planner context providers, run tool providers, agent completion hooks,
capability definition providers, startup hooks) through a subprocess via
`runtime_callback_worker.py`. The subprocess is spawned with:

```python
cwd=str(Path(__file__).resolve().parents[2])
```

**In an installed wheel (Docker), this resolves to
`/usr/local/lib/python3.11/site-packages` — a read-only directory, not `/app`.**
Any relative-path file I/O at module import time in the wrapped module will fail.

**Silent failure mode:** `invoke_runtime_callback` raises `RuntimeError` when the
subprocess returns `{"ok": False}`. `evaluate_trigger()` in `autonomous_controller.py`
catches all exceptions and collapses them to `_decision("defer", 0.0, "trigger evaluator
failed")`. The HTTP response is still 202 — no 500, no visible error, permanent deferral.

**Correct guard pattern in modules imported by the subprocess (e.g., `config.py`):**

```python
# For mkdir:
try:
    log_dir.mkdir(parents=True, exist_ok=True)
except PermissionError:
    pass  # subprocess cwd is site-packages (read-only)

# For FileHandler:
try:
    handlers.append(logging.FileHandler(log_file))
except OSError:  # covers both PermissionError and FileNotFoundError
    pass
```

Use `except OSError` (not `except PermissionError`) for file-open operations. `mkdir`
throws `PermissionError`; `FileHandler` throws `FileNotFoundError` when the parent
directory doesn't exist. Both are `OSError` subclasses but only the broader catch
covers both cases.

Key files: `AINDY/platform_layer/runtime_callback_host.py` (subprocess spawn + CWD),
`AINDY/platform_layer/runtime_callback_worker.py` (subprocess runner),
`AINDY/config.py` (`_build_log_handler` — the fixed guards).

---

## TECH_DEBT.md — prefix registry

- **IDEM-\*** — idempotency audit findings. Next available: **IDEM-10**.
- **CLI-1** — lazy settings getter / module-level import hazard (deferred post-1.0).
- **CLI-SANDBOX-FORMAT-\*** — `sandbox` subcommand UX findings. CLI-SANDBOX-FORMAT-1: raw JSON wall, deferred to 1.0.1.
- **C2, C3** — cross-platform sandbox tiers. C2: container-grade, closed 2026-05-24. C3: strong-sandbox cross-platform; Phase 0 (adversarial escape test suite — 17 tests in `tests/sandbox/`, marker `sandbox_escape`, artifact `tests/sandbox/sandbox_escape_results.json`) complete 2026-06-04; Phase 1 (`_detect_wsl2()`, Linux backend propagation to OCI controls, 21 new unit tests) complete 2026-06-06; Phase 2 (`docker_macos_backend` detection, static matrix update, policy doc `docs/runtime/MACOS_CONTAINER_POLICY.md`) complete 2026-06-06; Phase 3 (threat model `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` + `sandbox_escape_test_posture()` in `sandbox_runner.py`) complete 2026-06-05; Phase 4 (release gate Step 16 in `docs/runtime/RELEASE_CHECKLIST.md`) complete 2026-06-05. All phases complete — macOS escape suite certification still pending (first run required before certifying macOS deployment).
- **PACK-DEBT-\*** — packaging and dependency findings.
- **DEBT-COMPAT-\*, TENANT-\*, COMPAT-\*, DATA-\*, LOCAL-\*** — architectural gaps.
- **ALEMBIC-FRESH-DB-\*** — alembic migration blank-database safety. ALEMBIC-FRESH-DB-1: closed 2026-05-27.
- **COMPOSE-PGVECTOR-\*** — pgvector extension requirement. COMPOSE-PGVECTOR-1: closed 2026-05-27.
- **PACKAGING-DEP-\*** — pip --prefix bootstrap-package propagation gaps. PACKAGING-DEP-1: closed 2026-05-27.
- **COMPOSE-HOST-\*** — container host binding issues. COMPOSE-HOST-1: closed 2026-05-27.
- **EVENTBUS-REDIS-URL-\*** — Redis URL env var consolidation. EVENTBUS-REDIS-URL-CONSOLIDATION-1: open.
- **PYPI-PUBLISH-\*** — PyPI publish transition. PYPI-PUBLISH-1: open.
- **MONITORING-GRAFANA-\*** — Grafana monitoring profile gap. MONITORING-GRAFANA-1: closed 2026-06-05.
- **COMPOSE-PROD-PORTS-\*** — database ports exposed in prod. COMPOSE-PROD-PORTS-1: closed 2026-06-05.
- **PROMETHEUS-PIN-\*** — Prometheus image version pinning. PROMETHEUS-PIN-1: open.
- **PLATFORM-UI-ENV-\*** — Vite `VITE_API_BASE_URL` bakes localhost into bundle. PLATFORM-UI-ENV-1: closed 2026-06-05 — relative-URL fallback + vite.config.ts proxy.
- **PLATFORM-AUTH-ACQUISITION-\*** — Platform SPA first-party auth. PLATFORM-AUTH-ACQUISITION-1: closed 2026-05-28.
- **PLATFORM-UI-KIT-\*** — ui-kit npm publish gap; local edits require manual rebuild chain. PLATFORM-UI-KIT-1: closed 2026-05-28.
- **MCP-BEHAVIOR-\*** — MCP protocol integration facts. MCP-BEHAVIOR-1: `call_tool()` never raises; check `result.isError is True` instead of `pytest.raises`.
- **AGENT-EVAL-\*** — Agent trigger-evaluator contract issues. AGENT-EVAL-001: swallowed evaluator exception + SUCCESS-on-defer envelope contract; closed 2026-06-03.
- **AGENT-APPROVE-\*** — Agent approve endpoint contract issues. AGENT-APPROVE-001a: concurrent race guard (CAS fix); closed 2026-06-03. AGENT-APPROVE-001b: background execution dispatch (approve returns immediately, execute_run fires in daemon thread); closed 2026-06-04. Orphaned-`approved` watchdog: open (liveness gap remains).
- **ROUTES-CONSUMER-SPLIT-\*** — Shared @aindy/ui-kit ROUTES table consumed identically by monolith and runtime; quarantine breaks monolith on next publish. ROUTES-CONSUMER-SPLIT-1: open.
- **API-MODULE-DRIFT-\*** — Quarantined ROUTES groups left platform SPA API modules reading undefined → TypeError. API-MODULE-DRIFT-1: rippletrace.js ×16, analytics.js ×19, platform.js ×4; open; fix depends on ROUTES-CONSUMER-SPLIT-1.
- **AGENT-API-\*** — Platform SPA agent.js functions reference never-existed ROUTES.AGENT.* constants. AGENT-API-001: getAgents/recallFromAgent/getFederatedMemory; consumer AgentRegistry.jsx; open.
- **AGENT-RESLIMIT-\*** — Agent execution resource limit conflicts with real workloads. AGENT-RESLIMIT-001: field renamed to `wall_time_ms`; `MAX_CPU_TIME_MS` → `MAX_WALL_TIME_MS`; migration 0005; closed 2026-06-05.
- **OPER-DEFER-\*** — Operator panel deferred-runtime routes (constant live, NavLink gated on FEATURE_FLAGS). OPER-DEFER-001: `/platform/flows/strategies` not yet served; OPER-DEFER-002: `/automation/logs` group (monolith today); both open.
- **SCHED-\*** — Scheduler status endpoint issues. SCHED-001/002/003: `/platform/observability/scheduler/status` returns 500 in platform-only profile (tasks domain absent); closed 2026-06-04 — direct impl replaces flow dependency; `FEATURE_FLAGS.OPERATOR_SCHEDULER_STATUS` flipped to `true`.
- **ROUTE-REG-\*** — Router files that exist but are never registered; their endpoints return 404. ROUTE-REG-001: `watcher_router` and `db_verify_router` unregistered; closed 2026-06-03 — watcher added to ROOT_ROUTERS, db_verify added to PLATFORM_ROUTERS.

---

## MCP protocol integration note (MCP-BEHAVIOR-1)

When working with any MCP server via `mcp.ClientSession.call_tool()`, the SDK **never raises a Python exception** for tool failures. Instead, it returns `CallToolResult(isError=True)`. Always check `result.isError` explicitly:

```python
result = await session.call_tool("tool_name", args)
if result.isError:
    # handle error — result.content[0].text has the error message
```

Do not write `with pytest.raises(...)` around `call_tool()` — it will never fire.

---

## Key file locations

| What | Where |
|---|---|
| Idempotency gate | `AINDY/kernel/syscall_dispatcher.py` |
| EffectRecord model | `AINDY/db/models/effect_record.py` |
| Schema version | `AINDY/db/schema_contract.py` — `SCHEMA_CONTRACT_VERSION` |
| Schema baseline | `scripts/schema_version_baseline.json` |
| Scheduler jobs | `AINDY/platform_layer/scheduler_service.py` |
| Alembic migrations | `alembic/versions/` |
| Idempotency contract | `docs/runtime/IDEMPOTENCY_CONTRACT.md` |
| 90-day hardening checklist | `AINDY_RUNTIME_90_DAY_CHECKLIST.md` |
| Runtime module map (tagged inventory) | `docs/runtime/RUNTIME_MODULE_MAP.md` |
| Runtime execution invariants | `docs/runtime/EXECUTION_INVARIANTS.md` |
| Architecture risk (complexity/blast-radius) | `docs/runtime/ARCHITECTURE_RISK.md` |
| Runtime security matrix | `docs/runtime/SECURITY_MATRIX.md` |
| Cross-repo compatibility policy | `docs/runtime/CROSS_REPO_COMPATIBILITY.md` |
| Runtime → SDK contract | `docs/runtime/SDK_CONTRACT.md` |
| Runtime → UI contract | `docs/runtime/UI_CONTRACT.md` |
| Release verification checklist | `docs/runtime/RELEASE_CHECKLIST.md` |
| Cross-repo regression tests | `tests/unit/test_cross_repo_compatibility.py` |
| Syscall registry floor constant | `AINDY/kernel/syscall_registry.py` — `SYSCALL_REGISTRY_MIN_COUNT` |
| Tech debt tracker | `TECH_DEBT.md` |
| Docker compose | `docker-compose.yml` |
| Dockerfile | `Dockerfile` |
| pgvector init script | `docker/init-pgvector.sql` |
| Prometheus config | `monitoring/prometheus.yml` |
| Runtime env reference | `AINDY/.env.example` |
| Auth router (issues JWTs) | `AINDY/routes/auth_router.py` |
| Auth service (bcrypt, JWT, key ring) | `AINDY/services/auth_service.py` |
| Admin bootstrap (Phase 5.5) | `AINDY/startup.py` — `_bootstrap_admin_email()` |
| CLI auth subcommand | `AINDY/runtime_only.py` — `_promote_admin()` |
| Platform SPA entry | `platform/src/PlatformApp.tsx` |
| SPA static files (asset 404 guard) | `AINDY/routing.py` — `_SPAStaticFiles` |
| Platform SPA Vite config | `platform/vite.config.ts` — `outDir: ../AINDY/platform/dist` |
| ui-kit source | `C:\dev\aindy-ui-kit\src\` |
| ui-kit auth API (unwrap invariant) | `C:\dev\aindy-ui-kit\src\api\auth.js` |
| Subprocess callback spawn (CWD hazard) | `AINDY/platform_layer/runtime_callback_host.py` |
| Subprocess callback runner | `AINDY/platform_layer/runtime_callback_worker.py` |
| Log handler OSError guard | `AINDY/config.py` — `_build_log_handler` |
| Sandbox escape test suite | `tests/sandbox/` — marker `sandbox_escape`, image `python:3.11-alpine` |
| Sandbox escape results artifact | `tests/sandbox/sandbox_escape_results.json` |
| Sandbox escape audit log (append-only) | `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` |
| Sandbox escape posture function | `AINDY/platform_layer/sandbox_runner.py` — `sandbox_escape_test_posture()` |
| macOS container sandbox policy | `docs/runtime/MACOS_CONTAINER_POLICY.md` |
| WSL2 / macOS backend detection | `AINDY/platform_layer/sandbox_runner.py` — `_detect_wsl2()` |
| Open questions tracker | `docs/runtime/OPEN_QUESTIONS.md` |
| Route ownership inventory | `docs/runtime/ROUTE_OWNERSHIP_INVENTORY.md` |
| nginx plain HTTP config | `nginx/nginx.conf` |
| nginx TLS config (Let's Encrypt) | `nginx/nginx.tls.conf` |
| Compose production port override | `docker-compose.prod.yml` |
| Apps monolith project instructions | `C:\dev\aindy-apps-monolith\CLAUDE.md` |
