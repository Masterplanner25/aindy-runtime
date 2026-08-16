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

Orientation only — the tagged, per-file inventory is `docs/runtime/RUNTIME_MODULE_MAP.md`.

| `AINDY/` | Holds |
|---|---|
| `kernel/` | SyscallDispatcher, SyscallRegistry, EventBus, SchedulerEngine, CircuitBreaker, ResourceManager, TenantContext |
| `platform_layer/` | LLM clients, metrics, OTel, rate limiter, cache, extension ABI + sandbox runner, scheduler_service |
| `core/` | Execution pipeline middleware, RetryPolicy, DistributedQueue, SystemEventService, ResumeWatchdog |
| `runtime/` | Flow engine (DAG executor), Nodus script execution, memory loop, flow definitions |
| `agents/` | Agent runtime, planner backends, tool registry, AgentCoordinator, AutonomousController |
| `memory/` | MemoryNode persistence, MemoryAddressSpace (MAS), embedding pipeline, scoring, traces |
| `db/` | SQLAlchemy models, Alembic env, DAO layer, schema contract |
| `routes/` | FastAPI routers — auth, flows, agents, memory, platform/* |
| `worker/` | Background worker processes (memory ingestion, metric writing) |
| `nodus/` | Nodus language stdlib (`memory.nd`) and runtime adapter |

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
- Current chain: `0001` → … → `0010` → `0011` (effect-record attribution, MEB) → `0012`
  (flow-history sequence, DUR) → `0013` (nodus misfire policy, ECOGAP-5) → `0014`
  (`0014_users_email_verification`, FR-6/FR-8) → `0015` (`0015_agents_metadata`, FR-13) →
  **`0016`** (`0016_agents_owner_scoped_name`, FR-12b). *Corrected 2026-08-14: this line said the
  chain ended at `0010` — four migrations stale, in the section you read before writing a
  new one. `AINDY/db/alembic_head.py` is authoritative and is CI-enforced by
  `tests/unit/test_runtime_alembic_head.py`; prefer it over this line.*

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

## ★ CHANGELOG protocol — write the entry in the PR that makes the change

**A PR that changes behaviour, API surface, configuration, schema, or what CI proves writes its own changelog entry in the same PR.** Not at release time.

**★ Since 2026-08-16 the entry is a NEW FILE in `changelog.d/`, not an edit to `CHANGELOG.md`.** Create `changelog.d/<PR>-<slug>.md` containing exactly what you would have written under `## Unreleased`; prefix `00-` if an operator must read it before upgrading (those sort to the top). `python scripts/assemble_changelog.py` folds them in at release. **Do not hand-edit `## Unreleased`.**

*Why the location moved: editing one shared section made every concurrent PR collide — three times in one afternoon (#449/#450/#451) — and the failure mode was worse than the annoyance. The reflexive "keep mine" resolution **silently reverted another PR's entry**, and a dropped changelog paragraph breaks no build. A new file cannot conflict with another new file. The rule below is unchanged; only where you put it moved.*

**This is measured, not a style preference** — across six release windows the CHANGELOG was
written in bursts at release time, worst case **1 of 50 commits** (`v2.0.1..main`), leaving the
file stale for most of every cycle. **Why deferring costs more than it saves:** reconstructing
50 commits later means writing from commit *subjects*, and the reasoning is not in the subject
— "unique per owner" survives, *"a plain `UNIQUE (owner_user_id, name)` would not be equivalent,
because SQL treats NULLs as distinct"* does not. The entries worth having decay fastest.
(`PYPI-PUBLISH-1`'s "bump the pin and the CHANGELOG in one PR" is the *verification* step at
release, not the place to author 50 entries.)

**What needs an entry:** new or changed routes, syscalls, env vars, config defaults, response
shapes, schema/migrations, behaviour changes (including "this used to return 500"), removed
surfaces, dependency bumps with consumer impact, and **test/CI changes that alter what a green
check means** (`CI-MARKER-1` changed which tests run at all — that belongs in the log).

**What does not:** pure refactors with no observable change, doc-only edits, and tests that add
coverage without changing what CI enforces. When unsure, write it — an over-documented change
costs a paragraph; an undocumented behaviour change costs an operator an incident.

**Format** — match the file. `### Added|Changed|Fixed|Removed — <short title> (#PR)`, then
bullets that say what changed, and *why it was wrong* where that is not obvious. Call out
anything an operator must read before upgrading at the top of `Unreleased`, not buried in a
bullet.

**Never rewrite a published entry.** Older entries are the audit trail of what was believed
then. Correct them with a new dated entry that says what the earlier one over-reached on — as
the `AINDY_REDIS_URL` entry does for its 2026-06-06 predecessor.

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

**★ ALL TEN checks are required as of 2026-08-14** (was four; `strict: true`, so a branch must
also be up to date before merge). The other six previously ran without blocking — verification
that exists but does not enforce, the same shape as `DOCS-COVERAGE-CLAIM-1` / `CI-MARKER-1`.

| Check | Workflow | What it guards |
|---|---|---|
| `Runtime Lint` | `runtime-ci.yml` | ruff |
| `Runtime Docs Validation` | `runtime-ci.yml` | `docs/runtime/` frontmatter + `last_verified` floor |
| `Runtime Contracts` | `runtime-ci.yml` | `pytest tests -m runtime_only`, schema contract, **native crate build** |
| `Native Crate Build (Rust)` | `runtime-ci.yml` | `cargo build --locked --release` (NATIVE-CI-1) |
| `Integration Tests (PostgreSQL + Redis)` | `runtime-ci.yml` | `pytest -c pytest.integration.ini` on live PG + Redis |
| `Platform UI Build` | `runtime-ci.yml` | `npm ci` + SPA build (LOCKFILE-PLATFORM-1) |
| `Runtime Package Build` | `runtime-ci.yml` | sdist + wheel |
| `Install Smoke Test` | `runtime-ci.yml` | wheel installs and imports |
| `pip-audit (OSV)` | `security-audit.yml` | dependency CVEs |
| `Boot Smoke — Linux / Python 3.11` | `smoke-postgres.yml` | published wheel boots against real PG |

**Before adding an eleventh, check what made these ten safe:** no `paths:` filter (the classic
trap — a filtered check never reports on unrelated PRs and blocks them forever), no job-level
`if:`, and `Boot Smoke` guards its steps *individually* so a version-bump PR reports green
rather than hanging pending.

**Consequence to expect:** with `strict: true`, every PR needs a rebase when `main` moves, and
`Integration Tests` (~6–7 min) sets merge latency. Combining dependency bumps into one PR is the
normal move, not an optimization.

---

## ★ Trusting a green check — read this before citing CI as evidence

**Eight separate times** this repo has shipped something that *looked* covered and was not.
Assume there will be a ninth — the catalogue exists so you can recognise the shape, and the
rules below are what it cost to learn:

| # | Variant | How it looked green | Entry |
|---|---|---|---|
| 1 | Claimed and absent | 6 docs cited 8 test files that never existed | `DOCS-COVERAGE-CLAIM-1` |
| 2 | Exists, not collected | 268 unit tests in 24 unmarked files ran in no job | `CI-MARKER-1` |
| 3 | Collected, skipped | native suite skipped — nothing built the crate; a skip reads green | `NATIVE-CI-1` |
| 4 | Runs, doesn't gate | 6 checks could be red without blocking | branch protection |
| 5 | Gates, doesn't cover | Integration Tests never executed `EventBus.publish()` | `EVENTBUS-COVERAGE-1` |
| 6 | Covers, asserts nothing | a test asserting an *absence* passes when the wire is broken | `EVENTBUS-COVERAGE-1` |
| 7 | Asserts the source, not the behaviour | route tests read the handler as *text*, never called it — 500 instead of 409 for a day | `ROUTE-GUARD-1` |
| 8 | Verification that never runs | the boot-time route AST proof has no call site in the app | `ROUTE-AST-UNWIRED-1` |

Variants 2 and 3 are fixed at the mechanism level (`tests/unit/conftest.py` defaults the marker;
`AINDY_REQUIRE_NATIVE_BRIDGE=1` turns a skip into a failure) — but both stay listed, because the
failure mode is general and only those two paths are immune. Variant 8 is the first found in a
runtime mechanism rather than a test.

**Rules that follow:**

- **Before citing a check as evidence for a change, confirm it executes that code.** The job
  name is not evidence. `Runtime Contracts` runs `pytest tests -m runtime_only`, *not*
  `tests/unit/`; `pytest.integration.ini` sets `testpaths = tests/integration`.
- **Mutation-test a new suite.** Break the thing it covers and confirm it fails, and how many
  tests fail. This is cheap and it is the only check that a test asserts anything: a first-draft
  wire suite scored 4/7, because the absence-assertion passed with the wire broken. **A test
  asserting an absence needs a liveness control** or it is vacuous by construction.
- **A new test file must be selected by some job.** Under `tests/unit/` this is now automatic —
  `tests/unit/conftest.py` applies `runtime_only` to every item that does not already carry it
  or a marker handing it to another job (CI-MARKER-1) — so write `pytestmark` for readability,
  not for safety. **Everywhere else the old rule still bites:** nothing marks a file outside
  `tests/unit/`, and `pytest.integration.ini` only reaches `tests/integration`. Adding a test
  directory means giving it a job, or its tests run nowhere.
- **When a test can legitimately skip, make skipping loud where it must not happen** — an
  env-gated assertion that fails in CI beats a silent skip.
- **★ A route test must call the route.** Reading the handler's source proves the guard was
  written, not that the caller receives its answer — and the status code *is* the contract:
  a client cannot tell "rejected" from "the server broke" by a 500. Source assertions are fine
  as a *supplement* (they catch a deleted guard cheaply); they are never the coverage.

**Chasing a flaky test:** never pipe the run through `tail`. Three observed failures of
`FLAKY-1` were run as `pytest ... -q | tail`, which discarded the traceback and kept the summary
— the evidence was destroyed at the moment it was produced, three times. Write to a file. And do
not conclude from small samples: that test produced **three** wrong readings (deterministic,
branch-caused, confined to `tests/unit/`) before the fourth run refuted each. Against a ~50% base
rate, four clean runs happen ~6% of the time by luck.

---

## `pytest.mark.integration` — skip hazard for Docker-only tests

`pytest.mark.integration` triggers a conftest guard that **skips the entire test when `DATABASE_URL` is not a live PostgreSQL URL**. This fires even in the default dev environment where `DATABASE_URL=sqlite:///:memory:`.

*Corrected 2026-08-15: this said the guard lives in `tests/conftest.py`. It is in **`tests/integration/conftest.py`** — but the warning below still holds, because that hook does **not** filter by path, and `pytest_collection_modifyitems` receives the whole session's items. So a `tests/sandbox` file marked `integration` is skipped by a conftest in a directory it has nothing to do with, which is exactly why the cause was hard to find. (The new `tests/unit/conftest.py` hook filters by path deliberately, for this reason.)*

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

## TECH_DEBT.md — maintenance convention

Entries are numbered sequentially within a prefix. **Do not reuse a number** — next available is
recorded on the prefix's line in the registry below.

When closing an entry, change `Status: Deferred — Low Priority` to `Status: CLOSED (YYYY-MM-DD)`
and replace the description with what was implemented and any remaining gap.

**Write the finding in `TECH_DEBT.md`, not here.** The registry section below is a one-line
index; it reached 104 KB — 68% of this file — by being used as the journal instead.

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

## Current phase + standing decisions (2026-08-15)

**Phase: runtime testing.** Things get connected to the runtime in order to exercise it — which
is why app-side feature requests keep arriving; they are a symptom of the testing method, not
scope creep. **Consequence: flag soak happens in `aindy-apps-monolith`, not here.** The runtime
ships capabilities default-off; the app repo turns them on and lives with them. Don't plan soak
work in this repo.

**Release state (verified 2026-08-15): `v2.1.0` is released and `main` is clean behind it.**
Dockerfile pin `2.1.0`, schema contract `2026-08-15.1`, Alembic head `0016`, `CHANGELOG.md`
`## Unreleased` empty. `recommended_runtime_requirement` stays `>=2.0,<3.0`, so no consumer pin
has to move.

> **This section has gone stale twice.** It once directed "the next release must be 2.0.0" after
> 2.0.0 had shipped, and then described 2.1.0 as unreleased after it was tagged. **If you are
> reading a release directive here, verify it against `git tag` and `CHANGELOG.md` before acting
> on it.** The release *protocol* — which does not go stale — is in the `PYPI-PUBLISH-1` line of
> the prefix registry and in `docs/runtime/RELEASE_CHECKLIST.md`.

**Standing decisions** (full record: `TECH_DEBT.md` → `DECISIONS-2026-08-01`):

- **FR-6 email delivery = hybrid** (registered `email` connector if present, else runtime SMTP).
- **`/auth/register`'s duplicate-email enumeration oracle is to be fixed, but it is NOT
  standalone work — it is a dependent of the FR-6 email decision.** Register returns an access
  token on success and a duplicate cannot be given one, so the responses must differ; no
  status-code or message choice closes the oracle while registration also authenticates. The
  real fix is the standard shape (neutral `202`, token only after an emailed verification link,
  duplicate gets a *"someone tried to register"* mail). **Second channel to remember: the
  duplicate path returns before `hash_password`, so it skips bcrypt and is measurably faster — a
  status-code-only fix leaves that timing oracle intact.** Build FR-6 first and fold this in.
- **The UI major cluster is decided from `C:\dev\aindy-ui-kit`**, not here.

---

## TECH_DEBT.md — prefix registry

**This is an index, not the record.** `TECH_DEBT.md` holds the full entry for every item here —
diagnosis, measurements, corrections, and the reasoning that decays fastest. It is consistently
2–5× longer than the line below it. **Read the `TECH_DEBT.md` entry before acting on any item;
these lines exist so you know the trap is there, not so you can skip the source.**

**Maintenance rule:** one line per item — status, the hook, the pointer. Detail goes in
`TECH_DEBT.md`. Do not reuse a number within a prefix. (This section was 104 KB — 68% of this
file — because findings were written where they were discovered instead of where they belong.)

### Open — P0

- **FR-15** — **dispatch into the execution pipeline is serialised through a 1-second `max_instances=1` APScheduler job.** App team measured a **177s** gap with zero events; they inferred a single slot and declined to claim the mechanism — there is one. `_scheduler_heartbeat_tick` is the *only* queue drainer; it calls `schedule()`, which runs each item **synchronously** because `_decide_mode` returns `INLINE`. **★ Rule 2 (`AINDY_ASYNC_HEAVY_EXECUTION`, default FALSE) short-circuits Rules 4 and 5** — so *"high-priority work should never block a request thread"* and the `{flow, agent, nodus, job}` async routing are **dead code by default**. Demonstrated: all 8 type×priority combinations return `INLINE` unset, `ASYNC` set. Another **built-and-not-wired** (cf. `ROUTE-AST-UNWIRED-1`, `IDEM-11`). **Predates 2.1.0.** **(c) SHIPPED 2026-08-15 (#442): `scheduler.queued` event + `aindy_scheduler_queue_wait_seconds` histogram — named `scheduler.` NOT the requested `execution.`, because the contract gate raises for `execution.*` outside a pipeline and the hottest enqueue callers have none.** **(b) SHIPPED 2026-08-16 (#443): wait firing has its own job AND its own executor** — the job split alone is probabilistic (`max_instances` is per-job, the pool is shared: 16 jobs vs a default pool of 10). ★ Severity was higher than filed — a **correctness** bug: `tick_time_waits` lived inside `schedule()`, so a flow parked on a timer stayed parked while an unrelated flow executed. Still open: **(a)** flip `AINDY_ASYNC_HEAVY_EXECUTION` (needs soak). Source: `TECH_DEBT.md` FR-15.
- **IDEM-11** — at-most-once is built and **shipped disabled**; the per-syscall audit is DONE 2026-08-15, the flag flip is not. Gate fires only when: `AINDY_SYSCALL_IDEMPOTENCY` on, the entry declares `EXACTLY_ONCE`, an EU id is present, and `_gate_scope_engaged`. **★ A fourth path the filing omits: `_durable` (DUR-2) engages the gate for ANY syscall, bypassing both the flag and the declaration** — so a durable continuation already dedups everything. **★ Two filed numbers were WRONG — corrected against source: the registry holds 23 entries, not 27 (`SYSCALL_REGISTRY_MIN_COUNT = 23`), and the one pre-existing `EXACTLY_ONCE` was `sys.v1.memory.write`, NOT `memory.delete`.** That inverts the significance: it was the busiest write path, not the syscall with zero callers. Audit outcome **1 → 7 declared**; the 6 added are `event.emit`, `flow.run`, `flow.execute_intent`, `nodus.execute`, `job.submit`, `agent.undo`. **★ `register_syscall` had NO `execution_guarantee` parameter at all** — `SyscallEntry` accepted it, the function never forwarded it, so every *plugin* syscall was `AT_LEAST_ONCE` with no way to opt in; the gate was unreachable for apps by construction. Now forwarded and validated (a typo raises rather than silently downgrading). **★ Prerequisite for the flip, now fixed: the gate caches the handler's return in JSONB and the syscall path had no serializability check** — the tool path (MEB-0) always had one. A `UUID`/`datetime` return unwound to `dispatch()`'s broad handler and came back as an **error envelope after the effect had already landed**. Guard ported; degrades like the tool path. **Remaining = flip the flag after soak.** Guard: `tests/unit/test_syscall_execution_guarantee.py` (10 tests, mutation-checked with a liveness control that proves the gate actually fires).
- **HTTP-SCOPE-GAP-1** — the capability model does not reach the runtime's own front door. **`Depends(enforce_api_key_scope)` on 7 of 147 route decorators**; everything else (all of `/memory/*`) depends only on `get_current_user` — identity, not authority. `memory_router.py` reaches effects with **zero** dispatcher references. **★ CLOSED (first half) 2026-08-16 (#449): a JWT no longer bypasses scopes.** A session carries `session_scopes` derived from `User.is_admin` **per request, not from a token claim** — so no session is invalidated (unlike 2.0.0's `purpose`) and a grant/revocation lands on the next call. Ordinary = `flow.read/execute`, `memory.read/write`, `agent.run`, `execution.read`; admin adds `webhook.manage`, `platform.admin`; **neither includes `memory.delete`/`event.emit`**. **Ships ENFORCING, not default-off, because the blast radius is countable: only 7 of 147 decorators enforce anything, and all three scopes they need are in the ordinary set — pinned by a source-scanning test so a future enforcement an ordinary user can't satisfy fails in CI, not in a browser.** Hatch: `AINDY_JWT_SCOPE_ENFORCEMENT=0`. **Still open: the 140 routes that enforce NOTHING**, and `memory_router.py` reaching effects with zero dispatcher references. Original decision: **DECIDED 2026-08-15 (owner): a JWT must NOT bypass scopes** — but a JWT carries no scopes today, so implementing that literally denies every session request. Decide where JWT authority comes from first (recommend: derive from the user row). **★ UNBLOCKED 2026-08-15 — the app team answered with their real call surface** (`RUNTIME_FEATURE_REQUESTS.md` → *Response to v2.1.0 §6*). Their UI is **two privilege classes sharing one JWT**, and the client already draws that line itself (`useAuth().isAdmin`, `<AdminAccessRequired />`) — **frontend-only today**, so deriving from the user row makes the server enforce a boundary the UI already draws rather than imposing a new model. Ordinary session: `memory.read/write`, `flow.read/execute`, `agent.run`, `execution.read`. Admin: those + `webhook.manage`, `platform.admin`. **Not needed at all: `memory.delete`, `event.emit`.** Two constraints they name: tie admin scopes to the **existing user-row admin flag** (two sources of truth for 'is this an operator' is worse than none), and **`execution.read` conflates scope with data ownership** — a scope cannot answer *"may I read someone else's"*, the same distinction `memory_agents_list` owner-scoping just hit. Roll out permissive→narrow, and **name the scopes in the handoff for the release that enforces them** or the UI fails as scattered 403s that read as a frontend bug.
- **TOOL-SEAM-ISOLATION-1** — every authority check at the tool seam is advisory with respect to the code that runs next: `execute_tool` evaluates token/tools/capabilities/policy/egress, then calls the tool **in-process with the live DB session**. Proposed `register_tool(..., isolation=...)`, failing closed, default unchanged.

> **These converge on one root, together with the now-closed `GUEST-CONFINE-1`:** `create_sandbox_runner` is reachable only from `plugin_host.py`. Three audits found it from three starting points — guest VM, execution unit, tool seam. It is **one provider re-homed and three call sites taught to ask**, not three fixes. `GUEST-CONFINE-1` is done and was deliberately taken first: it needed no new vocabulary, only the arguments the VM already accepted. The rest do need the vocabulary — start at `EXEC-ENV-BIND-1`.

### Open — P1

- **EXEC-ENV-BIND-1** — an execution unit cannot declare the environment it needs; `ExecutionUnit` has 22 columns and no confinement field. The descriptor's value is **accountability, not variation** — today "was this the containment you asked for?" has no answer for any given run. Keep separate from GUEST-CONFINE-1.
- **AUTHORITY-VALUE-1** — the syscall capability check reads a value the calling frame supplied; **`child_context()` could WIDEN — clamp shipped opt-in 2026-08-16 (#448)** behind `AINDY_CHILD_CONTEXT_CLAMP` (default off), with a WARNING on every widening either way so the exposure is countable. **★ The "two lines" estimate was wrong:** a repo-wide grep shows no caller under `AINDY/`, but `aindy-apps-monolith`'s `_dispatch_owner_syscall` grants the *nested* syscall's capability while the parent holds only the *outer* one — so clamping intersects to **EMPTY** and denies a working call. Flip only after that caller gets a legitimate grant. Not a vulnerability (every entry point has its own authorisation) — the point is the chokepoint is not an independent second gate. **Quiet bit: absent identity SKIPS the boundary rather than denying**, logged at `debug`.
- **CANCEL-REACH-1** — cancellation is durable but never reaches an in-flight effect; observed between segments only. **Constraint already paid for twice: `should_stop()` must not do a per-effect DB round-trip on the request-shared session** (RT-MEMTXN-LEAK-1, MEM-RECALL-N1-1).
- **FLOW-PARALLEL-1** — no fan-out/join/barrier; plan steps are strictly sequential, so independent calls cost the sum of their latencies. Apps needing parallelism route *around* the flow engine, losing history/retry/quota. **Determinism is the load-bearing part of any fix, not speed** — merge output patches in declaration order.
- **AUTHORITY-NEGOTIATION-1** — a denied capability terminates the step; approval is whole-plan, so recovery discards durable state. Keep any fix bounded, **downgrade-only**, recorded. Note `sys.v1.agent.simulate` already offers a better fallback than retrying with more authority.

### Open — P2 and below

- **ROUTE-AST-UNWIRED-1** — the boot-time route AST proof exists and **never runs against the application**; `routing.py` calls the request-time *wrapper*, a different function with a near-identical name. **The defect is the CLAIM, not the absence. Do NOT wire it as-is — by its own test it raises on a route that works today.**
- **QUEUE-DURABILITY-CLASS-1** — `_fallback_to_memory_backend` swaps durable Redis for in-memory with no per-job durability class. Lower severity than it reads: `AINDY_REQUIRE_REDIS` makes the fallback raise, and it already classifies `UNSAFE_DEGRADED`. Fold into the ownership contract.
- **ORCHESTRATOR-SPLIT-1** — durable work state lives in three stores with three recovery paths and no shared transaction. **Do (b) first: publish the ownership contract.** The split may be correct; it is undocumented, so it can be neither relied on nor reviewed.
- **AUDIT-CORRELATION-1** — three joins the audit trail cannot make. (1) and (2) fall out of AUTHORITY-VALUE-1 / EXEC-ENV-BIND-1; only `EffectRecord.action_id`→`SystemEvent` is standalone (joined by `trace_id` convention, no FK).
- **EGRESS-INPROC-1** — a re-homing, not a build. `egress_guard` is off by default and **its own docstring names both bypasses**. Fold the egress decision into TOOL-SEAM-ISOLATION-1's provider.
- **DISPATCH-ADMISSION-1** — deliberately deferred. **Do NOT build a general hook system** — an interception seam runs someone else's code in the kernel process, which the Tiered Isolation Contract reserves for Tier 1.
- **ISOLATION-DOC-STATUS-1** — trivial. `ISOLATION_MODEL_PLAN.md` says "no implementation has begun" at line 6 and "Scope B1 complete" at line 148. It sits at the repo **root**, outside the `docs/runtime/` frontmatter checks — which is why nothing caught it.
- **MEM-EXPAND-DEAD-1** — `expand()`'s semantic-neighbour half returns `[]` on every call and always has (pgvector 0.4.2 returns `ndarray`, the guard tests `isinstance(list)`). **pgvector 0.5.0 fixes it — which is exactly why dependabot #390 was held, not merged:** taking it turns expansion on by default in the path that caused RT-MEMTXN-LEAK-1's pool exhaustion. **Widening the guard is not the safe option it looks like.**
- **MEM-RECALL-N1-1** — `recall()`'s scoring loop is 3 queries per candidate; the re-fetch exists only to read 4 columns the originating SELECT already read. Performance-only.
- **DB-NODUS-BUDGET-1** — both fixes shipped 2026-08-01 (idle-in-transaction cap 30s→60s + opt-in `AINDY_MEMORY_RECALL_OWN_SESSION`); remaining is soak then flip. **Do NOT "fix" this by rolling back the caller's session** — RT-MEMTXN-LEAK-1 tried that and it broke `test_agent_approve_idempotency`.
- **MCP-SDK-2X-1** — `mcp 2.0.0` removed the 1.x API `nodus-mcp 0.1.2` is built on. Both install sites are capped `<2` (`pyproject.toml` **and** the separate CI step — a cap must be repeated in both). **Not a test bug — do not skip the live test to go green.**
- **LOCKFILE-PLATFORM-1** — a Windows-generated `platform/package-lock.json` cannot satisfy Linux `npm ci` (missing packages are `bundleDependencies` of an optional wasm32 package, so a machine that never installs it never walks that subtree). Resolver shipped: **`Platform Lockfile` workflow**, dispatch-only. Stays open — every future rolldown/oxide bump needs the same treatment. **Process rule: verify a lockfile change with `npm ci`, never `npm install` + build** (install silently repairs the mismatch). Expect a benign `"peer": true` diff; read *"Packages added"*, not the changed bit.
- **DEP-UPGRADE-DEFERRED-1** — OTel and the UI major cluster both closed. **Two lessons kept:** the otel packages are version-locked so single-package PRs always die `ResolutionImpossible`; and **grouping is necessary but not sufficient** — dependabot resolves each package independently, so hand-align and verify with `pip install --dry-run`. react-router 7→8 deferred (needs a ui-kit release first).
- **C3** — non-Linux strong sandbox. **C2 closed 2026-06-06** (container-grade, escape-tested); C3 is **open** — both supported-platform tuples are `(PLATFORM_LINUX,)`, so non-Linux hosts reach `container-sandbox-certified` but not `strong-sandbox-certified`. Pre-scoped in `C3_NON_LINUX_STRONG_SANDBOX_PLAN.md`.
- **CLI-1** — lazy settings getter / module-level import hazard. Deferred post-1.0.
- **CLI-SANDBOX-FORMAT-1** — `sandbox` raw JSON output wall. Deferred to 1.0.1.
- **SYSMAX-5** — OPEN, P2, **latent by construction**. The scheduler thread pool is **smaller than the job count and was never sized deliberately**: APScheduler's default `ThreadPoolExecutor()` = **10 workers** vs **12 runtime jobs** in `platform-only` (the floor) plus **21** app `register_scheduled_job` sites → **~33 jobs on 10 workers** in a real deployment; app jobs are added with no `executor=` so all land on `default`. Two holders are unbounded: `scheduler_heartbeat_tick` holds a worker for a whole INLINE execution (~13 min in the FR-15 incident), and several maintenance jobs block up to `DB_POOL_TIMEOUT` (60s) under pool exhaustion (`RT-MEMTXN-LEAK-1`). Failure mode is a **maintenance brownout** — recovery jobs stop running exactly when the condition they exist to clean up is happening, and nothing raises. **FR-15 (b) protected `scheduler_wait_tick` by name with its own executor; the ratio itself is untouched.** ★ **Do not close by raising the number alone** — that moves the threshold without bounding either holder; re-read after FR-15 (a), which may reduce this to "dedicated recovery executor + emit pool saturation". Source: `TECH_DEBT.md` SYSMAX-5.
- **SYSMAX-1 / -3 / -4** — thread-mode 100-job cap still the `.env.example` default (prod overlay enforces distributed); memory bytes not enforced per EU (needs OS integration); per-EU syscall and wall-time caps advisory.
- **TIER3-10** — `async_job_service` coupling. Architectural, no bounded fix.
- **DEPLOY-TARGET-1 / -2** — cloud deployment manifests; multi-tenant SaaS readiness gate. Triggers: first cloud deployment / first multi-tenant operator.
- **BILLING-1..5** — deferred until commercial launch. Source: `docs/runtime/MONETIZATION_AUDIT.md`.
- **LAYER-1..5** — layer boundary violations. All deferred.
- **ROUTE-EXTRACT-\*** — remaining candidates: `memory_router` (split required), `coordination_router` (AgentRegistry ownership gap).
- **PACK-DEBT-\*, DEBT-COMPAT-\*, TENANT-\*, COMPAT-\*, DATA-\*, LOCAL-\*** — packaging, dependency and architectural gaps.

### Open — programs and multi-item prefixes

- **APP-FR-\*** — app-side feature requests from `aindy-apps-monolith`. **Next available: FR-17.** Open: **FR-14** (`bootstrap-schema` refuses additive drift → crash-loops on any release adding a runtime column; **their claim that `serve` self-migrates is VERIFIED FALSE** — one behaviour, two gates, neither default-on. **★ PARTIALLY CLOSED 2026-08-16 (#450):** branchable exit codes — **3** additive-reconcile-required (*the only one safe to automate*), **4** offline-migration-required, **5** manual-repair; `1` stays config-error and `2` import-failure, which is the point. Precedence puts 4 above 3 so an entrypoint never auto-reconciles a DB that needs a person. `--help` now says a bare call under `set -e` is a crash loop in a container; the release checklist gained a `git diff`-gated step requiring the handoff to name the reconcile. The report always had `reconcile_supported`/`offline_migration_required` — only the exit surface collapsed them (IDEM-11's `register_syscall` shape). **★ STILL OPEN, the half that prevents recurrence: the upgrade path is never exercised against an EXISTING database** — CI builds a fresh one where `create_all` makes the columns, so no green check can see this class; the same blind spot hid FR-8) and **FR-6 items 2+3** (forgot/reset — blocked structurally: the runtime ships no `email` connector, so "the runtime sends it" has nothing to send with; recommend runtime SMTP or hybrid, never the app-connector option that inverts the split). FR-1/2/3/4/5/7/8/9/10/11/12/12b/13 all shipped. **Do not re-add the "their FR-7 status is stale" note — withdrawn 2026-08-15; the note was itself the stale thing.**
- **ECOGAP-\*** — ecosystem capability gaps (`ECOGAP-1..6`), roadmap gaps rather than classic debt. ECOGAP-2 is owned by C2/C3, ECOGAP-3 extends MEMORY-EMBEDDING-PROVIDER-1 — **don't double-track**. ECOGAP-1 Phases 1+2+2a and ECOGAP-4 G4b (MCP client + stdio server) shipped opt-in. **G4a remains built-but-INERT** — every guard vacuous until a policy is registered.
- **RTR-\*** — runtime roadmap (`RTR-1..8`). RTR-1/5/6 closed; RTR-2/3/4/7 harden-halves done, BUILD halves deferred (RTR-3 full AgentRun↔FlowRun unification; RTR-4 remaining = soak+flip `AINDY_DELEGATION_PRIVATE_MEMORY`). RTR-8 stale/closed. **RTR-4 gotcha: delegate writes take the deferred capture path, so `MemoryNodeDAO.save` is the write chokepoint, not the syscall.**
- **AGENT-HARDEN-1..10** — **all CLOSED 2026-07-05/06.** Cancel, HMAC tokens, compensating undo, simulation + virtual tools, LLM fallback chain, verifier, cassette contract tests, capability policy, secret broker, extension signing. MCP is not here — it is ECOGAP-4.
- **DOCS-\*** — docset findings. DOCS-BUCKET-A-1 and DOCS-STALE-1 closed; **`Runtime Docs Validation` now asserts `last_verified` is real and `>= 2026-05-17`** (it only checked key presence before). **DOCS-COVERAGE-CLAIM-1 half closed:** 6 docs cited 8 test files that never existed; all four areas now have suites (249 tests) *and* are made to actually run. **★ The pattern worth keeping: four separate docs mis-stated plugin-layer routes as runtime-owned. Check `APP_ROUTERS` + `ROUTE_OWNERSHIP_INVENTORY.md`, never file presence.** **★ Gotcha: `ResourceManager.can_execute` returns `(True, None)` unconditionally under `settings.is_testing`, so quota enforcement is vacuous in tests** — and `is_testing` is a pydantic *property*, so patch it on the class.
- **SYSCALL-STABILITY-\*** — `-1` fixed 2026-08-13. `SyscallEntry.stable` (advertised maturity) and `_STABLE_SYSCALLS` (rename guard) measure different things and may legitimately differ. **Two gotchas: the duplicate-registration guard is on `SyscallRegistry.__setitem__`, not `register_syscall`; and `stable` defaults to `True`, so an unset flag is not necessarily accidental.** Open app-side: the monolith defines `register_all_domain_handlers` twice.
- **AUDIT-INVARIANTS-VERIFIED-1** — **RECORD, not a defect.** The claimed guarantees were swept, not just the gaps; most held. **Two did not:** the boot-time route proof (→ ROUTE-AST-UNWIRED-1), and *"output validation is warn-only"* — **FALSE for `stable` syscalls**, which return an error envelope; only *experimental* ones warn. **★ Method note: verify the guarantees, not just the gaps — both errors were in "already covered" sections, the part of an audit least likely to be re-checked.**

### Standing rule — not an item

- **★ Module-import-time env reads are invisible to behavioural tests.** Three bugs share this shape: FR-10 (`settings = Settings()` at import crash-looped the container), `ResourceManager._get_backend()` (caches the Redis-vs-in-process choice on first call), and the `AINDY_REDIS_URL` alias in `rate_limiter.py` — which survived a cleanup that believed it had removed the alias everywhere, because **nothing about the running limiter differs when the alias is honoured**. **When auditing env-var handling, grep the source; do not trust a passing suite.**

### Closed — kept as one line because the rule still bites

- **NODUS-UPGRADE-1** — pin now **`nodus-lang==4.2.0`** (2026-08-16, #451, FR-16). **★ The pin is EXACT, so an app cannot adopt a nodus release on its own** — `pip install nodus-lang==X` succeeds and leaves the env inconsistent with our declared requirement, and an editable install *downgrades* it back. Staying exact is deliberate; the cost is that bumping promptly is the runtime's obligation. **★ Probe checklist before any bump — one item is new: `GUEST-CONFINE-1` depends on the VM accepting `allow_subprocess`/`allow_network`/`allow_env`, so verify all 31 gated builtins still refuse AGAINST THE REAL VM** — a renamed argument leaves the guest unconfined while every VM-mocking test still passes. Also re-check the three fragile couplings (`syscall_runtime.call_syscall`, `NodusRuntime._get_active_vm`, `register_function` still refusing builtin overrides — `NODUS-SYS-SURFACE-1` depends on that). 4.2.0's breaking change (errors carry absolute paths) doesn't reach us: nodus errors are forwarded, never parsed.
- **GUEST-CONFINE-1** — **CLOSED 2026-08-15.** The guest VM ran unconfined: `nodus_worker.py` passed none of the confinement args, so a guest script reached subprocess/network/host env without touching the dispatcher, token, ledger, egress guard or tool registry. **Demonstrated, not inferred** — a guest script created a file on the host, read the real PATH, and did real DNS. Fixed with the 3 kwargs (`allow_subprocess/network/env=False`); measured first that no first-party `.nd` script in either repo uses them, so deny-by-default broke nothing. **Deliberately not env-configurable — a global flag re-opens the hole for every run at once; per-execution variation is EXEC-ENV-BIND-1.** **Gotcha: `allowed_paths` already defaulted to cwd, so the VM's *filesystem* guard was fine — the host write went via subprocess, which bypasses that check entirely.** Guard: `tests/unit/test_guest_confinement.py`, mutation-tested 4/5 with a liveness control.
- **RT-MEMTXN-LEAK-1** — CLOSED (three parts). Login 43.6s → 0.3s, 60 held connections → 0. **Rules: never hold an open DB transaction on a request-shared session across a slow external call (embedding/LLM/HTTP) — order the code so the external call precedes the DB work; never `rollback()` a shared session to free its connection; and a memory capture must never enqueue work whose own lifecycle events are capturable (any capture → job → capture edge is a cycle).** **Gotcha: after a commit, touching an ORM attribute silently re-opens a transaction.** **Diagnostic: `xact_age_s == idle_s` cannot distinguish "held across a slow call" from "held by a frame that never returned" — only a stack dump does (`py-spy dump` needs `--privileged`).**
- **CI-MARKER-1** — CLOSED 2026-08-15, both halves. `tests/unit/conftest.py` now applies `runtime_only` by default, so a new unit file cannot silently run nowhere. **Everywhere else the old rule still bites: nothing marks a file outside `tests/unit/`, and `pytest.integration.ini` only reaches `tests/integration`. Adding a test directory means giving it a job.** **Gotchas: `--collect-only -q` prints `<path>: <count>`, not node ids; exit code 5 is `EXIT_NOTESTSCOLLECTED`, not an error.**
- **FLAKY-1** — CLOSED 2026-08-15 (15 healthy runs across two trees). **Rules that outlived it: when chasing a flake, never pipe the run through `tail`** — three failures were destroyed at the moment they were produced — **and do not conclude from small samples** (this one produced three wrong readings before the fourth run refuted each).
- **MEM-DELETE-1** — core shipped. `sys.v1.memory.delete` is hard, syscall-only, tenant-scoped, irreversible, with its own `memory.delete` scope **not** granted by `memory.write`. **No SDK consumer — nothing calls it yet.** Four opt-in upgrades deferred (G1–G4).
- **NODUS-SYS-SURFACE-1** — CLOSED. Idiomatic `import "std:sys"` routes to nodus's own 4-syscall stub, **not** the AINDY dispatcher; only the bare `sys(...)` builtin reaches `dispatch_syscall`. It could not be aliased, so there is a fail-loud guard in `nodus_worker.py`.
- **MCP-BEHAVIOR-1** — `call_tool()` never raises; check `result.isError is True`. Full note in its own section below.
- **NATIVE-CI-1** — CLOSED. `Native Crate Build (Rust)` runs `cargo build --locked --release` every PR. **Encoded gotchas: `--locked` is the point; no `cargo test` (pyo3 `extension-module` omits libpython, so a test harness fails to link); deliberately not path-filtered (a `paths:` filter on a required check never reports and blocks forever); added to `runtime-ci.yml` because a new workflow file doesn't trigger on the PR that adds it.** Builds on Linux, not MSVC.
- **NATIVE-DISCOVERY-1** — CLOSED. Both crate consumers now delegate to `AINDY/memory/native_bridge.py`. **★ Trip hazard: `cargo build` emits `libmemory_bridge_rs.so` / `memory_bridge_rs.dll` — Python imports neither. CI renames; a local build needs it by hand.** **And `sys.path.insert` in priority order puts the lowest-priority path first** — that inversion let a stale debug build shadow a fresh release one.
- **NATIVE-PARITY-1** — CLOSED. Native and Python scorers disagreed on negative `impact_score`. **Severity was defense-in-depth, not live** — `MemoryNodeDAO.save()` clamps at the universal write chokepoint. **★ The regression guard is native-independent on purpose** — parity tests skip without a built crate, so pinning the clamp only there would repeat DOCS-COVERAGE-CLAIM-1 in miniature.
- **EVENTBUS-PUBLISH-LATCH-1** — CLOSED. **Root cause was one field meaning two things:** `_enabled` was both the operator kill switch and the runtime give-up latch, which made a transient blip permanent *and* invisible. Now split: config vs. a `CircuitBreaker`. **Behaviour change: `/health/deep` reports the bus degraded during suspension rather than `ok`.**
- **EVENTBUS-COVERAGE-1** — CLOSED. The pub/sub wire had never been exercised end to end. **★ Mutation-tested 5/7 — the first draft scored 4, because a test asserting an *absence* passes when the wire is broken; it now runs a liveness control first.** **Placement: marked `redis`, NOT `integration`** (that marker trips the conftest skip guard). **Both race pitfalls were real: buses in one process share a hostname-derived `_instance_id`, and Redis pub/sub has no readiness signal — republish inside the polling loop, never a fixed sleep.**
- **ROUTE-GUARD-1** — CLOSED. Every `raise HTTPException` in three routers was returning **500**; FR-12's reserved-namespace guard answered 500 instead of 409 for a full day. **★ Why nothing caught it: the tests assert on the route's *source text* and never call it.** **A route test must call the route** — the status code *is* the contract. **Flagged, not fixed — `ADMIN-PROMOTE-UUID-1`:** the promote route also 500s on a missing user, for an unrelated SQLite-harness-only reason.
- **KERNEL-INIT-DUPLICATE-1** — CLOSED. `AINDY/kernel/__init__.py` was a byte-identical copy of `tenant_context.py`, so two different `TenantContext` classes existed and `isinstance` was silently `False` across them. Nothing had broken because nothing imported it. **All 337 `.py` files under `AINDY/` were hashed — no byte-identical duplicates remain.**
- **TENANT-FROZEN-SHALLOW-1** — CLOSED. `frozen=True` does not deep-freeze; `capability_scope` is now a tuple. **Still open, adjacent: `TenantContext.validate_memory_path` rejects the exact tenant root while `memory_address_space.validate_tenant_path` accepts it — two tenant guards, two answers for one string.**
- **MAS-FLATTEN-1** — CLOSED. `flatten_tree` dropped every node that was a parent of another. **★ The invariant that would have caught it in one line: `len(flatten_tree(tree)) == len(tree)`.** Zero callers, but documented as usable, so fixed rather than deleted.
- **NODUS-WARMPOOL-1** — CLOSED 2026-07-19. Warm worker pool (`AINDY_NODUS_WARM_POOL`, default off) + boot-allowance clock split, so plugin cold-start is not billed to the script budget. Falls back to a fresh subprocess on any fault.
- **INFINITY-RUNTIME-1** — FULLY CLOSED. **Gotcha: adding a `SystemEventTypes` value trips the frozen-hash baseline — regenerate `tests/baselines/system_event_contract.json` in lockstep.** Remaining is flag-flip after soak, not build.
- **PYPI-PUBLISH-1** — CLOSED. **Release protocol (both halves bit us before): bump the Dockerfile builder-stage pin AND the CHANGELOG in one PR; after the tag publishes, append the `SANDBOX_ESCAPE_AUDIT.md` entry for the gate run.** `Boot Smoke` installs the pinned version from PyPI, so a bump PR skips-green until the tag exists.
- **PLANNER-SUBPROC-1 / INFINITY-COMPLETION-HOOK-BOUNDARY-1** — CLOSED. Stateful callback surfaces run in-process. Full mechanism is in the `_maybe_wrap_runtime_callback` section above — read that, not this line.
- **SDK-SYSCALL-GRANT-1** — CLOSED. Per-syscall least-privilege grants. **Two namespaces (`Scopes` vs capabilities) — don't conflate them.**

### Closed — no live rule; see `TECH_DEBT.md` if you need the history

`MEM-NODETYPE-1` · `LEASE-1` · `REPLAY-1` · `PROMETHEUS-PIN-1` ·
`OPER-DEFER-001` · `OPER-DEFER-002` · `IDEM-1..10` (IDEM-10 closed at the mechanism level
2026-07-11 via the MEB program; next available **IDEM-13**) · `DOCS-BUCKET-A-1` · `DOCS-STALE-1` ·
`C2` · `SYSCALL-STABILITY-1`

- **IDEM-12** — OPEN, P2, **latent**. `undo_run_effects` selects effects by `status == "success"`, never marks them reversed and never consults `effect_reversals`, so a second `sys.v1.agent.undo` re-invokes **every** compensator (double refund) and duplicates audit rows. Not live only because **zero compensators are registered** — it goes live with the first one. `EXACTLY_ONCE` (IDEM-11) is defense-in-depth, **not the fix**: the gate is default-off and keys on `(name, payload, scope)`, so a deliberate second undo still lands. **Do not close by relying on the IDEM-11 flag flip** — that makes reversal correctness depend on an env var, the shape IDEM-10 already paid for.


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
| **What the runtime is (category + what a consumer inherits)** | `docs/runtime/WHAT_THE_RUNTIME_IS.md` |
| Runtime module map (tagged inventory) | `docs/runtime/RUNTIME_MODULE_MAP.md` |
| Runtime execution invariants | `docs/runtime/EXECUTION_INVARIANTS.md` |
| Architecture risk (complexity/blast-radius) | `docs/runtime/ARCHITECTURE_RISK.md` |
| Runtime security matrix | `docs/runtime/SECURITY_MATRIX.md` |
| Cross-repo compatibility policy | `docs/runtime/CROSS_REPO_COMPATIBILITY.md` |
| Runtime → SDK contract | `docs/runtime/SDK_CONTRACT.md` |
| Runtime → UI contract | `docs/runtime/UI_CONTRACT.md` |
| Latest app-team handoff (v2.2.0) | `docs/runtime/APP_HANDOFF_v2.2.0.md` |
| Release verification checklist | `docs/runtime/RELEASE_CHECKLIST.md` |
| Cross-repo regression tests | `tests/unit/test_cross_repo_compatibility.py` |
| Unit-marker auto-default + its guard (CI-MARKER-1) | `tests/unit/conftest.py`, `tests/unit/test_ci_marker_default.py` |
| Syscall registry floor constant | `AINDY/kernel/syscall_registry.py` — `SYSCALL_REGISTRY_MIN_COUNT` |
| Native crate loader (single search policy) | `AINDY/memory/native_bridge.py` — `load_bridge()`, `search_paths()` |
| Agent identity hook (FR-12) | `AINDY/platform_layer/registry.py` — `register_agent`; applied in `startup.py` `_apply_registered_agents()` |
| Runtime-callback subprocess budget (FR-11) | `AINDY/platform_layer/runtime_callback_host.py` — `resolve_callback_timeout_seconds()` |
| Event-bus publish circuit breaker | `AINDY/kernel/event_bus.py` — `_publish_breaker`; state via `get_status()` |
| MAS / native / OS-layer / event-bus suites | `tests/unit/test_memory_address_space.py`, `test_memory_native_scorer.py`, `test_os_layer.py`, `test_event_bus.py` |
| Event-bus wire test (needs live Redis) | `tests/integration/test_event_bus_wire.py` — marked `redis`, **not** `integration` |
| Tech debt tracker | `TECH_DEBT.md` |
| Roadmap reading aid (digest of `TECH_DEBT.md`; NOT the source of truth) | `RTR.md` |
| Docker compose | `docker-compose.yml` |
| Dockerfile | `Dockerfile` |
| pgvector init script | `docker/init-pgvector.sql` |
| Prometheus config | `monitoring/prometheus.yml` |
| Runtime env reference | `AINDY/.env.example` |
| Agent admin routes (list / register / deactivate / restore) | `AINDY/routes/platform/admin_router.py` — mounted at `/platform`, runtime-owned |
| User-owned agent routes (FR-12b) | `AINDY/routes/platform/agents_router.py` — `/platform/agents`, owner-scoped; `derive_user_namespace()` |
| Platform system agent roster (single source) | `AINDY/db/models/agent.py` — `SYSTEM_AGENT_SPECS`; `SYSTEM_AGENTS` is derived from it |
| Route-guard bypass test (ROUTE-GUARD-1) | `AINDY/core/route_execution_guard.py` — `_is_pipeline_bypass_on_error()` |
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
