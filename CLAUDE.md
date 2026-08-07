# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**This file is the authoritative agent-instruction surface for this repo.** Two companions:

- **[`docs/platform/governance/AGENT_WORKING_RULES.md`](docs/platform/governance/AGENT_WORKING_RULES.md)**
  — the *collaboration* boundaries: what an agent may change without approval, what requires
  sign-off, and how to behave at a boundary it cannot resolve. This file covers what is true
  about the codebase; that one covers what you are permitted to do to it. **Read it before
  making a change whose blast radius you are unsure of.**
- **[`CODEX.md`](CODEX.md)** — a pointer to this file, not a parallel copy. It used to be a
  hand-maintained duplicate and drifted badly; do not reintroduce content there.

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
- Current chain: `0001` → `0002` → … → `0009` → `0010` (`0010_agent_runs_flow_run_id_index`, RTR-3).

**Head-revision bump protocol (APP-DEPLOY-1 / `bootstrap-schema`):** when you add a new
`alembic/versions/NNNN_*.py`, also bump `RUNTIME_ALEMBIC_HEAD_REVISION` in
`AINDY/db/alembic_head.py` to the new head. That constant is the packaged source of truth
the `aindy-runtime bootstrap-schema` command stamps into `alembic_version_runtime` — the
`alembic/` scripts dir lives at the repo root and is **not** shipped in the wheel
(`packages.find = AINDY*`), so the command cannot read the head from the scripts at
install time. `tests/unit/test_runtime_alembic_head.py` fails if the constant drifts from
the actual scripts-dir head, so a forgotten bump is caught in CI. Note `memory_nodes`
(defined in the runtime-owned `AINDY/memory/memory_persistence.py`) is runtime-owned and
included by `runtime_owned_table_names()`; it is create_all-managed via the schema
contract, NOT alembic-tracked (it is absent from env.py's `_RUNTIME_TABLES` autogenerate
allowlist because alembic's `env.py` does not import the memory model — a deliberate,
not-a-bug asymmetry).

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
`pending_approval → approved` transition with an atomic SQLAlchemy CAS.

**CAS fires only from `pending_approval`.** `approve_run()` returns immediately after the CAS;
`execute_run` is dispatched to a daemon background thread. A process crash between approval and
the thread's first `db.commit()` (which sets status `executing`) leaves the run stranded in
`approved`. The orphan watchdog (`_recover_orphaned_approved_runs` in `scheduler_service.py`,
runs every 5 minutes) re-dispatches `execute_run` for any `approved` row older than
`ORPHANED_APPROVED_THRESHOLD_MINUTES` (10 min). **AGENT-APPROVE-001b: CLOSED 2026-06-04.**
Do not add a second CAS guard in `execute_run` — the status check on entry is the correct
guard; the 10-minute threshold ensures the original thread is dead before re-dispatch fires.

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

**Docker does NOT build the SPA — it installs it, prebuilt, from PyPI.** *(Corrected
2026-08-05. This section previously described a `node:20-alpine` `ui-builder` stage running
`npm ci` + `npm run build`; that stage was **deleted 2026-06-15** in `0a427a6` when the
image switched to installing the published wheel, and the doc was never updated.)*

The Dockerfile has two stages, both `python:3.11-slim`, and no node at all. The builder does:

```dockerfile
RUN pip install --prefix=/install "aindy-runtime==2.0.0"
```

The SPA rides along inside that wheel as package data — `pyproject.toml` declares
`[tool.setuptools.package-data] "AINDY" = [..., "platform/dist/**"]` with
`include-package-data = true`. So `docker compose build` still needs no local UI build, but
for a different reason than the old text gave, and with a **consequence that text hid**:

> **A UI change does not reach any container until a release is cut AND the Dockerfile pin
> is bumped.** The image ships whatever `AINDY/platform/dist` was packaged into the pinned
> version. This is exactly why a running container served `assets/index-CmX9Wucu.css`
> (tailwind 3) while the working tree had `index-C9NdGPSF.css` (tailwind 4) — not a caching
> bug, the designed behaviour. Verify UI work against `npm run dev`, and treat the container
> as showing the last *released* UI.

The `dist/` that gets packaged is CI's own — `Runtime Package Build` runs after
`Platform UI Build` in `runtime-ci.yml` — so the wheel never carries a locally-built bundle.

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

Entries are numbered sequentially. Do not reuse a number. IDEM-1 through IDEM-10 are recorded in TECH_DEBT.md. **IDEM-10 (open, 2026-07-09): the EXACTLY_ONCE idempotency gate is dead in production — never persisted to an EU + EU-PK lookup can't match; agent tool calls bypass the dispatcher entirely.** Next available: **IDEM-11**.

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
evaluators, agent completion hooks, capability definition providers, startup hooks)
through a subprocess via `runtime_callback_worker.py`. **Exception (PLANNER-SUBPROC-1
+ INFINITY-COMPLETION-HOOK-BOUNDARY-1): `run_tool_provider`, `planner_context`, and
`agent_completion_hook` run in-process** — they read live in-process state
(`TOOL_REGISTRY`, planner context) or must re-open a session to reach live app state that a
bare subprocess can't reconstruct (its cwd is read-only site-packages, so `load_plugins()`
finds no app manifest → zero tools → planner 500 on Linux; and completion hooks no-op'd,
killing the post-completion Infinity loop). They are listed in
`_STATEFUL_IN_PROCESS_CALLBACK_SURFACES`. **Note:** the boundary sanitizer still strips
`db`/`run` for completion hooks — the context carries `run_id` (a string that survives) so
the hook re-fetches with its own session; the runtime never leaks a db/ORM handle. The
subprocess is spawned with:

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

## Current phase + standing decisions (2026-08-01)

**Phase: runtime testing.** Things get connected to the runtime in order to exercise it — which
is why app-side feature requests keep arriving; they are a symptom of the testing method, not
scope creep. **Consequence: flag soak happens in `aindy-apps-monolith`, not here.** The runtime
ships capabilities default-off; the app repo turns them on and lives with them. Don't plan soak
work in this repo.

**★ THE NEXT RELEASE MUST BE `2.0.0`.** `main` carries a merged, unreleased breaking change —
`register_user` now rejects passwords under `MIN_PASSWORD_LENGTH`. Semver is followed as a rule
here, and this is load-bearing rather than ceremonial: `runtime_compatibility.py:11`
`_major_series()` advertises `recommended_runtime_requirement` as `>={major}.0,<{major+1}.0`, so
releasing that change as 1.12.0 would make the runtime's own self-reported compatibility claim
false. Either ship 2.0.0 or pull the change first. **Cross-repo:** on 2.0.0 the advertised
requirement flips to `>=2.0,<3.0` and the apps-monolith floor (`>=1.11.0,<2.0`) excludes it — the
app team must move that pin deliberately.

**Other standing decisions** (full record: `TECH_DEBT.md` → `DECISIONS-2026-08-01`):
FR-6 email delivery = **hybrid** (registered `email` connector if present, else runtime SMTP);
`/auth/register`'s 409-on-duplicate **enumeration oracle is to be fixed — but it is NOT standalone work: it is a dependent of the FR-6 email decision.** Register returns an access token on success and a duplicate cannot be given one, so the responses must differ; no status/message choice closes the oracle while registration also authenticates. A real fix is the standard shape — always neutral `202`, token only after an emailed verification link, duplicate gets a *"someone tried to register"* mail — i.e. an email-verification flow that does not exist (no `is_verified`/`verification_token` on `User`). Rate limiting is irrelevant (targeted enumeration is one request). **Second channel to remember:** the duplicate path returns *before* `hash_password`, so it skips bcrypt and is measurably faster — a status-code-only fix leaves that timing oracle intact. **Build FR-6 first and fold this in;** #3 alone can only look fixed. Also: **add a cargo build
job** (NATIVE-CI-1, blocks #292/#296/#306); **verify** `python-jose`/`passlib` interop before
merging cryptography 48→49 (#302); the UI major cluster is **deferred and decided from
`C:\dev\aindy-ui-kit`** (its react-router 6 peer pin blocks #312/#324 — and #324 supersedes #312).

---

## TECH_DEBT.md — prefix registry

Open items only; closed entries are in TECH_DEBT.md. Do not reuse numbers within a prefix.

- **IDEM-\*** — idempotency audit findings. **IDEM-10 open (2026-07-09): EXACTLY_ONCE gate never fires in prod + agent tool calls bypass it — the real ECOGAP-1 Phase 3a.** Now consolidated into the **Mediated Effect Boundary program** (`docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md`): IDEM-10 = MEB-0 (tool-path effect boundary, keystone) + MEB-1 (dispatcher gate repair); G4a = MEB-2; multi-tenant MCP = MEB-3. Start at MEB-0 (self-contained, no schema change). Next available: **IDEM-11**.
- **CLI-1** — lazy settings getter / module-level import hazard. Open, deferred post-1.0.
- **CLI-SANDBOX-FORMAT-1** — `sandbox` raw JSON output wall. Open, deferred to 1.0.1.
- **C2, C3** — cross-platform sandbox tiers. All phases closed 2026-06-06.
- **PACK-DEBT-\*** — packaging and dependency findings.
- **DEBT-COMPAT-\*, TENANT-\*, COMPAT-\*, DATA-\*, LOCAL-\*** — architectural gaps.
- **PYPI-PUBLISH-1** — CLOSED 2026-06-14 (first published at v1.3.1). **Latest release: v2.0.1 (2026-08-05)** — PATCH, fixes the 2.0.0 upgrade path: FR-10 (empty env var crash-looped the container at module import), FR-8 (`--reconcile` now grandfathers rows predating a new column — alembic `0014` never ran on wheel installs), FR-9 (runtime mail moved to a reserved `transactional_email` type). Plus `cryptography` 50.0.0 (CVE-2026-69247), node 24 in CI, and the UI toolchain major — **first wheel shipping the Tailwind 4 SPA**. Schema `2026-08-05` with **no DDL change**; alembic head stays `0014`; `recommended_runtime_requirement` stays `>=2.0,<3.0` so no consumer pin moves. Sandbox gate Entry 013, 17/17. **Prior: v2.0.0 (2026-08-02)** — MAJOR. Breaking, all in auth: register returns 202 with no token (closes the enumeration oracle), access tokens require a `purpose` claim (ends every existing session at upgrade), `MIN_PASSWORD_LENGTH` on register. Plus FR-6 in full (change/forgot/reset/verify-email), FR-7 memory capture fixes, and a security fix for plaintext passwords in `input_payload`. Schema `2026-08-02`, Alembic `0014`. **`recommended_runtime_requirement` now reports `>=2.0,<3.0`, so consumers pinned `<2.0` must move their pin deliberately.** **and** the Dockerfile builder-stage pin (`pip install "aindy-runtime==X.Y.Z"`) + CHANGELOG in one PR; after it merges green, push the `vX.Y.Z` tag → `publish.yml` (TestPyPI → PyPI behind the `production` manual-approval gate) + `sandbox-escape-linux.yml` (Linux escape gate) fire on the tag. Then append a `SANDBOX_ESCAPE_AUDIT.md` entry for the gate run. `Boot Smoke` installs the pinned version from PyPI so a bump PR skips-green until the tag publishes.
- **NODUS-UPGRADE-1** — CLOSED 2026-06-11: bumped to nodus-lang==4.0.3; 4.0.5 (2026-06-19); **4.1.0 (2026-07-17)** — all no-code-change bumps. The 4.1.0 bump was risk-probed (full nodus unit surface passes; the 3 version-fragile internal couplings survive — `syscall_runtime.call_syscall`, `NodusRuntime._get_active_vm()`, `register_function`; no new transitive deps). See NODUS_DEVELOPER_GUIDE.md §8.
- **NODUS-SYS-SURFACE-1** — CLOSED 2026-07-12 (fail-loud guard + doc): idiomatic `import "std:sys"` routed to nodus's 4-syscall in-process stub (`syscall` builtin → `nodus.services.syscall_runtime`), NOT the AINDY dispatcher; only the bare `sys(...)` builtin reaches `dispatch_syscall`. It could NOT be aliased (`register_function` forbids overriding a builtin; VM resolves builtins before host fns), so `nodus_worker.py::_install_std_sys_guard()` monkeypatches `syscall_runtime.call_syscall` to fail loud with a "use the bare `sys(...)` builtin" error (real-VM verified). Documented in NODUS_DEVELOPER_GUIDE §3.4. See TECH_DEBT.md.
- **NODUS-WARMPOOL-1** — CLOSED 2026-07-19 (P1, quick knob shipped 2026-07-10): every Nodus execution spawns a fresh worker subprocess that cold-starts the whole plugin stack (~12s on the 17-app profile) *inside* the wall-clock budget, so app-profile runs hit `"Nodus script exceeded 30000ms wall-clock timeout"` — a runtime architecture issue, NOT a nodus-lang bug (the kill is the runtime's own `subprocess.run(timeout=)` in `nodus_runtime_adapter.py`; a nodus upgrade can't fix it). Quick mitigation: `AINDY_NODUS_MAX_EXECUTION_MS` (default 30000) now sets BOTH the outer subprocess timeout and the inner `run_source(timeout_ms=)`; per-run `context.max_execution_ms` still overrides. **Option A (clock split) SHIPPED 2026-07-18 (#265, v1.9.0):** outer `subprocess.run(timeout=)` = `AINDY_NODUS_MAX_EXECUTION_MS` (inner script clock) **+** `AINDY_NODUS_BOOT_ALLOWANCE_MS` (default 15000), so cold-start isn't billed to the script budget — fixes the "boot billed to script" bug (per-run latency unchanged). **Option B Phase 1 (single warm worker) SHIPPED 2026-07-19 — the durable latency fix:** `AINDY_NODUS_WARM_POOL` (default off) keeps ONE long-lived worker that loads the plugin stack once (amortized). `nodus_worker.py` `main()`+`serve_forever()` share `run_one(payload)` (rebuilds all per-request state → no cross-run leak); `nodus_worker_pool.py` = length-prefixed JSON framing + respawn/recycle (`AINDY_NODUS_WARM_MAX_REQUESTS`, default 500) + reader-thread timeout; adapter routes through the pool and **falls back to a fresh subprocess on any fault** (can't regress). **Phase 2 (bounded worker pool) SHIPPED 2026-07-19:** `NodusWorkerPool` = up to `AINDY_NODUS_WARM_POOL_SIZE` (default 4) workers, each serial, so N executions run concurrently; saturation waits `AINDY_NODUS_WARM_ACQUIRE_TIMEOUT_MS` (default 2000) then raises `PoolBusy`→adapter spills to a fresh subprocess (bounded backpressure). **Phase 3 (metrics/drain/pre-warm) SHIPPED 2026-07-19 → NODUS-WARMPOOL-1 CLOSED:** `pool.stats()` + Prometheus (`aindy_nodus_warm_pool_*`); `pool.drain(timeout_s)` (stops checkouts→spill, waits for in-flight, kills); `pool.prewarm()` pays plugin load ahead of traffic via a worker `{"__warmup__":true}` control request (tool-less scripts still skip load), kicked in a background thread on first `get_pool()` when `AINDY_NODUS_WARM_PREWARM` on. Deferred (not required): active health-heartbeat + the sibling `runtime_callback_host.py` 10s callback subprocess. See TECH_DEBT.md.
- **ECOGAP-\*** — Ecosystem capability gaps from the 12-project re-audit (corrected lens), `ECOGAP-1..6`. Roadmap gaps, not classic debt (except ECOGAP-6 + narrow 5a). Source: `docs/runtime/ECOSYSTEM_CAPABILITY_GAPS.md`. Note: ECOGAP-2 (sandbox) is owned by **C2 (closed)/C3 (open)** — the audit overstated it; container-grade is certified + escape-tested. ECOGAP-3 extends MEMORY-EMBEDDING-PROVIDER-1. Don't double-track. **ECOGAP-1 Phases 1+2 shipped 2026-07-08** (opt-in, `AINDY_DURABLE_CONTINUATION` default off): Phase 1 `core/flow_continuation.py` re-drives stranded non-waiting flows from `current_node`/`state` via `PersistentFlowRunner.resume()` at startup, gated per-flow `mark_flow_continuation_safe`; Phase 2 `core/agent_continuation.py` re-drives crashed nodus_vm agent runs from their last completed segment (reuses `_build_agent_resume_callback` with `claim_status="executing"`), gated per-agent-type `mark_agent_type_continuation_safe`. Phase 2a per-step segment granularity shipped 2026-07-08 (opt-in `AINDY_DURABLE_STEP_GRANULARITY`, off): `split_agent_plan` expands each multi-step segment into one-segment-per-step so Phase 2 continuation resumes at step granularity (one-VM-per-step, reuses everything, no schema). Deferred: full pending→success worker-subprocess WAL (non-idempotent at-most-once) + Phase 3 (FlowHistory-fold event-sourcing + clock threading + broaden EffectRecord). **ECOGAP-4 (MCP/A2A) scoped 2026-07-09 (deferred, PR #212 docs):** verify-first audit found **G4a (runtime egress+secret-broker) built but INERT** — enforcement seams cut at `execute_tool` (`tool_registry.py`) but every guard vacuous (no `CapabilityPolicy`/secret scope registered; `resolve_secret` has zero prod callers; no true egress chokepoint — arg-string inspection only). **G4b (MCP/A2A wire adapters) has zero runtime code**; real out-of-tree `nodus-mcp`/`nodus-a2a`, plugin ABI (`register_agent_tool`/`load_plugins`) ready. Strong-form G4a (mediated egress) converges with IDEM-10. Reopen: first external MCP interop, or sandbox needs mediated egress. **★ G4b CLIENT-SIDE SHIPPED 2026-07-11 (opt-in):** `AINDY/platform_layer/mcp_client.py` (`bootstrap()` on default manifest, no-op unless `AINDY_MCP_CLIENT_ENABLED`+`AINDY_MCP_SERVERS`) discovers external MCP tools via `nodus_mcp_aindy` and registers each via `register_tool`→`TOOL_REGISTRY` (cap `outbound.mcp`, risk high); `pip install aindy-runtime[mcp]`. Corrections from the build: A2A is NOT a wire protocol (out of scope); `register_tool` is the executable path NOT `register_agent_tool` (`_agent_tools` is discovery-only); nodus-mcp packaging fixed in 0.1.1 (issue #5). **★ G4b SERVER-SIDE (stdio) SHIPPED 2026-07-11 (opt-in):** `aindy-runtime mcp-server --transport stdio` (`AINDY/platform_layer/mcp_server.py`) exposes an allowlist of syscalls as MCP tools to external clients (Claude Desktop); each handler `dispatch_syscall(name, args, user_id=<configured>)`. Single configured identity `AINDY_MCP_SERVER_USER_ID`; read-only default (`AINDY_MCP_SERVER_ALLOW_WRITES` opts in writes; `AINDY_MCP_SERVER_TOOLS` overrides). Verified write→read-back on real PG. Deferred: SSE transport (nodus-mcp #7) + multi-tenant per-session auth (= G4a) + both G4a forms. See `docs/runtime/MCP_INTEGRATION.md`.
- **RTR-\*** — Runtime Roadmap (`RTR-1..8`): Nodus-first execution substrate + runtime primitives, consolidated from the app-side evolution docs and validated against source 2026-06-29. **BUILD** (greenfield): RTR-1 **CLOSED 2026-07-07** (Nodus `register_nodus_workflow` + VM-backed agent path shipped; all four runtime gaps resolved — Phase-3 managed bytecode cache is roadmap, dead `NodusTraceEvent` trace path dropped via Alembic `0009`; `nodus_vm` stays opt-in behind `AINDY_AGENT_EXECUTION_BACKEND` pending app-side soak + RTR-3), RTR-5 (autonomous execute-window — **shipped opt-in 2026-07-08**: `agents/autonomous_window.py` `run_execute_window` composes evaluate→create_run→execute_run bounded by max-iterations/active-run-cap/cooldown, gated `AINDY_AUTONOMOUS_EXECUTE_WINDOW` default off; registered async job `agent.autonomous_window`; new `AUTONOMY_WINDOW` event), RTR-6 (reasoning event model — **first-class capture signal shipped 2026-07-08**: `reasoning.signal` event + `core/reasoning_signal.py`, wired at `memory_capture_engine`; recall stays first-class via `RECALL_USED`; dedicated `ReasoningEvent` DB model deferred). **HARDEN** (built, finish gaps): RTR-2 (durable queue exists; **gaps 1+2 done 2026-07-08** — `config.resolve_execution_mode()` makes prod default to `distributed` (fail-fast), one source replacing 3 duplicated env reads; `job_recovery.recover_orphaned_thread_jobs()` re-dispatches thread-mode orphans at startup; per-tenant lanes deferred to DEPLOY-TARGET-2 — see SYSMAX-1/TIER3-10), RTR-3 (AgentRun↔FlowRun unification — **HARDEN half done 2026-07-08**: enums canonicalized in `condition_codes.py` + single-source terminal/active classification helpers + no-op recovery gap closed across the 6 reconcilers + `ix_agent_runs_flow_run_id`, schema `2026-07-08`/Alembic `0010`; full unification / non-nullable link is the deferred BUILD half), RTR-4 (delegation core built; **gaps a+b done 2026-07-08** — per-delegate capability narrowing active by default + opt-in `AINDY_DELEGATION_HANDSHAKE` accept/reject handshake with `AWAITING_DELEGATION` + `respond_to_delegation`; **gap c token-scoped private memory SHIPPED 2026-07-12/13** (#245/#246) — `AINDY_DELEGATION_PRIVATE_MEMORY` (`config.py:342`, default off), enforced at `memory_persistence.py:136/174`, owner threaded from `execution.py:214`; remaining = soak+flip. Gotcha: delegate writes take the **deferred** capture path, so `MemoryNodeDAO.save` is the write chokepoint, not the syscall), RTR-7 (execution-causality canonical via `EventEdge`; "RippleTrace" name dissolved into the primitive by design — **runtime half CLOSED 2026-07-08**: `execution_graph` endpoint falls back to the runtime `event_trace_service` when app `rippletrace_*` symbols are absent; substantive remainder is app-owned/RTR-2-gated). RTR-8 (PyPI) is stale/CLOSED. Source: `TECH_DEBT.md` RTR-*.
- **PROMETHEUS-PIN-1** — CLOSED 2026-06-05: pinned to prom/prometheus:v3.4.1 in docker-compose.yml.
- **MCP-BEHAVIOR-1** — `call_tool()` never raises; check `result.isError is True` instead of `pytest.raises`.
- **MCP-SDK-2X-1** — **Open (2026-07-31).** `mcp 2.0.0` removed the 1.x low-level `Server.list_tools()` decorator that `nodus-mcp 0.1.2` (latest) is built on, so `NodusServer.__init__` raises `AttributeError` and `test_mcp_client_live.py` reddened **every** CI run. Both install sites were unbounded `mcp>=1.0.0` — the `[mcp]` extra in `pyproject.toml` **and** the separate "Install MCP extra" step in `runtime-ci.yml` (it installs the packages directly, not via the extra, so a cap must be repeated in both or CI resolves past it). Both now say `"mcp>=1.0.0,<2"`. **Not a test bug — do not skip the live test to go green**; the extra is broken at server-construction time for real callers. Our code (`mcp_client.py`/`mcp_server.py`) imports only `nodus_mcp_aindy`, never the `mcp` SDK, so nothing needs porting — lift the cap in both files when an upstream nodus-mcp release targets mcp 2.x.
- **OPER-DEFER-001** — CLOSED 2026-06-15: `GET /platform/flows/strategies` served; Strategies tab live.
- **OPER-DEFER-002** — CLOSED 2026-06-15: `GET|POST /automation/logs` served in `automation_router.py`; Automation tab live.
- **INFINITY-RUNTIME-1** — **FULLY CLOSED 2026-07-08** (Deliverable C acting half added 2026-07-09). Runtime counterpart to app handoff `INFINITY-RUNTIME-HANDOFF-1`; unblocks app Infinity Phase 2. **Full advance log is in `TECH_DEBT.md` — this is the summary + load-bearing facts.** All five loop-closure gaps + the item-3 aggregate syscall shipped (PRs #194–#198): Gap 1 recall→planning + `RECALL_USED` (`core/execution_recall.py`, flag `AINDY_PLANNER_MEMORY_INJECTION`), Gap 3 per-run `SCORE_COMPUTED` (`core/execution_score.py`), Gap 4 Next-Action record-first (`core/next_action.py` + emit at `execution.py` `_emit_agent_next_action`), Gap 5 async jobs join the loop (flag `AINDY_ASYNC_JOB_LOOP_CLOSURE`), item-3 syscall `sys.v1.observability.support_metrics` (cap `execution.read`; bumped `SYSCALL_REGISTRY_MIN_COUNT`→22 + `_STABLE_SYSCALLS`). Events `RECALL_USED`/`SCORE_COMPUTED`/`NEXT_ACTION_CHOSEN` are un-prefixed (not `execution.`-gated). **Gotcha: adding a `SystemEventTypes` value trips the frozen-hash baseline — update it in lockstep.** **Deliverable C (autonomous acting on NextAction) shipped opt-in 2026-07-09 (#213, in v1.6.2):** `core/next_action_dispatch.py` `maybe_act_on_next_action` (wired into `_emit_agent_next_action` after the record emit) dispatches ONE bounded follow-up run for an **app-sourced** `trigger_execution` (objective in `args`) → async job `agent.next_action_followup` → create_run→execute_run. Reuses admission (`count_active_executions`) + approval gate (a `pending_approval` follow-up is NOT force-executed); net-new **chain-depth cap** (`parent_run_id` hops, `AINDY_NEXT_ACTION_MAX_CHAIN`) so a self-returning hook can't self-perpetuate. Never acts on a runtime-default decision. Gated `AINDY_NEXT_ACTION_ACTING` (default off); agent runs only; no syscall/schema/new-event. **Remaining (non-blocking): flip the 3 opt-in flags after real-world soak (`AINDY_PLANNER_MEMORY_INJECTION`, `AINDY_ASYNC_JOB_LOOP_CLOSURE`, `AINDY_NEXT_ACTION_ACTING`); broaden acting verbs (`retry`/`schedule_follow_up`) if needed.**
- **AGENT-HARDEN-\*** — Open (2026-07-05, from two agent-framework self-assessments). Runtime-owned safety/security/resilience/architecture hardening, each riding existing primitives: `-1` **CLOSED 2026-07-05** — `sys.v1.agent.cancel` (cap `agent.cancel`) + terminal `AgentRunStatus.CANCELLED` + cooperative check at `_execute_agent_segment_chain` segment boundaries (nodus_vm backend); AGENT_FLOW node-granularity interrupt deferred to RTR-1. `-2` **CLOSED 2026-07-05** — capability-token `token_hash` is now HMAC-SHA256 keyed on the `KeyRing` secret (`auth_service.signing_key()`/`verification_keys()`), active+previous verify with constant-time compare; Ed25519 identity tier deferred. Migration: pre-deploy tokens fail verify (drain/re-approve). `-3` **CLOSED 2026-07-05** — compensating-undo engine: `SyscallEntry.compensate` hook + append-only `effect_reversals` table + `core/effect_compensation.py` `undo_run_effects` (walks a run's successful EffectRecords in reverse → reversed/irreversible/failed) + `sys.v1.agent.undo` (cap `agent.undo`). No built-in compensators yet (all effects report irreversible). Schema bump 2026-07-05, migration 0008. This is the rollback half of `-6`. `-4`/`-4b` **PR1 done 2026-07-05** — shadow `call_tool` seam: `runtime/tool_simulation.py` `simulate_agent_tool` (read-only capability gate, predicted output + `would_write` intent, never executes); `simulate` flag threads `NodusExecutionContext`→worker→`simulated_effects`. PR2 done 2026-07-05 — `sys.v1.agent.simulate` (cap `agent.simulate`) + `simulate_agent_run` runs a plan shadowed and persists `run.result["simulation"]` (no status change); `simulate` flag threads flow-node→`execute_nodus_runtime`→context→subprocess. MIN_COUNT 20→21. **-4 close trigger met.** PR3 done 2026-07-05 (-4b): `virtual_tools` fake tool impls (`{tool: {result, success?, error?}}`) threaded ctx→worker→`simulate_agent_tool` + `sys.v1.agent.simulate` payload — rehearsal against a simulated world (effects tagged `source:virtual`/`placeholder`); network isolation is inherent (zero real tools execute), container `--network none` stays the extension-path guarantee. **-4/-4b both CLOSED.** `-5` **CLOSED 2026-07-05** — `FallbackLLMClient` + `get_llm_client_chain()`/`resolve_provider_chain()` in `llm_client.py`, config-driven via `LLM_PROVIDER`+`LLM_FALLBACK_PROVIDERS`; open-primary-breaker fails over to secondary. Factory available; call-site adoption (planning/embedding/planner_backends) is a deferred opt-in follow-up. `-6` **CLOSED 2026-07-05** — Verifier stage: `core/verifier.py` (rules-based per-step `expects` checker, fail-closed) wired into `_execute_agent_segment_chain` terminal-success; pass→`completed`+`VERIFIED`, fail→terminal `AgentRunStatus.VERIFY_FAILED`+`VERIFY_FAILED` event + `undo_run_effects` rollback (-3). No `expects` → vacuous pass (no behavior change). Stacked on #168 (-3). `-7` **PR1 done 2026-07-06** — respx recorded-cassette contract tests (`tests/fixtures/cassettes/`) for the OpenAI chat+embedding & DeepSeek chat boundaries (request wire shape + response handling). Surfaced+fixed a real bug: DeepSeek SDK had no `base_url` → calls hit api.openai.com; now `settings.DEEPSEEK_BASE_URL`. Remaining: same pattern for first-party HTTP tools, opportunistic, `-8` **PR1 done 2026-07-06** — declarative per-capability policy: `agents/capability_policy.py` `CapabilityPolicy(recipients, domains, rate)` + registry + `enforce_capability_policy` (recipient/domain-egress allowlists, extracted from call args), enforced in `execute_tool` after the capability check; vacuous until a policy is registered. PR2 done 2026-07-06 — `CapabilityPolicy.rate` (`"N/period"`) enforced via new `ResourceManager.rate_limit_hit` (Redis fixed-window + in-memory fallback) + `enforce_capability_rate` (keyed cap×tenant). **-8 CLOSED.**, `-9` **PR1 done 2026-07-06** — secrets broker: `platform_layer/secret_broker.py` `SecretBroker` ABC + `EnvSecretBroker` (`AINDY_SECRET_<NAME>` namespace) + capability-scoped fail-closed `resolve_secret` (JIT, never persisted; not a syscall — value must not transit the trace-logged envelope). PR2 done 2026-07-06 — `FileSecretBroker` (Docker/K8s `/run/secrets`), `VaultSecretBroker` (KV v2 over httpx, respx-tested), `ChainSecretBroker`; `execute_tool` wraps the tool in `capability_scope(token caps)` so `resolve_secret(name)` is gated by the run's grants (secret consumed in-tool, never returned to the script). **-9 CLOSED.**, `-10` **PR1 done 2026-07-06** — `platform_layer/extension_signing.py`: real Ed25519 detached bundle signatures + trust registry + `enforce_bundle_signature` profile gate (production refuses unsigned/untrusted) + CycloneDX-lite SBOM. PR2 done 2026-07-06 — `derive_plugin_artifact_provenance` verifies a declared `signature:{value,key_id}` against the trust registry (records `signing:{verified/unverified/unsigned}`); enforcement scoped to external-third-party + gated by `AINDY_REQUIRE_SIGNED_PLUGINS` (production profile refuses unsigned/untrusted, default off). `signing.status` flipped unsupported→**supported** (public version-API contract; the 2 frozen assertions updated). **-10 CLOSED.**. MCP is NOT here — it's `ECOGAP-4` (G4a runtime egress/secret-broker, G4b plugin wire adapter). Priority order: 1→2→5, then 3→6→4/4b, with 7–10 opportunistic.
- **ROUTE-EXTRACT-\*** — Route extraction to plugin layer. Remaining candidates: `memory_router` (split required), `coordination_router` (AgentRegistry ownership gap).
- **SDK-SYSCALL-GRANT-1** — CLOSED 2026-07-07: `dispatch_syscall` now derives the grant from the requested syscall's own `entry.capability` (least-privilege, one cap/dispatch) against a governed dispatch surface (`_resolve_dispatch_capabilities`/`_DISPATCH_CAPABILITY_SCOPES` in `platform_ops_router.py`), replacing the fixed-`DEFAULT_NODUS_CAPABILITIES` grant + prefix gate. `flow.run` now grantable (scope `flow.execute`); `event.emit` grantable to API-keys via new `Scopes.EVENT_EMIT` (additive, no regression); memory reads honor `memory.read` scope. SDK README's nonexistent `flow.run`/`syscall.*` scopes corrected. Distinct from TIER3-V2V3 (domain-scope *gate*, closed).
- **APP-FR-\*** — App-side runtime feature requests (handoff 2026-07-17, `aindy-apps-monolith`). FR-1..FR-4, verified against source on receipt. **FR-1 SHIPPED 2026-07-17** (`register_connector` + `dispatch_connector`/`ConnectorContext` in `connector_service.py` + `authorized_external_call`/`OutboundCallDenied` in `external_call_service.py` + `outbound_http.outbound_request`; opt-in/vacuous-by-default, composes AGENT-HARDEN-8/9 + egress_guard; contract `docs/runtime/CONNECTOR_CONTRACT.md`). **FR-2 already shipped** (= RTR-1 `register_nodus_workflow`). **FR-3 SHIPPED**: core acting half = INFINITY-RUNTIME-1 Deliverable C; **dispatch-outcome contract shipped 2026-07-17** — new `SystemEventTypes.NEXT_ACTION_DISPATCHED` (`next_action.dispatched`) + `emit_next_action_dispatched` + `DISPATCH_DISPOSITIONS` in `core/next_action.py`, one outcome per app-sourced `trigger_execution` candidate parented to `NEXT_ACTION_CHOSEN`; **adding the event type required regenerating `tests/baselines/system_event_contract.json`** (frozen-hash gotcha). Only verb-broadening (`retry`/`schedule_follow_up`) + flag-flip remain. **FR-4 ALREADY SATISFIED** (verified 2026-07-17): completed by this repo's `DOCS-BUCKET-A-1` migration 2026-06-27/28 (before the handoff) — Bucket A docs + runtime half of `INVARIANTS.md` all present/tracked; no relocation to do (handoff premise stale like FR-2/FR-3). **FR-1/3/4 shipped in v1.8.0.** **FR-6 (NEW 2026-07-31, verified real against the live
OpenAPI):** the whole auth surface was 4 routes — no forgot/reset and, sharper, **no
change-password**, so setting one meant a direct `UPDATE users SET hashed_password`.
**Item 1 SHIPPED 2026-07-31:** `POST /auth/password/change` (Bearer-JWT only; API-key
principals rejected like `logout`) → `auth_service.change_user_password` verifies current,
enforces `MIN_PASSWORD_LENGTH`=8 + new≠current, rehashes, bumps `token_version`, and returns a
re-versioned token **in `/auth/login`'s exact shape** so the caller survives its own session
invalidation. **Gotchas:** auth routes return the *canonical envelope* (`{status, data}`) —
`response_model=TokenResponse` is bypassed because the handler returns a `Response`, which is
why ui-kit's `loginUser` must `unwrapEnvelope`; a client wiring this **must store the returned
token** or the next request 401s. Passwords must never reach `input_payload` or the emitted
`auth.password.changed` event (both trace-logged) — asserted by test. `MIN_PASSWORD_LENGTH` is
change-only; `register_user` still has no policy (deliberate, tracked). **Items 2+3 (forgot/reset) OPEN — app team answered 2026-08-01: option (a), the runtime sends the mail.** Sub-questions settled and agreed: stateless signed token carrying `user_id`+`token_version` (single-use falls out by construction — the reset bumps the version so a consumed token stops verifying; no table, no migration), TTL 30–60min, `/forgot` always 200 (enumeration oracle), rate limit 3/min per IP **and** per email. **Remaining blocker is structural, not a preference:** `register_connector` is a hook for *apps*, and the runtime ships **no** `email` connector (verified — none registered anywhere under `AINDY/`), so "the runtime sends it" currently has nothing to send with. Pick one: (1) runtime ships its own SMTP sender routed through `authorized_external_call`; (2) dispatch an app-registered connector with `/forgot` 503 when absent — **inverts the split** and breaks "runtime boots clean without plugins"; (3) hybrid. **Recommend 1 or 3** — option 2 satisfies the letter of (a) while reintroducing the dependency (a) was chosen to avoid. **Sub-item CLOSED 2026-08-01:** `MIN_PASSWORD_LENGTH` now enforced on `register_user` too (400 under 8). The "zero migration cost" argument was app-specific and the objection was raised; owner overruled deliberately — a floor deferred indefinitely is not a floor. Unflagged and not configurable by design. Does NOT invalidate stored passwords or touch login; only new short-password registrations 400. Length check precedes the duplicate-email lookup, so short+taken returns 400 not 409. **Still open (adjacent): `/auth/register` returns 409 on duplicate email — an enumeration oracle, same class as the `/forgot` 200 rule.** Next available: **FR-14**. **FR-11/12/13 FILED 2026-08-06 (verified, not built):** **FR-11** `invoke_runtime_callback` has a **hardcoded 10s** budget (`runtime_callback_host.py:43`) that neither `registry.py` call site overrides and no env key exposes; invoked from scheduled jobs so it repeats, and hardest when the host is slowest. App team filed it as hardening, NOT a defect — cold-start only, 0 recurrences warm. **Their stated mechanism is wrong and the record says so:** `bootstrap_register` fires only for `runtime_agent_defaults` (`registry.py:410`), not a 16-app bootstrap; the real cost is a fresh subprocess `import_module` pulling an app's transitive graph. **FR-12** there is **no `register_agent`** — 8 `register_agent_*` hooks exist and every one registers *behaviour*, not identity; the roster is a hardcoded 7-spec `_SYSTEM_AGENTS` list at `startup.py:937`. Live data confirms the sharp half: 7 rows, all `agent_type='system'`, **`count(owner_user_id)=0`** — the per-user half of the schema has never been exercised. **FR-13** `agents` has exactly 8 columns and **no JSONB / no `updated_at`**; wants `metadata` so identity (role) can outlive the vendor client. **Build cost:** touches `AINDY/db/models/` → schema-contract protocol + Alembic revision + `RUNTIME_ALEMBIC_HEAD_REVISION` bump; additive/nullable so no `reconcile_backfill` needed (unlike FR-8). **FR-8/9/10 confirmed CLOSED app-side on 2.0.1.** **Their FR-7 status is STALE — flag it:** all four defects shipped in 2.0.0 and are in source (`_policy_base_significance`, `normalize_for_dedup`, `_forced_capture_suppressed`, `blend_impact_with_significance`); they run 2.0.1, so only the doc is behind. **FR-5 (NEW 2026-07-18,
verified real):** native `run_nodus_workflow` `.nd` couldn't reach app callables via either VM surface. **(a) SHIPPED**: `run_nodus_workflow` gains `capability_token`+`run_id` params threaded to flow state as `execution_token`/`agent_run_id` (the agent path's proven keys) → `call_tool` seam; token binds run_id+user_id (both required). **(b) SHIPPED 2026-07-18 — corrected diagnosis**: handoff said "make `dispatch_syscall` resolve app-registered syscalls"; verify-first found apps DO register into `SYSCALL_REGISTRY` (kernel `register_syscall` at `syscall_registry.py:1813`, with real caps) which `dispatch` consults — the real bug was a **subprocess plugin-load gap** (sibling to PLANNER-SUBPROC-1): the worker's `sys()` seam had no plugin-load entry point (unlike `call_tool`→`execute_tool`→`_ensure_tools_loaded`), so `sys()`-only workflows dispatched against an unpopulated registry → "Unknown syscall". Fix: `nodus_worker.dispatch_worker_syscall` runs `_ensure_tools_loaded()` before dispatch (lazy/idempotent); capability enforcement unchanged (app syscall keeps its cap, granted via `_infer_dispatch_capability`). Contract `NODUS_WORKFLOW_CONTRACT.md` §8.1. **FR-8/9/10 (NEW 2026-08-03, all SHIPPED 2026-08-05, all verified-real) — the 2.0.0 upgrade trio, and they gate a 2.0.1.** Found by the app team upgrading a live deployment to the release we'd just cut. **Common thread: each is invisible from a source checkout AND from CI** — a wheel has no `alembic/` tree, Compose writes `""` where a shell writes nothing, and an app-registered connector type is a shape our tests never see. **FR-10 (#360):** `"${VAR:-}"` renders as `""` → pydantic bool_parsing → and since `settings = Settings()` runs at **module import**, the container restart-loops before serving (27 restarts). Fix: `env_ignore_empty=True`. 28 typed bools were exposed. **FR-8 (#361):** Alembic `0014` grandfathers pre-existing users to verified, but `alembic/` **isn't in the wheel** and `reconcile_runtime_schema` is purely structural → wheel installs left every account unverified = latent lockout when an operator trusts our upgrade notes and flips `AINDY_REQUIRE_VERIFIED_LOGIN`. Fix: columns declare `info={"reconcile_backfill": "<sql>"}`; reconcile `UPDATE`s right after `ADD COLUMN` (no WHERE — the column didn't exist a moment before, so every present row predates it). Tripped the schema-contract protocol (`orm_hash` = content hash of `AINDY/db/models/`) → version `2026-08-05`, though **no DDL changed**. **FR-9 (#362):** runtime transactional mail dispatched to the **`email`** type apps register for automations, in a different undocumented shape; with the (correct) no-fallback rule that meant `/auth/register` 202 + **no account can complete signup**. Fix: reserved **`transactional_email`** type; shape published in `CONNECTOR_CONTRACT.md` §5a; failure now logs ERROR. **Three gotchas worth keeping:** (1) SQLAlchemy renders `server_default="false"` as `DEFAULT 'false'` — a *quoted string*; PG casts it to boolean false but **sqlite stores the characters and reads back truthy**, so a sqlite bool assertion tests type affinity, not your code. (2) `0014`'s re-run guard `created_at < now()` evaluates `now()` at execution time, so a re-run matches **every** row — the comment overstates the protection (flagged, not edited). (3) `send_email`'s "never raises" is **inherited** from `dispatch_connector` normalising handler exceptions, not enforced — mocking the dispatcher tests nothing. Next available: **FR-11**. Source: `TECH_DEBT.md` APP-FR-*.
- **RT-MEMTXN-LEAK-1** — FIXED IN THREE PARTS (app handoff, HIGH — login took ~40s, exceeded the web client's 30s timeout). **Part 3 (2026-07-19) is the actual root cause: an unbounded *synchronous* cascade** — `submit_async_job` (opens its own `SessionLocal()`) → emits `EXECUTION_STARTED` → `capture_system_event_as_memory` → `MemoryNodeDAO.save` → `_enqueue_embedding` → `submit_async_job`. Every memory node spawns an async job whose lifecycle event becomes another memory node; each level holds the session it opened until the descent below it returns, so depth is capped only by the connection pool — 60 conns each holding **one** `SELECT … memory_nodes WHERE id=…` (the `save()` refresh), then a full `pool_timeout` wait for everything after → ~42s login. Fixed on three axes: **(1)** the runtime's own maintenance jobs (`memory.generate_embedding`, `memory.embedding_sweep`, listed in `RUNTIME_INTERNAL_TASK_NAMES`) are no longer captured as memory — cuts the cycle at its origin; **(2)** new `AINDY/core/memory_capture_guard.py` — `submit_async_job` runs inside `async_submit_scope()` and captures are suppressed at submission depth ≥ 2 (outermost still captures, so INFINITY-RUNTIME-1 loop-closure signal survives), with `fresh_async_submit_depth()` resetting at the `_execute_job` thread boundary because worker threads inherit the submitter's context via `copy_context()`; **(3)** `_is_duplicate`'s `WHERE user_id = :uid` is never true when uid is NULL, so the *global* nodes this cascade produced (byte-identical content) were never deduped — now branches to `user_id IS NULL` (branch, not `IS NOT DISTINCT FROM`: unsupported on SQLite, untyped NULL bind on PG). Verified live: **login 43.6s → 0.3s, 60 held conns → 0.** **Rule: a memory capture must never be able to enqueue work whose own lifecycle events are capturable — any capture → job → capture edge is a cycle.** **Diagnostic: `xact_age_s == idle_s` means one statement per transaction, which is equally consistent with "held across a slow call" (parts 1–2) and "held by a frame that never returned" (part 3); only a stack dump separates them — `docker exec --privileged -u root <api> py-spy dump --pid 1` (needs `--privileged`, the container lacks `CAP_SYS_PTRACE`).** Parts 1–2 below were both real transaction-hold bugs found en route, and stay fixed. **Part 2 (2026-07-19), the embedding-write fan-out:** app-verified that v1.10.0's recall fix was only *partial* (it stopped post-request lingering); 30+ **concurrent** connections still each ran ONE `SELECT memory_nodes` then sat idle (`xact_age_s == idle_s`). Cause: in `embedding_jobs.process_embedding_job`, `queue_system_event(required=True)` commits → **expires `memory_node`** → reading `.content` fires a **refresh SELECT** opening a fresh transaction → `generate_embedding()` ran with it open; one job **per captured memory**, each on its own session → pool exhaustion. Fix: capture `node_content`, `db.commit()` to release the connection, then embed. **Gotcha to remember: after a commit, touching an ORM attribute silently re-opens a transaction (expire_on_commit) — never let a slow external call follow such an access.** **Part 1 (v1.10.0), the recall read path:** `MemoryNodeDAO.recall` held a DB connection `idle in transaction` across the synchronous `generate_query_embedding` LLM/embedding API call (~seconds); under a browser login's concurrent fan-out ~60–85 connections piled up and exhausted the pool. Fix (reorder, not rollback): `MemoryNodeDAO.recall` generates the embedding BEFORE any DB query (`_count_complete_embeddings` moved after) so the session holds no connection during the API call; the 2 other embed sites already embed-first. **Rejected a first cut that rolled back the request-shared session (`release_read_transaction`) — it broke `test_agent_approve_idempotency` because `session.dirty` doesn't see Core `db.execute(UPDATE)` / outer transactions; rolling back a shared session mid-request discards in-flight state.** **Rule: never hold an open DB transaction on a request-shared session across a slow external call (embedding/LLM/HTTP) — order the code so the external call precedes the DB work; never `rollback()` a shared session to free its connection.** See `TECH_DEBT.md`.
- **DEPLOY-TARGET-1** — Cloud deployment manifests (Railway/Render/Fly.io) not yet authored. **Open**, trigger: first cloud deployment.
- **DEPLOY-TARGET-2** — Multi-tenant SaaS readiness gate. **Open**, trigger: first multi-tenant operator.
- **DEP-UPGRADE-DEFERRED-1** — Deferred deliberate dependency upgrades. **OTel half CLOSED 2026-08-01: bumped to 1.44.0** (`api`/`sdk`/`exporter-otlp-proto-grpc` 1.44.0 + `instrumentation-fastapi` 0.65b0; the other six otel packages are unpinned and resolve transitively). **Two lessons:** (1) the otel packages are version-locked, so single-package PRs always die `ResolutionImpossible` (#251/#254/#307 all closed for it) — a `groups` config for `opentelemetry-*` now forces them into one PR; (2) **grouping is necessary but NOT sufficient** — dependabot's own grouped PR (#325) still failed because it resolved each package independently and picked `api==1.43.0` against `sdk==1.44.0`. Grouping controls *which PR*, not *which versions*; expect to hand-align the set and verify with `pip install --dry-run` before pushing. **UI major unit CLOSED 2026-08-03 (#349):** vite 6→8 + `@vitejs/plugin-react` 4→6 + tailwind 3→4 as ONE commit (plugin-react 6 peer-locks to vite ^8; #298/#308/#310 auto-closed). Its blocker was never "breaking UI major" (the old, wrong diagnosis) — it was `LOCKFILE-PLATFORM-1`, and once the resolver existed the code side went green first try. **Tailwind 4 load-bearing bits:** `@config "../tailwind.config.js"` is required (v4 is CSS-first and otherwise ignores `tailwind.config.js` **entirely** — theme colours, `darkMode:["class"]`, and the content glob scanning `@aindy/ui-kit/dist` without which every kit-only class is purged); the PostCSS plugin moved to `@tailwindcss/postcss`; autoprefixer dropped (v4 prefixes itself); **no border shim needed** despite v4's `gray-200`→`currentColor` default change against 237 bare `border` uses, because `platform.css` already has shadcn's `* { @apply border-border }`. react-router 6→7 **landed alone 2026-08-02 (#345)** once `@aindy/ui-kit@2.0.0` widened its `^6.0.0` peer pin. **react-router 7→8 deferred; dependabot alert #17 (high, RSC CSRF) dismissed `not_used` 2026-08-03** — advisory affects only the unstable RSC APIs and the SPA is client-side only (zero hits for `unstable_`/`rsc`/`createCallServer`/`RSCHydratedRouter` in `platform/src` **or** ui-kit); the fix is react-router 8, another major, and ui-kit peers `^6||^7`, so it needs a ui-kit release first. Re-assess if the SPA ever adopts RSC/SSR. Source: `TECH_DEBT.md` DEP-UPGRADE-DEFERRED-1.
- **LOCKFILE-PLATFORM-1** — **Open (2026-08-02); resolver shipped, and the UI unit it blocked has landed.** Stays open because the npm behaviour is permanent: every future rolldown/oxide bump needs the same treatment. A Windows-generated `platform/package-lock.json` cannot satisfy Linux `npm ci`: vite 8 (**rolldown**) and tailwind 4 (**oxide**) ship platform-specific native bindings, and the missing `@emnapi/*` packages are **`bundleDependencies` of `@tailwindcss/oxide-wasm32-wasi`** (itself `optional`, `cpu:["wasm32"]`) — *corrected 2026-08-03; the first write-up said "transitive deps npm prunes", right shape, wrong mechanism*. A machine that never installs the wasm32 variant never walks that subtree, so its lock omits what the resolving platform demands: complete here, `Missing: @emnapi/runtime@1.11.3 from lock file` in CI. The mechanism matters because the fix follows from it — no flag or cleaner regenerate can reach a bundled dep. **Not fixable from this machine** — `npm install`, `--package-lock-only`, a from-scratch regenerate, and npm 11's `--os/--cpu/--libc` targeting all yield the same tree; a Linux container over the *mounted* dir is worse (it reconciles against the Windows `node_modules` and produces a win32-only lock). Contrast **esbuild**, which declares all 26 platform variants as *explicit optional* deps so npm records every one — the question is never "are the other platforms' packages in the lock" but "are their **transitive** deps". **Fix SHIPPED 2026-08-02: `.github/workflows/platform-lockfile.yml`** (`Platform Lockfile`) — `workflow_dispatch` with inputs `ref` (branch to resolve) + `push` (commit back, default off → artifact `platform-package-lock`). Runs `npm install` on `ubuntu-latest`, then **verifies with `rm -rf node_modules && npm ci` + `npm run build`** (that verify step is the point — without it the job repeats the mistake it exists to prevent). Deliberate: dispatch-only (it's a tool, not a gate — so it must be on the **default branch** before it can run against any ref, i.e. it can't be exercised on its own PR); incremental `npm install` not from-scratch (keeps the diff reviewable); **no `cache: npm`** (setup-node keys the cache off the lockfile being regenerated). Linux is authoritative — and **measured 2026-08-03, the win32 caveat does NOT bite**: `npm ci` on Windows against the Linux-resolved lock succeeds (112 pkgs, exit 0, lock untouched), because rolldown/oxide declare all platform bindings as *explicit optional* deps like esbuild, so the Linux resolution records the win32 variants too. **Smoke-verified green on `main` 2026-08-03** (run `30815998362`); **first real use the same day (#349, run `30817776715`, `push:true`)** added **35 packages** a Windows lock never records — all 15 `@rolldown/binding-*` + all 12 `@tailwindcss/oxide-*` + `@tailwindcss/node`/`postcss`, `enhanced-resolve`, `detect-libc` — net `+937/−2181`, and the PR passed `Platform UI Build` first try. **Expect a benign `"peer": true` diff — don't chase it:** that run reported `LOCK_CHANGED=true` on an untouched `main` and the whole diff was 16 lines of `"peer": true` flags, zero package/version changes. Cause was npm-major skew — dev node 24/npm 11 (writes the flag) vs workflows on node 20/npm 10 (don't). **Skew CLOSED 2026-08-05:** all four workflows → **node 24** + repo-root **`.nvmrc`**, so both ends resolve identically. Second and sharper reason it was overdue: **node 20 hit EOL 2026-04-30** (`nodejs/Release/schedule.json`) — CI had been on an unsupported runtime for three months; 24 over 22 because 22 leaves the dev-vs-CI mismatch that was the actual problem (EOLs: v22 2027-04-30, v24 2028-04-30). `platform/package.json` still declares no `engines` — adding one would make the floor enforceable rather than advisory. Regardless: read the *"Packages added"* output, not the changed/unchanged bit. **Process rule: verify a lockfile change with `npm ci`, never `npm install` + `npm run build`** — `install` silently repairs a mismatch and a build keeps passing off a populated `node_modules`. Source: `TECH_DEBT.md` LOCKFILE-PLATFORM-1.
- **DB-NODUS-BUDGET-1** — **Open (2026-07-31), half verified.** **Verified:** the default nodus wall clock is `30s` script (`nodus_runtime_adapter.py:29`) **+ `15s`** boot allowance (`:30`) = **45s** outer `subprocess.run(timeout=)`, while the production `idle_in_transaction_session_timeout` default is **30s** (`config.py:283`) — so an entirely in-budget nodus run has a 15s window where PG can kill its connection. Compounding: `SessionLocal` (`database.py:77`) has **no `expire_on_commit=False`**, so a post-commit ORM attribute touch silently re-opens a transaction (the RT-MEMTXN-LEAK-1 Part 2 gotcha). **VERIFIED 2026-08-01 against real PG — it IS a real exposure:** the flow runner's own session holds an open, idle transaction for the **entire** duration of node execution (`xact_age_s == idle_s`, tracked 4.12s → 20.60s across a 20s node, one backend). Trigger is a **`memory_nodes` SELECT** on the node path — NOT a `run.*` attribute touch; `expire_on_commit` compounds but isn't the cause. It survived 20.6s idle, which proves the 30s prod cap (not the 10s test cap) was in force. So a slow-but-in-budget nodus run gets its connection killed at 30s. **BOTH FIXES SHIPPED 2026-08-01.** (1) *Ordering guard, active:* `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` default **30000→60000** (clears the 45s ceiling + 15s headroom); `tests/unit/test_db_nodus_budget_ordering.py` derives the ceiling from the adapter constants, so raising a nodus budget without raising the cap fails CI, not prod. (2) *Root cause, opt-in `AINDY_MEMORY_RECALL_OWN_SESSION` (default off):* `MemoryOrchestrator.get_context` takes a dedicated short-lived read session (`_resolve_read_session`, closed in `finally`) so the read-only `memory_nodes` SELECT never opens a transaction on the caller's session; falls back to the caller's session on any failure. **Do NOT "fix" this by rolling back the caller's session** — RT-MEMTXN-LEAK-1 tried that (`release_read_transaction`) and it broke `test_agent_approve_idempotency` (`session.dirty` can't see Core `db.execute(UPDATE)`/outer transactions). Remaining: soak, then flip the flag. See TECH_DEBT.md.
- **NATIVE-CI-1** — **CLOSED 2026-08-02.** A `Native Crate Build (Rust)` job in `runtime-ci.yml` now runs `cargo build --locked --release` on the pyo3 scorer every PR, so cargo bumps are no longer green-but-unverified (previously nothing in CI compiled the crate at all). **Gotchas encoded there:** `--locked` is the point — it proves the committed `Cargo.lock` is the one that builds; **no `cargo test`** because pyo3's `extension-module` omits libpython so a test harness fails to *link* (and the crate has no tests anyway); **not path-filtered on purpose** — a `paths:` filter on a required check never reports on unrelated PRs and would block them forever; added to `runtime-ci.yml` rather than a new workflow file because **a new workflow file doesn't trigger on the PR that adds it**. Covers the C++ half too (`build.rs` compiles `memory_cpp/` via `cc`, itself a bumped package). **Now a required status check on `main`** (alongside Runtime Lint / Docs Validation / Contracts). **Remaining:** builds on Linux, not MSVC — catches API-breaking dep changes, would miss an MSVC-only compile error; add a Windows matrix leg if that bites.
- **MEM-RECALL-N1-1** — `MemoryNodeDAO.recall()`'s scoring loop is N+1: **3 queries per candidate** — `_get_model_by_id` (1 × `memory_nodes`) + `get_graph_connectivity_score` (2 × `memory_links` COUNT), over up to `limit*3` semantic + `limit*3` tag candidates. Benign next to RT-MEMTXN-LEAK-1 (one session, indexed lookups, no external call in the loop) but unnecessary: the re-fetch exists only to read `success_count`/`failure_count`/`usage_count`/`weight`, which the originating SELECT already read and `_node_to_dict` then drops. Fix: carry those 4 columns in the candidate dict (kills the re-fetch outright) + batch the link counts with one `GROUP BY`. **Open**, performance-only. Source: `TECH_DEBT.md` MEM-RECALL-N1-1.
- **SYSMAX-1** — Thread-mode 100-job hard cap still `.env.example` default (partial mitigation: prod overlay enforces distributed). **Open.**
- **SYSMAX-3** — Memory bytes not enforced per EU (requires OS integration). Deferred.
- **SYSMAX-4** — Per-EU syscall (100) and wall-time (5 min) caps advisory; tunable via env. Document in Nodus guide.
- **BILLING-\*** — BILLING-1 through BILLING-5: billing identity, metering, plan enforcement, acquisition funnel, usage reporting. All open, deferred until commercial launch. Source: `docs/runtime/MONETIZATION_AUDIT.md`.
- **LAYER-\*** — Layer boundary violations (LAYER-1 through LAYER-5). All deferred. See TECH_DEBT.md.
- **TIER3-10** — `async_job_service` coupling. Open — architectural, no bounded fix.
- **REPLAY-1** — CLOSED 2026-06-11: `AINDY/kernel/clock.py` — ContextVar `utcnow()` + `frozen_at()`. 12 sites updated across kernel/core/flow engine. 12 tests in `test_clock.py`.
- **MEM-DELETE-1** — Core SHIPPED 2026-07-11: `sys.v1.memory.delete` (cap + dedicated `memory.delete` scope, NOT granted by `memory.write`) — hard, syscall-only, node-id, tenant-scoped, idempotent, DB `ON DELETE CASCADE` (history/traces/edges/links), irreversible; bumped `SYSCALL_REGISTRY_MIN_COUNT`→23 + `_STABLE_SYSCALLS`. Verified real-PG (isolation+cascade+idempotency). Four opt-in upgrades DEFERRED (see TECH_DEBT MEM-DELETE-1 G1–G4): REST route, `MEMORY_DELETED` audit event (frozen-hash gotcha), bulk/MAS-path delete, soft-delete (schema bump + ~8 read-site filters). SDK `client.memory.delete` is the consumer.
- **MEM-NODETYPE-1** — CLOSED 2026-06-27: `memory.write` defaulted `node_type="execution"`, which `VALID_NODE_TYPES` rejects → every default write raised `ValueError`, blocking the `runtime_local` execute loop. Fixed in two passes — PR #98 (syscall handler + `nodus_builtins.py`), then execute-to-completion verification on Postgres exposed 6 more sites in the **deferred** path the flow engine actually runs (`nodus_worker.py`, `nodus_runtime_adapter.py`, `nodus/runtime/memory_bridge.py` `remember`, extension ABI) where the rejected save was **silently swallowed**. All 8 → `"insight"`; tree-wide sweep clean. `memory_persistence.py` untouched so no schema-contract bump. Distinct from ECOGAP-1 (kernel replay log). Tests: `test_mem_nodetype_default.py` (unit) + `test_planner_loop_execute_to_completion.py` (integration, real PG).
- **LEASE-1** — CLOSED 2026-06-24: `lease-elected` background leadership is now enforced via an atomic `background_task_leases` lease + `BackgroundLeadershipElector` (`AINDY/platform_layer/leadership.py`). Distributed profiles elect exactly one scheduler with failover; single-instance keeps the local-boolean guard. Was advertised-but-unimplemented (every replica self-elected).
- **PLANNER-SUBPROC-1** — CLOSED 2026-06-27 (1.4.3): the agent planner 500'd on Linux/Docker because first-party run-tool / planner-context providers were routed through `_maybe_wrap_runtime_callback`'s isolated subprocess, which (cwd=site-packages) couldn't `load_plugins()` → zero tools. These two registry-state-dependent surfaces now run in-process via `_STATEFUL_IN_PROCESS_CALLBACK_SURFACES` in `registry.py`; self-contained surfaces stay isolated. Masked on Windows (manifest resolves there). Remaining gap: app trigger evaluators still isolated. Verified with a `python:3.11-slim` non-editable repro. Tests: `test_extension_ownership.py`.

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
| Runtime Alembic head constant + stamp helper | `AINDY/db/alembic_head.py` — `RUNTIME_ALEMBIC_HEAD_REVISION`, `stamp_runtime_alembic_head()` |
| `bootstrap-schema` CLI command | `AINDY/runtime_only.py` — `_bootstrap_schema()` |
| Idempotency contract | `docs/runtime/IDEMPOTENCY_CONTRACT.md` |
| Nodus developer guide (scripts + builtins) | `docs/runtime/NODUS_DEVELOPER_GUIDE.md` |
| Syscall API reference (all registered calls) | `docs/runtime/SYSCALL_REFERENCE.md` |
| Connector registration hook (FR-1) | `AINDY/platform_layer/registry.py` — `register_connector`; dispatch in `connector_service.py` |
| Authorized outbound boundary (FR-1) | `AINDY/platform_layer/external_call_service.py` — `authorized_external_call`; client `outbound_http.py` |
| Connector + outbound contract (FR-1) | `docs/runtime/CONNECTOR_CONTRACT.md` |
| 90-day hardening checklist | `AINDY_RUNTIME_90_DAY_CHECKLIST.md` |
| Runtime module map (tagged inventory) | `docs/runtime/RUNTIME_MODULE_MAP.md` |
| Runtime execution invariants | `docs/runtime/EXECUTION_INVARIANTS.md` |
| Architecture risk (complexity/blast-radius) | `docs/runtime/ARCHITECTURE_RISK.md` |
| Runtime security matrix | `docs/runtime/SECURITY_MATRIX.md` |
| Cross-repo compatibility policy | `docs/runtime/CROSS_REPO_COMPATIBILITY.md` |
| Runtime → SDK contract | `docs/runtime/SDK_CONTRACT.md` |
| Runtime → UI contract | `docs/runtime/UI_CONTRACT.md` |
| Latest app-team handoff (v2.0.0) | `docs/runtime/APP_HANDOFF_v2.0.0.md` |
| Release verification checklist | `docs/runtime/RELEASE_CHECKLIST.md` |
| Cross-repo regression tests | `tests/unit/test_cross_repo_compatibility.py` |
| Syscall registry floor constant | `AINDY/kernel/syscall_registry.py` — `SYSCALL_REGISTRY_MIN_COUNT` |
| Tech debt tracker | `TECH_DEBT.md` |
| Roadmap reading aid (digest of `TECH_DEBT.md`; NOT the source of truth) | `RTR.md` |
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
| Cloud deployment targets + readiness | `docs/runtime/DEPLOYMENT_TARGETS.md` |
| Monetization and billing architecture audit | `docs/runtime/MONETIZATION_AUDIT.md` |
| Route ownership inventory | `docs/runtime/ROUTE_OWNERSHIP_INVENTORY.md` |
| nginx plain HTTP config | `nginx/nginx.conf` |
| nginx TLS config (Let's Encrypt) | `nginx/nginx.tls.conf` |
| Compose production port override | `docker-compose.prod.yml` |
| Apps monolith project instructions | `C:\dev\aindy-apps-monolith\CLAUDE.md` |
| Live stack verification scope (runtime + apps UI) | `C:\dev\aindy-apps-monolith\LIVE_VERIFICATION_SCOPE.md` |
