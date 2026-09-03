# CLAUDE.md

This file provides guidance to Claude Code (c- **FLOW-GRAPH-SIGNATURE-1** — CLOSED 2026-09-03 (#559). A suspended run resumed against **whatever flow definition existed then**, silently and successfully. A run now records a hash of its graph SHAPE at start and is **quarantined** on mismatch (`flow_runs.graph_signature`, Alembic 0018). **★★ What goes in the hash IS the design:** node identities and edge topology, targets IN ORDER (first matching edge wins); NOT bodies, `node_configs`, or predicate implementations *or names* — one that moved every deploy quarantines every in-flight run and is switched off in a week. **★ Absent ≠ mismatch**, or upgrading quarantines everything. **★ Deliberate blind spot: a changed PREDICATE is not caught — that needs the predicate to be DATA (`FLOW-PARALLEL-1`).**
laude.ai/code) when working with code in this repository.

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
ruff check AINDY tests --config AINDY/ruff.toml   # exactly what CI's `Runtime Lint` runs

# ★ Do NOT run `ruff format` casually — see LINT-FORMAT-1. The tree has never been
# formatted: 457 of 559 files would change, so a stray run buries your diff in ~450
# unrelated files. Nothing enforces it; `Runtime Lint` runs `check` only.

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
- **★ The current head is `RUNTIME_ALEMBIC_HEAD_REVISION` in `AINDY/db/alembic_head.py`, and the
  chain is `ls alembic/versions/`. This file no longer enumerates it.** It used to, and it went
  stale twice — once four migrations behind, then again the day `0017` shipped — *in the section
  you read before writing a new migration*, which is the worst possible place for it. The
  constant is CI-enforced by `tests/unit/test_runtime_alembic_head.py`, so it cannot drift;
  a hand-copied list here can only ever be a slower, wrong second copy.

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
| *(not required yet)* `Upgrade Path Guard` | `upgrade-path-guard.yml` | previous release's DB → this build (`FR-8`/`FR-14`). **Read its `negative-control` job**: on a release with no schema change the main job passes trivially. |

**Before adding an eleventh, check what made these ten safe:** no `paths:` filter (the classic
trap — a filtered check never reports on unrelated PRs and blocks them forever), no job-level
`if:`, and `Boot Smoke` guards its steps *individually* so a version-bump PR reports green
rather than hanging pending.

**Consequence to expect:** with `strict: true`, every PR needs a rebase when `main` moves, and
`Integration Tests` (~6–7 min) sets merge latency. Combining dependency bumps into one PR is the
normal move, not an optimization.

---

## ★ Trusting a green check — read this before citing CI as evidence

**Eleven separate times** this repo has shipped something that *looked* covered and was not.
Assume there will be a twelfth — the catalogue exists so you can recognise the shape, and the
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
| 9 | **Green because there was nothing to catch** | a check whose condition this release does not contain — `Upgrade Path Guard` passes trivially with no schema change | `FR-8`/`FR-14` |
| 10 | **The instrument cannot see the thing** | `caplog` silently captured nothing for a warning emitted on a WORKER THREAD by a module logger — so the assertion could not tell *"the mechanism did not fire"* from *"I failed to observe it"* | soak harness |
| 11 | **The answer went stale, not wrong** | seven PRs carried a green `pip-audit` for a week while the advisory refuting it was published — the check asks a question about the OUTSIDE WORLD, and the world moved without the diff moving | `security-audit.yml` |

**Variant 9 is the one to design against, not just record:** it cannot be fixed by making the
check better, because the check is fine — the *release* lacks the condition. The only answer
is a **negative control that injects the condition**, which is why `upgrade-path-guard.yml`
ships with one. A new check is at its least proven exactly when it is newest, and "it went
green" is worth nothing until something has made it go red.

**★ Variant 10 is the one that bites hardest in a CONCURRENT or CROSS-PROCESS test, and this
repo is about to write more of those.** It surfaced when a soak assertion failed on a
docstring-only commit — the signature of an unreliable instrument, not a regression. Three
instruments were tried before one worked: `caplog` (could not see across the thread boundary), a
logger spy (thread-safe, but observes a log line, which is not what an operator has), and finally
a **Prometheus counter** — thread-safe, and the same signal production reads.

**Rules that follow, and they generalise past logging:**

- **Prefer the signal an operator would actually read.** If the assertion and the ops dashboard
  disagree about where to look, the test is measuring a proxy.
- **`caplog` is not thread-safe for practical purposes.** Anything asserting on a log emitted off
  the main thread needs a different instrument. A test asserting the *absence* of such a log is
  vacuous by construction — variant 6 with a specific, easy-to-miss cause.
- **An instrument that can be absent must fail loudly when it is.** `soak_harness.read_metric`
  raises on an unknown metric family rather than reading 0, because `None`-as-zero makes "did not
  move" and "does not exist" indistinguishable. **Its first real use immediately found the other
  half:** a labelled counter has no sample until `.labels()` is first called, so
  "family exists, this label combination unobserved" must read 0 while "no such metric" still
  raises. The guard was right to refuse; the rule was too coarse.

**★ Variant 11 is the only one where the check was RIGHT when it ran, and this is the class to
expect from every dependency, advisory or license check.** Such a check does not ask a question
about the branch — it asks one about the OUTSIDE WORLD, so its answer decays on its own, with no
commit to mark the moment. On 2026-08-31 `pip-audit (OSV)` went red on an **unchanged `main`**:
`a2fe25c` passed it on 08-24 and failed it on 08-31, because `PYSEC-2026-3726` was published
against a pinned `nltk` in between.

**The four PRs it turned red were not the hazard — the seven it left green were.** The red ones
were loud and got looked at. The seven older ones kept a green from 08-24 that any re-run would
have refuted, and nothing about `gh pr checks` says so: it prints a duration, never a date. A
week-old green on an external-input check is not evidence, and it is indistinguishable from a
fresh one at a glance.

**Rules that follow:**

- **For a check whose input is external, the age of the result is part of the result.** Read the
  run date before citing a dependency/advisory/license check — `gh run list --workflow=<f>
  --branch <b>` prints `createdAt`; the PR checks view does not.
- **A required check must gate the branch it protects, not only the PRs into it.** This one ran
  on `pull_request` + a weekly `schedule` and had no `push` trigger, so `main` could sit red on a
  CVE for up to a week with nothing surfacing it — and it only surfaced at all because a PR
  happened to be open. Fixed by adding `push: branches: [main]`; the same question is worth
  asking of any check whose value is time-varying.

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

**★ Trusting your own verification — three rules that each cost something on 2026-08-20:**

- **A check read moments after a push is the PREVIOUS commit's.** `gh pr checks` returned green
  about a minute after a push; those were the prior head's results, the PR was merged on them, and
  **a pushed commit was silently lost** (the design doc's settled decisions, recovered later from
  an orphan). The merge succeeding proves the *merged* head was green, not that it was your
  latest. **Compare `gh pr view --json headRefOid` against `git rev-parse HEAD` before merging.**
  The tell that was missed: the remote branch survived `--delete-branch`.
- **A local suite run that stops partway measures how far it got, not whether it passed.** Three
  full-suite runs died at 31%, 57% and killed; each time "zero failures so far" was reported as
  evidence and CI then found real failures in files the run never reached alphabetically. On this
  machine a partial sweep is a *different measurement*, not a weaker one — report it as progress.
  A targeted subset that finishes is worth more than a broad one that does not.
- **A low mutation score is often bad mutations, not weak tests.** Two runs scored 2/4, and in
  both cases every survivor was a defective mutation — one edited code the fixture disabled, one
  added an unused class while the real branch still ran. **A mutation that does not change
  behaviour proves nothing about the test.** Verify the mutation bites before concluding the
  test is weak; the reverse mistake (weakening a good test to "fix" a score) is worse.

**Chasing a flaky test:** never pipe the run through `tail`. Three observed failures of
`FLAKY-1` were run as `pytest ... -q | tail`, which discarded the traceback and kept the summary
— the evidence was destroyed at the moment it was produced, three times. Write to a file. And do
not conclude from small samples: that test produced **three** wrong readings (deterministic,
branch-caused, confined to `tests/unit/`) before the fourth run refuted each. Against a ~50% base
rate, four clean runs happen ~6% of the time by luck.

---

## ★ Vendored shims on `pythonpath` — untested by construction

`pytest.ini` sets **`pythonpath = . AINDY`**, so `import apscheduler` resolves to the
hand-written shim in **`AINDY/apscheduler/`** for *every test in this repo*, not to the
installed package. **Anything the runtime calls that the shim does not implement is untested by
construction** — and where the call sits inside a `try/except`, it fails *silently*: the test
passes, production takes a different branch.

This has now bitten three times:

1. `executors.pool` missing → the dedicated-executor branch shipped unexercised (`FR-15` (b)).
2. `events` + `add_listener` missing → the starvation listener shipped unexercised (`SYSMAX-5`).
3. `remove_job` missing → `_remove_from_scheduler` swallowed an `AttributeError` under a comment
   claiming it was for an already-deleted job. **Removal could have been a permanent no-op with
   every test green.**

**Rule: grow the shim to match the guard, never weaken the guard to match the shim.** A
source-derived guard now exists (`tests/unit/test_apscheduler_shim_parity.py`) — it scans
`AINDY/` for scheduler method calls and fails if the shim cannot express one, so a fourth
instance is a CI failure rather than a discovery.

**`nodus` is the other shadowed name.** `AINDY/nodus/` shares the installed package's name and
`AINDY/nodus/runtime/embedding.py` shares the exact module path `GUEST-CONFINE-1`'s tests import
`NodusRuntime` from. It currently resolves to the **installed** package (pinned by a test), and
the collision is self-limiting only because that file is a re-export — a real definition there
would turn a loud failure into a silent one.

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

## Current phase + standing decisions (2026-08-20)

**Phase: runtime testing.** Things get connected to the runtime in order to exercise it — which
is why app-side feature requests keep arriving; they are a symptom of the testing method, not
scope creep.

**★★ CORRECTED 2026-08-20 — this section used to end "flag soak happens in `aindy-apps-monolith`,
not here … Don't plan soak work in this repo." That is FALSE, and it was the instruction standing
between the runtime and its own backlog.** Eight items had "soak, then flip" as their entire
remaining work, and deferring that soak to the app repo is why none of them moved for months —
`SUBSTRATE-WITNESS-1` records the other half of the same mistake (no first-party consumer sends
traffic through the paths being flipped, so the soak could not happen there either).

**Soak happens HERE, and the apparatus exists**: `tests/integration/soak_harness.py`
(barrier-synchronised concurrency + before/after metric readback), an advisory flag-on CI step,
and live Postgres + Redis on every PR. Three defaults were flipped on evidence gathered in this
repo on 2026-08-20 — and the first contention soak found that `EXACTLY_ONCE` is not exactly-once
under contention, which no amount of app-side traffic would have surfaced as a *runtime* finding.

What remains true: the runtime still ships capabilities default-off until there is evidence, and
the app repo is where production traffic lives. What was wrong is the inference that evidence
therefore has to come from there.

**★ Release state — there is deliberately no version number in this paragraph any more.** Read
it from the four places that cannot go stale, and reconcile them:

```bash
git tag --sort=-creatordate | head -1      # newest tag
cat AINDY/_version.py                      # what the next build will call itself
grep 'aindy-runtime==' Dockerfile          # what the image installs
sed -n '/^## Unreleased/,/^## [0-9]/p' CHANGELOG.md   # empty = drained, else a release is owed
```

*Why the numbers are gone rather than corrected: this paragraph has been wrong **four** times —
it said `2.0.0` after 2.0.0 shipped, described 2.1.0 as unreleased after it was tagged, said
`v2.1.0` while 2.3.0 was live, and said `v2.3.0` while 2.4.0 was live. Each correction was
accurate on the day it was written and decayed the same way. A fifth was free, so the class was
removed instead.* Per-release facts — schema contract, Alembic head,
`recommended_runtime_requirement`, whether `bootstrap-schema` needs `--reconcile` — belong in
that release's `CHANGELOG.md` entry and app handoff, where they stay attached to the release
they describe.

**What does not decay, and is why this section still exists: the newest *tag* and the newest
*fix* are different things.** Work merged after a tag is in no installable release until the
next one is cut — `2.4.0` shipped with the `nodus-lang` pin that `f7f3555` had already fixed on
`main`, which is the whole reason `2.4.1` exists. **Before telling anyone a fix has shipped, run
`git tag --contains <commit>`;** "it is on `main`" and "it is released" are separate claims and
only the first is cheap to verify.

The release *protocol* — which does not go stale either — is in the `PYPI-PUBLISH-1` line of the
prefix registry and in `docs/runtime/RELEASE_CHECKLIST.md`.

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
`TECH_DEBT.md`. Do not reuse a number within a prefix.

**★ That rule is now enforced, because stating it three times did not work.**
`tests/unit/test_debt_registry_accuracy.py` fails when a registry entry exceeds **1150 bytes**
(**850** under a `### Closed` heading). Both numbers are the *current high-water mark*, not an
endorsement of that length — the cap is a **ratchet against regrowth**, and the history is why:
the registry was trimmed to 66 KB, was back to 98.8 KB within a week, and the next "trim"
(#487) reported −14,936 B while the file actually grew 96,913 → 115,234 B. Every one of those
was honest work measured wrongly — **the trim was measured over the entries touched, not over
the file.** Measure the whole file, before and after.

**Where the detail belongs is not a judgement call: 79 of 91 entries already have a *larger*
record in `TECH_DEBT.md`.** If trimming an entry would lose something, the loss is the signal
that the text was never indexed anywhere — move it down first, then trim. Ratchet the cap
downward in a dedicated pass; never raise it to accommodate a new entry.

**★ Provenance tags → `docs/runtime/COMPARATIVE_RESEARCH_INDEX.md`.** A line tagged
*(Aider research)*, *(MAF research)*, *(Codex research)*, *(Claude Code research)*,
*(CrewAI/Nodus research)*, *(GPT Engineer research)* or *(ADK research)* came from a comparative
analysis in `C:\codev\<name> research\`, not from an audit of this codebase — a different class of
finding (**absent vocabulary rather than broken wiring**). **Eight systems have been audited; the
index records what each produced, what is already SETTLED and must not be re-litigated, the six
recurring errors to check first on the next system, and which primitives were derived twice
independently.** Each folder holds an `ACCURACY_CHECK_vs_aindy-runtime_*.md` recording which claims
survived verification against source. `TECH_DEBT.md` provenance headers:
`AIDER-PORTABILITY-2026-08-17`, `MAF-REFERENCE-2026-08-17`, `CREWAI-NODUS-2026-08-18`,
`ADK-LENS-2026-08-18`; Codex, Claude Code, Hermes and GPT Engineer entries cite sources inline.
**Read the index before acting on one of these, and before starting a new system — none is a
defect, and several are things no internal audit would surface.**

**★ A closed entry must not sit under an `### Open` heading.** This drifted: after one week of
closures, six entries whose own text began `**CLOSED …**` were still filed under `Open — P0`/`P2`,
and one read *"D open"* two days after D merged. Anyone scanning for open work got a wrong
answer and nothing said so — the same shape as an index contradicting its entries.
`tests/unit/test_debt_registry_accuracy.py` now fails when an Open entry headlines itself as
closed. It deliberately does **not** guess at partial closure: `IDEM-11` and `HTTP-SCOPE-GAP-1`
legitimately describe closed halves while staying open, and a checker that guessed would be
disabled inside a month. (This section was 104 KB — 68% of this
file — because findings were written where they were discovered instead of where they belong.)

### Open — P0

- **FR-15** — dispatch is serialised: `schedule()` is the only queue drainer and runs each item **synchronously**. (b)+(c) shipped 2026-08-15/16; **(a) thread-mode flipped 2026-09-01**. **★★ The DISTRIBUTED half is BUILT AND OPT-IN 2026-09-02 (#551–#556)** — a resume crosses the queue as `run_id`+`eu_type` and the worker rebuilds it with the call the rehydration sweeps already make every boot; the durable record was never missing. Not refused under `EXECUTION_MODE=distributed` any more, but does NOT inherit the default there. **★★ FOUR silent losses were found on this path, all one shape — an unresolvable message ACKed as SUCCESS — and THREE were introduced by the fix for the previous one.** **★ STAYS OPEN: what remains is EVIDENCE, not code — the soak runs `process_one_job` IN-PROCESS, and a separate worker PROCESS is where every one of those four lived.** Remaining: enable on one distributed deployment, watch `aindy_execution_dispatch_total` vs the DLQ; then migrate live wait registrations. **Do NOT close on the opt-in.** Source: `TECH_DEBT.md` FR-15.


### Open — P1

- **EXEC-ENV-BIND-1** — **PHASES 1+2 SHIPPED 2026-08-19; open for 3-4.** Phase 1: an EU can DECLARE its environment (three columns, Alembic `0017`) — declare/refuse/record, **confines nothing**; `env_evidence_class` is the field that says whether anything was enforced. Phase 2: **the guest path now ASKS** — `nodus_worker` derives every confinement arg from a spec clamped to `GUEST_FLOOR`, closing `GUEST-CONFINE-1`'s residual. **★ The re-raise guard is the phase-1 mechanism: `require_execution_unit` ends in a broad `except Exception` returning `None`, so `except ExecutionEnvironmentError: raise` sits BEFORE it, and catches the BASE class so a malformed spec propagates too.** **★ `assurance_rank()` ranks unknown LOW; ranking it high makes a typo satisfy every minimum.** **★ A guest cannot widen its own sandbox — a declared spec is clamped to the floor, never merged with it.** Design + phasing: `docs/runtime/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md`.
- **CANCEL-REACH-1** — cancellation is durable but never reaches an in-flight effect; observed between segments only. **Constraint already paid for twice: `should_stop()` must not do a per-effect DB round-trip on the request-shared session** (RT-MEMTXN-LEAK-1, MEM-RECALL-N1-1).
- **FLOW-PARALLEL-1** — no fan-out/join/barrier; plan steps are strictly sequential, so independent calls cost the sum of their latencies, and apps needing parallelism route *around* the flow engine, losing history/retry/quota. **Determinism is the load-bearing part of any fix, not speed.** **★★ Declaration order answers ORDERING, not CONFLICT: `runner_steps.py:257` is `state.update(patch)` — LAST-WRITE-WINS, harmless only because there is never a second writer today. Require a DECLARED per-cell conflict policy and fail loudly on an undeclared double-write (LangGraph's `LastValue` / `BinaryOperatorAggregate` / `NamedBarrierValue`); do not pick one.** ★ MAF reframe: a superstep is the EXISTING per-node commit boundary widened to a barrier-delimited group, not "add concurrency"; their documented negative result is that predicates do not serialize. Open decision, barrier-as-commit-boundary vs independent branch commits — **settle WITH `EFFECT-PARTIAL-1`.**
- **COST-GOVERNOR-1** — **METER SHIPPED 2026-09-03 (#563, #564); the GOVERNOR is BLOCKED, and not on its design.** The runtime enforces a 300s wall-clock and 256MiB ceiling on work whose dominant cost is tokens and did not measure them — **worse than a missing cap, the usage object was DISCARDED at the boundary** (`return str(response.choices[0].message.content)`), so it lived one stack frame. `aindy_llm_tokens_total{provider,model,kind}` now records it on the RAW response path (not `chat()`, which delegates there — metering both double-counts, and a silently-2x number is a fabricated measurement a governor would reserve against). **★★ THE BLOCKER: the seam has NO CONSUMER.** Nothing in `AINDY/` outside `platform_layer` imports an LLM client and the app builds its own SDK clients, so a governor there refuses ZERO calls while passing its tests — `ROUTE-AST-UNWIRED-1` repeated. Scope: `LLM_SEAM_ADOPTION_SCOPE.md`. Budget scoping is SETTLED (agent run + tenant, both binding; refuse on breach). Source: `TECH_DEBT.md`.
- **AUTHORITY-NEGOTIATION-1** — a denied capability terminates the step; approval is whole-plan, so recovery discards durable state. Keep any fix bounded, **downgrade-only**, recorded. Note `sys.v1.agent.simulate` already offers a better fallback than retrying with more authority.
- **FS-SCOPE-1** — *(Aider research)* the capability vocabulary is **verb-shaped**, so no authority statement can name a path. `register_tool` carries `egress_scope` for network; `allowed_paths|path_scope|writable_root|allowed_dirs|fs_scope` returns **one hit repo-wide and it is a comment**. The runtime can say *may this run reach the network under scope X* and cannot say *may this run write `src/**` and nothing else*. **★ Do NOT build it as `fs_scope` beside `egress_scope`** — that is a second vocabulary for the question `EXEC-ENV-BIND-1` already asks. It is a field ON that descriptor, enforced at `TOOL-SEAM-ISOLATION-1`'s point: one structural change, three entries. ★ Shipped reference for the vocabulary: `codex-rs`'s `SandboxExecRequest` carries file-system and network policies as PEER fields plus `SandboxablePreference {Auto, Require, Forbid}` for fail-closed. **★ HALF DONE 2026-08-19: the vocabulary now EXISTS — `visibility.filesystem {mode, roots}` on `ExecutionEnvironmentSpec`, and it is enforced on the guest path. What remains is the OTHER seams, i.e. `TOOL-SEAM-ISOLATION-1`, not a second vocabulary.**

> **`EXEC-ENV-BIND-1` and `FS-SCOPE-1` converge on one root, with the now-closed `GUEST-CONFINE-1` and `TOOL-SEAM-ISOLATION-1`:** `create_sandbox_runner` is reachable only from `plugin_host.py` (verified at HEAD — the only *execution* call sites are `plugin_host.py:346` and `:816`; every other reference reads `.metadata()`). Four audits found it from four starting points — guest VM, execution unit, tool seam, path authority. It is **one provider re-homed and the call sites taught to ask**, not four fixes. The two closed ones were taken first because they needed no new vocabulary. `FS-SCOPE-1` is a **field on `EXEC-ENV-BIND-1`'s descriptor**, not a second vocabulary beside it.
- **EFFECT-PARTIAL-1** — **VOCABULARY SHIPPED 2026-09-03 (#560); the ENVELOPE half is what stays open.** A batched effect has three outcomes and the envelope has two, so a 5-unit effect with 2 failures was either a **lie** (`success`, silently partial) or a **waste** (`error`, discarding 3 applied). `EffectRecord.status` now has `partial`, the value set is **enforced** rather than a docstring convention (`complete_effect_record` validates), and no migration was needed — `String(32)`, no CHECK. **★ A `partial` with no per-unit record in `result_payload` is strictly worse than `failed`**: it reports that something went wrong and removes the ability to say what. **★ NOTHING EMITS IT YET, and the envelope is still `success | error`** — widening it is a consumer-visible response change and an app-team conversation, deliberately not bundled. Settled with `EFFECT-OUTCOME-UNKNOWN-1`, same column, one change. Source: `TECH_DEBT.md`.
- **SUBSTRATE-WITNESS-1** — *(Claude Code research)* **the substrate claim has no first-party consumer that exercises it.** Measured against `C:\dev\claw`: the flagship app integrates in 334 lines across 3 files, all optional, mostly HTTP; `execute_tool`/`EffectRecord`/`execution_token` appear **zero** times in its own source, and its real effects cross no chokepoint. **★ Corollary for reading this file: the coverage percentages across nine comparative audits describe capabilities the runtime HAS, not capabilities anything USES.** **★ Eight entries' remaining work is "soak then flip", and soak needs production traffic THROUGH the path being flipped. ★ Corrected 2026-08-19 — that is a DECISION NOT TAKEN, not an external constraint: every consumer is first-party and owner-controlled, so the integration is available whenever wanted. "Blocked" was the wrong word and eight entries inherited it.** Recommended slice: route only Claw's outbound message delivery through `execute_tool` with `EXACTLY_ONCE`. **★ Do NOT close with a synthetic fixture** — what is missing is a consumer that would NOTICE if the guarantee broke.
- **PERF-BASELINE-1** — *(Aider research)* **zero latency assertions across `tests/`.** **★★ MEASURED AND RENAMED 2026-08-19: the instrument EXISTS — 52 registered metrics — and NOTHING CONSUMED IT. Zero `get_sample_value` / `generate_latest` / `.collect()` across the whole tree. And the integration suite was ENTIRELY SEQUENTIAL: zero `ThreadPoolExecutor` / `asyncio.gather` / concurrent drivers, so the gated flags had been proven CORRECT and never under CONTENTION.** That was the real blocker behind eight "soak then flip" items — not production traffic, which is why deferring them to a consumer never helped. `tests/integration/soak_harness.py` (concurrency + metric readback, mutation-tested 6/6) and an advisory flag-on CI step now exist. **★ The gate metric that was missing now exists** (`aindy_effect_gate_outcomes_total`), so IDEM-11's production soak has something to read.

### Open — P2 and below

- **KEY-SCOPE-ESCALATION-1** — **Re-levelled P0 → P2 2026-08-18: both escalations FIXED (#463, #465); the residual is tidiness, not a hole.** A `flow.read`-only API key could mint itself `platform.admin` and promote its own user row (**survives revoking the key**), and could separately **rotate the platform signing key, choosing the new secret** — i.e. forge tokens for anyone. Root cause: `require_platform_admin_access` returned ANY `auth_type=="api_key"` unconditionally while its docstring claimed per-endpoint enforcement that never existed. **★ Gotchas: SQLite CANNOT reproduce it — `platform_api_keys.scopes` is a PG `ARRAY`, so a 201 shows up as a 500 and reads as an unrelated bug; and a 400 from `rotate-secret-key` can mean "same as the current key", raised AFTER authorization, so a status-code-only probe mis-reads validation as authorization.** `POST /platform/syscall` + `GET /syscalls` stay route-ungated **by decision**. Residual: the two admin guards disagree about what an API key is.
- **HTTP-SCOPE-GAP-1** — **Re-levelled P0 → P2 2026-08-18. Route coverage is COMPLETE; the remainder is a design question, not a gap.** Census on a booted app: 91 scope-gated / 12 admin / 21 public / 2 identity-only of 126. A JWT session derives scopes from `User.is_admin` **per request, not from a token claim**, so no session is invalidated and a grant lands on the next call. **★ Three gotchas that still bite: (1) a per-route `dependant` walk UNDER-reports enforcement — router-level `dependencies` are excluded and `_IncludedRouter` hides the nesting, producing a "97 of 126 enforce nothing" figure wrong by 56; (2) `enforce_api_key_scope` takes ANY-OF alternatives, and adding that form silently blinded a regex-based safety scan; (3) scanning `app.routes` for a path prefix finds ZERO — use `_iter_api_routes` + endpoint `__module__`.** Remainder: `execution.read` conflates scope with data ownership — a scope cannot answer *"may I read someone else's"*.
- **EFFECT-OUTCOME-UNKNOWN-1** — **VOCABULARY SHIPPED 2026-09-03 (#560), with `EFFECT-PARTIAL-1`.** The runtime had no word for *"dispatched, outcome unobserved"*; `EffectRecord.status` now has `unknown`. Narrowly: a **read timeout after a full request write**, the only genuinely ambiguous phase — DNS failure, refused connection and an incomplete write are knowably NOT dispatched, an ack is knowably landed. **★★ `pending` is now REFUSED as a completion, with an error saying why** — it was the obvious thing to reach for and wrong twice: the TTL job hard-excludes pending rows so an honest ambiguity there is never reaped, and the stale-handler warning fires on it hourly as a malfunction. **★ It is a claim about the WORLD, not the runtime's confidence — an unclassified exception is still `failed`.** Remaining: nothing emits it, and `AT_MOST_ONCE` is still absent from the guarantee frozenset. Source: `TECH_DEBT.md`.
- **QUOTA-ACCRUAL-ORPHAN-1** — **LIVE functional break, P2 only by adoption.** `SyscallDispatcher` step 4 ACCRUES usage (creating the snapshot when absent); only `ExecutionPipeline`'s `mark_completed` REAPS it — **nowhere else in `AINDY/`**. So any dispatch OUTSIDE the pipeline accrues forever. **★ `mcp-server` dispatches with no `execution_unit_id`, so everything lands on the key `""`: first call free (no snapshot), then ONE bucket shared process-wide, then PERMANENTLY refused past 100 syscalls with `RESOURCE_LIMIT_EXCEEDED: eu ''` — a long-lived stdio session just stops.** ★ Redis makes it WORSE (shared key → deployment-wide, survives restart). **★ Do NOT fix by early-returning on an empty id** — that restores stage 1 (no budget at all) and hides the unreaped accrual; prefer refusing an empty `execution_unit_id` at the seam, after checking who else omits it. Promotes to P1 on a second non-pipeline caller (a CLI — `CLI-EXEC-SURFACE-1`).
- **CLI-EXEC-SURFACE-1** — **★★ REFRAMED 2026-08-22: NOT a CLI entry. A terminal command is a TRANSPORT over the syscall vocabulary; only the vocabulary is ours.** `mcp-server` is that shape shipped (`mcp_server.py:204` wraps allowlisted `SYSCALL_REGISTRY` entries, each ending at `dispatch_syscall`) — **a transport cannot grant authority it does not have.** Real question: **is the OPERATOR half meant to be syscall-addressable, or is HTTP its only mediation?** Of 24 syscalls the EXECUTION half is (`flow.run`, `nodus.execute`, `agent.*`, `job.submit`, `memory.*`); resume, flow list/get, queue+DLQ, trace, health are **routes only**. **★ An operator syscall opens THREE doors at once** (`/platform/syscall` is route-ungated by decision, MCP allowlist, any CLI) — DLQ drain reachable by an LLM client is a DECISION. **★ Transports still own identity (`INITIATOR-IDENTITY-1`) and call setup (`QUOTA-ACCRUAL-ORPHAN-1`). ★ Do NOT build a CLI to answer this.** Scope: `CLI_EXECUTION_SURFACE_SCOPE.md` §8.
- **ROUTE-AST-UNWIRED-1** — the boot-time route AST proof exists and **never runs against the application**; `routing.py` calls the request-time *wrapper*, a different function with a near-identical name. **The defect is the CLAIM, not the absence. Do NOT wire it as-is — by its own test it raises on a route that works today.**
- **QUEUE-DURABILITY-CLASS-1** — `_fallback_to_memory_backend` swaps durable Redis for in-memory with no per-job durability class. Lower severity than it reads: `AINDY_REQUIRE_REDIS` makes the fallback raise, and it already classifies `UNSAFE_DEGRADED`. Fold into the ownership contract.
- **ORCHESTRATOR-SPLIT-1** — durable work state lives in **four** stores with four recovery paths and no shared transaction. **Do (b) first: publish the ownership contract** — the split may be correct, but undocumented it can be neither relied on nor reviewed. **★ Corrected 2026-08-18: the filed entry said THREE and missed `nodus_lang_workflow`, a SQLite `LocalWorkflowStore` that independently reimplements this runtime's whole durability vocabulary**. Failure mode is crash-only: `continue_crashed_agent_runs` restarts a partially-executed segment from step one, so a guest that rehydrated its own claim/wait state disagrees with the host, undetected. **★★ Worse than untracked — UNCONFIGURED, and it loses data: `AINDY/` has ZERO references to it, so the store roots relative to the worker's CWD — which no spawn path sets — in a directory with NO compose volume.** Store 4 IS injectable (`runner.py:437`). **★ 2026-08-19: NOT fixed by `GUEST-CONFINE-1`'s closure, against prediction — that bounded the VM's `allowed_paths` and left the PROCESS cwd untouched, so store 4 still roots wherever the worker started. Needs its own fix.**
- **EFFECT-PRECONDITION-1** — *(Aider research)* an effect cannot declare the version of the world it expects; `EffectRecord` keys on `sha256({action_type, input, scope})` — the identity of the **request**, never of the state it acted on. **★ The reference implementation is Aider's Git discipline, verified in its source: dirty-commit establishes the precondition, the commit hash is the version token, and `/undo` refuses on four separate mismatches.** **★ The design answer, and it is cheaper than what we were heading toward: the version identity is whatever the external system's own mechanism produces — record it, carry it, refuse on mismatch, NEVER reimplement it. Content-addressed snapshots inside the runtime is the wrong shape.** **Genuinely premature — it needs an external mutable resource the runtime actually mutates, and there is no filesystem syscall and no `sys.v1.repo.*`, correctly. Build after `FS-SCOPE-1` or not at all.**
- **EFFECT-MANIFEST-1** — *(Aider research)* **record-only, nothing is broken.** The reframe: plan-once is not about planning, it is about **knowing the effect set before executing it**. **★ The uncomfortable half: Aider's parse-validate-apply (three stages before the first byte hits disk) satisfies our own central assumption STRICTLY BETTER than we do — we derive the capability set from a plan the model produced without having seen the state it will act on.** One manifest per run / none per turn / several per turn are three granularities of one shape. **Do not build before `FS-SCOPE-1` + `EFFECT-PARTIAL-1`** or it carries only the verb-shaped capabilities the token already has.
- **EMBEDDED-FLOOR-1** — *(Aider research)* no supported profile below `single-instance`, which declares `postgres: True` (`deployment_contract.py`). A consumer shaped like a library in a terminal is **out of contract by declaration, not omission** — which is the honest position, but it is a *no*. **★ This is a soak-and-deployment gate, NOT a capability gap: nothing says the single-process case requires Postgres in a way SQLite could not serve (`AINDY_ALLOW_SQLITE` exists, the whole unit suite runs on it). What is missing is a profile that DECLARES the reduced guarantees and a tier that ASSERTS them — work, not invention.** Keep separate from `DEPLOY-TARGET-1/2`, which scale *up*.
- **WAIT-TYPED-CONTRACT-1** — *(MAF research)* a resume payload is **trusted, not checked**: `register_wait` keys on `event_name` + optional `correlation_id`, and nothing binds the resume payload to a schema or ties a response back to the node that asked. **★ The finding is the asymmetry, not the deficiency — `SyscallDispatcher.dispatch()` validates syscall inputs AND outputs against declared schemas; the wait path, which accepts outside data after an arbitrary delay across a restart, validates nothing. The looser gate is on the less trusted input.** Our durable wait is **stronger** than MAF's (theirs dies with the process) — layer a typed pending-request record ON it, do not replace it. **P2 because no exploit path is claimed; it becomes P1 the moment a wait is resumable by a less-trusted caller (webhook, MCP client, connector).**
- **OTEL-GENAI-SEMCONV-1** — *(MAF research)* our OTel + Prometheus + causal `SystemEvent` graph is **richer than the GenAI semantic conventions and aligned with none of them**, so standard tooling cannot read our traces. **★ Adopt the conventions, NOT the mechanism** (MAF's MRO layering solves a problem we don't have). Naming is the whole value. Filed rather than done because attribute names are a **public surface**: additive first, both emitted for a release, documented removal. **Scope: naming only — content capture (prompt/response bodies on spans) is a separate data-handling decision, which is why MAF ships it opt-in.**
- **RECOVERY-GRANULARITY-1** — *(LangGraph research)* **recovery granularity is welded to scheduling granularity: the runtime checkpoints at the boundary of the unit it schedules, so control flow INSIDE that unit is invisible to recovery.** **★ We have the repair at one layer and did not carry it down: the flow layer commits `FlowHistory` with `input_state`+`output_patch` BEFORE the snapshot advance (`runner.py:347-359`) — pending-writes-then-checkpoint. The agent layer does not; `AgentStep` is a post-segment batch write, so `_count_completed_segments` restarts a partially-executed segment from step ONE.** **★ P2 not P0 — `DUR-2` means mediated effects do not double-fire, so the re-run is CORRECT; what it costs is WORK — re-issuing every LLM call in the segment.** **★★ Worked reference (DBOS): `operation_outputs`, one row per step keyed `(workflow_uuid, function_id)`, checked before every step to REPLAY the recorded output. Step granularity — exactly ours; its monotonic ORDINAL is cheaper than a vector clock and the thing to build FIRST.** ★ Does NOT reopen the declined kernel-replay decision — see `ECOGAP-1`'s taxonomy row 4.
- **RETRY-CLASSIFY-1** — *(SWE-agent research)* **retryability is decided by SUBSTRING MATCHING on a lowercased error string** — `_NON_RETRYABLE_SUBSTRINGS` at `retry_policy.py:95` (`"404"`, `"invalid"`, `"not found"`, `"permission"`…). **★ Wired in FIVE places, not a helper awaiting adoption:** flow-node retry, `nodus_adapter.py`, registered as a NODUS HOST FUNCTION, and EMITTED INTO GENERATED AGENT PLANS (`agent_plan_compiler.py`). Concrete false positives: `"404"` matches `"took 404ms"`; `"invalid"` matches `"invalidated cache"`; `"not found"` matches a DNS message. **★ P2 because the direction is safe — a false positive GIVES UP rather than duplicating an effect — but it is SILENT: nothing records that a classification fired, so it is indistinguishable from a hard failure.** **★ Build WITH `RETRY-CONTEXT-1`** — classify and carry-forward are two halves of one payload. ★ Gotcha: `is_retryable_error`'s docstring says the system does not use it — stale.
- **RETRY-CONTEXT-1** — *(GPT Engineer research)* **a retry re-attempts the same call; it cannot make a better-informed one.** `execute_with_retry(fn, …)` (`retry_policy.py:224`) calls `fn()` with **no argument carrying the prior failure** — it classifies the error to decide *whether* to retry, and sleeps. `last_error|failure_context|error_context` hits only `plugin_host` reporting fields. **★ Derived twice as two halves of one primitive: gpt-engineer names *why the last attempt failed*; Aider names *which sub-units failed* (→ `EFFECT-PARTIAL-1`, which does NOT cover the whole-call case).** **★ Runtime-level because the runtime owns the loop:** three hand-rolled retry loops across two codebases exist largely to carry a string forward. **★ The boundary that keeps it small — the runtime CARRIES, it does not INTERPRET** (precedent: `is_retryable_error` classifies, never interprets). Guard rails: bound the carried history, no authority and no effect, no caller branching on error shape.
- **PROGRESS-CHANNEL-1** — *(Codex research, its N5)* an execution reports a result or nothing; **no partial-output surface exists** — zero `StreamingResponse`/`EventSourceResponse`/`text/event-stream` on any execution surface (the MCP server's SSE is a different surface). Surfaced only by an *interactive* comparator; nothing in the current batch-shaped workload asks it. **★ It is a runtime primitive because of three properties that are also its guard rails: carries NO authority, constitutes NO effect (no `EffectRecord`, not replayed, not in the idempotency key), attaches to the TRACE. The failure mode to design against is a progress channel quietly becoming a delivery guarantee — best-effort by construction, stated in the contract, not discovered.** Note `agent_continuation.py:11` already records the same boundary from the durability side; this makes mid-segment state **observable**, not durable.
- **LINT-FORMAT-1** — P3, cosmetic, **filed because CLAUDE.md advertised a command the repo does not satisfy.** The Commands section listed `ruff format AINDY/` beside `ruff check`; CI's `Runtime Lint` runs **`check` only**, and `ruff format --check AINDY tests` reports **457 of 559 files would be reformatted** (259 under `AINDY/`, 198 under `tests/`). So the tree has never been formatted and running the documented command produces a ~450-file diff. **★ Do not fix by formatting the repo in one sweep** — that rewrites almost every file, destroys `git blame` on all of it, and buys nothing CI checks. If it is ever wanted: format, then add `ruff format --check` to `Runtime Lint` in the same PR, because formatting without enforcing just resets the clock. Note `line-length = 120`, so `format` and `check` do not disagree — this is drift, not a conflict.
- **TEST-ORDER-REGISTRY-1** — P3, latent. `test_platform_only_startup.py` asserts against the GLOBAL tool/capability registries and **passes only because it sorts alphabetically before every `test_tool_*` file.** Run `pytest tests/unit/test_tool_session_handle.py tests/unit/test_platform_only_startup.py` in that order and two of its tests fail with an EMPTY capability set — verified on `main` with no local changes, so it predates the TOOL-SEAM work that surfaced it. **★ It is a green check that proves less than it appears: CI is green because of a filename, not because the isolation holds.** Harmless today and it bites the moment a suite is renamed, a file is added earlier in the alphabet, or anyone runs `-p xdist` / `-k`. Fix is registry isolation in that suite's fixture, not reordering.
- **SCOPE-NAMING-1** — *(found while checking the Codex audit)* P3, cosmetic. **Filed because the source already claims it is filed** — `auth_service.py:601` says "`SCOPE-NAMING-1` tracks the rename" and the id existed nowhere until 2026-08-17. `enforce_api_key_scope` now gates **every** caller, not just API keys (JWT exemption removed by HTTP-SCOPE-GAP-1), so the name is narrower than the behaviour. **★ Deliberately not renamed: 41 call sites across 14 route files, and a missed call site on a security dependency fails OPEN. If ever done — alias, mechanical migration, pin the old name by test, then delete. Never rename in place.**
- **DEBT-COMPAT-1** — **★★ REOPENED 2026-08-18 (P2): the deferral rationale "only one version of each exists today" is FALSE.** `C:\dev\claw` carries `aindy_runtime-1.4.0` against this repo at **2.4.0** — a full major behind and **below the floor we advertise ourselves** (`recommended_runtime_requirement` = `>=2.0,<3.0`). **★ Root cause: `aindy-runtime` is declared as a dependency NOWHERE in that consumer — no pin exists, so no pin can go stale visibly; the version survives only in a README table, which is where it rotted.** **★ Our half is `ROUTE-AST-UNWIRED-1`'s shape: `runtime_compatibility.py` publishes a policy saying consumers MUST declare a bounded range, serves it on `/api/version`, and NOTHING reads it — the policy is violated by the only consumer and the mechanism stating it cannot observe the violation.** Cheapest fix is one comparison at a call site that already fetches it; **warn, never refuse.** Do NOT close with a policy doc — the policy is written, served, and unread.
- **INITIATOR-IDENTITY-1** — *(OpenClaw research)* **the identity that initiates work is not the identity the runtime authenticates.** `tenant_context.py:13` — `tenant_id == user_id`, **which holds only while work is REQUESTED.** An inbound-event-driven consumer has one authenticated operator and N end-user identities the runtime cannot name. **Collapses: memory namespaces per operator not per peer (one peer can recall another's context), `MAX_CONCURRENT_PER_TENANT` becomes a deployment-wide cap, and the audit trail says "the operator did this" when a peer asked.** Needs an *acting subject* beside `user_id` — **attributed, not authenticated**, and honest about which it holds. **★★ Shipped by a peer: LiteLLM budgets at `end_user` scope — an asserted identity used for limits, never for authorisation. ★ The rule that keeps it safe: an asserted subject may only CONSTRAIN, never widen or SELECT** — namespacing memory by it would be a read-authorisation decision in accounting clothes. **★ Do NOT make the peer a `User` row.** P2 today, **P0 the day an inbound-driven consumer ships**: first symptom is cross-peer memory recall.
- **LEASE-FENCE-1** — *(Pi research)* `BackgroundTaskLease` has `owner_id`+`expires_at` and **no fencing token**; `fence|fencing|epoch` returns **zero** under `AINDY/`. **★ Expiry bounds how LONG two leaders coexist and does nothing about what the stale one WROTE — a process that lost its lease to a GC pause learns it at the next renew, and acted as leader throughout.** Reference shape: a conditional upsert that steals only an expired lease and increments `fence` on takeover, so a stale writer is **refused** rather than asked to notice. **★ P2, not a live corruption path** — it guards leadership, not writes, and those jobs are largely idempotent; filed because the fix is one integer column and the failure is INVISIBLE when it happens. Check which leader-only job is least idempotent before fencing uniformly; store 4 (`nodus_lang_workflow`) has an unfenced `claim` too. **★★ Second witness: Temporal fences shard ownership identically. ★ It was named in a June audit and NEVER FILED — those surviving-gap lists are an unworked filing queue.**
- **AUTHORITY-LIFETIME-1** — *(OpenHands research)* **the capability token is bound to the clock, not to the execution it authorises.** `capability_service.py:25` — `TOKEN_TTL_HOURS = 24`, and `revoke|revocation|invalidate` returns **zero** in that module. **A token minted for a run that finished in 90 seconds stays valid for the rest of the day**; nothing about reaching a terminal state invalidates it. Reference: OpenHands binds its session key to lifecycle — valid only while the sandbox is RUNNING, nulled on pause, rotated on resume. **★ Composes rather than competes: `capability_ceiling` answers WHAT MAY THIS BEARER DO (ours, stronger); RUNNING-only answers WHILE WHAT IS TRUE (theirs, we have no analogue).** **★ The real cost is turning a stateless HMAC check into a stateful one on the hot path** — settle where the check lives, whether a NEGATIVE cache is used, and fail-open-vs-closed before writing code. Not `AUTHORITY-VALUE-1` and not `KEY-SCOPE-ESCALATION-1`.
- **EVENT-OUTBOX-1** — **system events are buffered IN MEMORY and emitted AFTER the work commits.** `execution_signal_helper.py:16` appends to a ContextVar and returns a *provisional* UUID; the handler commits its own work (`runner.py:359`); the buffer flushes only at `pipeline.py:171`, and `signals.py:111+` swallows emit exceptions. **★ A crash in that window leaves the work durable and the record of it gone — silently.** P2 because it loses the **audit/causal record**, not an effect. **★ The mechanism behind "better index, weaker record": `build_trace_graph` indexes a record that can lose rows, and a missing row reads as "the work never happened".** **★ Check first whether `memory_capture_engine`'s `get_downstream_effects` read makes a lost event a missed capture** — that decides observability item vs memory-loop item. **★★ Cheaper than an outbox (DBOS): their event store IS their workflow store, one connection, no window. `SystemEvent` already shares our Postgres.** **★ Do NOT "fix" by emitting eagerly** — that trades this for events recorded for work that rolled back.
- **SYSEVENT-RETENTION-1** — *(out of `FR-18`, 2026-08-22)* **`system_events` grows without bound and nothing prunes it.** Stale job logs and expired `EffectRecord` rows are pruned; the table every execution, signal and causal edge lands in is not. FR-18 removed the loudest writer (a full health snapshot per healthcheck probe — 3.3 GB of one 3.8 GB database), not the class: growth is now proportional to work done, still unbounded. **★ Not "add a third cleanup job": a `SystemEvent`'s value is uniform by TYPE, not by age.** `EVENT-OUTBOX-1` reads a missing row as the work never happening, and `AUDIT-CORRELATION-1` joins `EffectRecord.action_id` by convention with NO FK — a blanket age policy deletes the audit trail and keeps the keepalives. Shape: a retention class per type, defaulting to KEEP so a new type is never deleted by omission, and **log what was dropped by type — a silent prune is indistinguishable from a lost write.** **★ Do NOT close by documenting a `DELETE`** — that is the FR-18 mitigation every deployment already runs by hand.
- **AUDIT-CORRELATION-1** — three joins the audit trail cannot make. (1) and (2) fall out of AUTHORITY-VALUE-1 / EXEC-ENV-BIND-1; only `EffectRecord.action_id`→`SystemEvent` is standalone (joined by `trace_id` convention, no FK).
- **EGRESS-INPROC-1** — a re-homing, not a build. `egress_guard` is off by default and **its own docstring names both bypasses**. Fold the egress decision into TOOL-SEAM-ISOLATION-1's provider.
- **DISPATCH-ADMISSION-1** — deliberately deferred. **Do NOT build a general hook system** — an interception seam runs someone else's code in the kernel process, which the Tiered Isolation Contract reserves for Tier 1.
- **MEM-EXPAND-DEAD-1** — `expand()`'s semantic-neighbour half returns `[]` on every call and always has (pgvector 0.4.2 returns `ndarray`, the guard tests `isinstance(list)`). **pgvector 0.5.0 fixes it — which is exactly why dependabot #390 was held, not merged:** taking it turns expansion on by default in the path that caused RT-MEMTXN-LEAK-1's pool exhaustion. **Widening the guard is not the safe option it looks like.**
- **DB-NODUS-BUDGET-1** — both fixes shipped 2026-08-01 (idle-in-transaction cap 30s→60s + opt-in `AINDY_MEMORY_RECALL_OWN_SESSION`); remaining is soak then flip. **Do NOT "fix" this by rolling back the caller's session** — RT-MEMTXN-LEAK-1 tried that and it broke `test_agent_approve_idempotency`.
- **MCP-SDK-2X-1** — `mcp 2.0.0` removed the 1.x API `nodus-mcp` is built on. Both install sites are capped `<2` (`pyproject.toml` **and** the separate CI step — a cap must be repeated in both). **Not a test bug — do not skip the live test to go green.** **★ A second instance ran in reverse and is now RESOLVED (2026-08-19): `nodus-mcp` capped `nodus-lang<5.0.0` and blocked a nodus major; 0.1.3 floated it unbounded. The FIRST instance (`mcp<2`) is still live — keep both caps.** **★ The rule the second instance established: never fix this by isolating the MCP tests** — pinning a nodus major that `nodus-mcp` caps below makes `aindy-runtime[mcp]` **uninstallable** (`ResolutionImpossible`), so green CI would ship a broken extra. **Ordering is a sequence, not a deadlock: nodus X publishes → nodus-mcp accepts `>=X` → the runtime bumps BOTH in ONE PR across ALL THREE sites.** ★ A prophylactic cap on a fast-moving first-party dep turns every major into a two-repo release train.
- **LOCKFILE-PLATFORM-1** — a Windows-generated `platform/package-lock.json` cannot satisfy Linux `npm ci` (missing packages are `bundleDependencies` of an optional wasm32 package, so a machine that never installs it never walks that subtree). Resolver shipped: **`Platform Lockfile` workflow**, dispatch-only. Stays open — every future rolldown/oxide bump needs the same treatment. **Process rule: verify a lockfile change with `npm ci`, never `npm install` + build** (install silently repairs the mismatch). Expect a benign `"peer": true` diff; read *"Packages added"*, not the changed bit.
- **DEP-UPGRADE-DEFERRED-1** — OTel and the UI major cluster both closed. **Two lessons kept:** the otel packages are version-locked so single-package PRs always die `ResolutionImpossible`; and **grouping is necessary but not sufficient** — dependabot resolves each package independently, so hand-align and verify with `pip install --dry-run`. react-router 7→8 deferred (needs a ui-kit release first).
- **C3** — non-Linux strong sandbox. **C2 closed 2026-06-06** (container-grade, escape-tested); C3 is **open** — both supported-platform tuples are `(PLATFORM_LINUX,)`, so non-Linux hosts reach `container-sandbox-certified` but not `strong-sandbox-certified`. Pre-scoped in `C3_NON_LINUX_STRONG_SANDBOX_PLAN.md`.
- **CLI-1** — lazy settings getter / module-level import hazard. Deferred post-1.0.
- **CLI-SANDBOX-FORMAT-1** — `sandbox` raw JSON output wall. Deferred to 1.0.1.
- **SYSMAX-1 / -3 / -4** — thread-mode 100-job cap still the `.env.example` default (prod overlay enforces distributed); memory bytes not enforced per EU (needs OS integration); per-EU syscall and wall-time caps advisory.
- **TIER3-10** — `async_job_service` coupling. Architectural, no bounded fix.
- **DEPLOY-TARGET-1 / -2** — cloud deployment manifests; multi-tenant SaaS readiness gate. Triggers: first cloud deployment / first multi-tenant operator.
- **BILLING-1..5** — deferred until commercial launch. Source: `docs/runtime/MONETIZATION_AUDIT.md`.
- **LAYER-1..5** — layer boundary violations. All deferred.
- **ROUTE-EXTRACT-\*** — remaining candidates: `memory_router` (split required), `coordination_router` (AgentRegistry ownership gap).
- **PACK-DEBT-\*, DEBT-COMPAT-\*, TENANT-\*, COMPAT-\*, DATA-\*, LOCAL-\*** — packaging, dependency and architectural gaps.
- **IDEM-12** — OPEN, P2, **latent**. `undo_run_effects` selects effects by `status == "success"`, never marks them reversed and never consults `effect_reversals`, so a second `sys.v1.agent.undo` re-invokes **every** compensator (double refund) and duplicates audit rows. Not live only because **zero compensators are registered** — it goes live with the first one. `EXACTLY_ONCE` (IDEM-11) is defense-in-depth, **not the fix**: the gate is default-off and keys on `(name, payload, scope)`, so a deliberate second undo still lands. **Do not close by relying on the IDEM-11 flag flip** — that makes reversal correctness depend on an env var, the shape IDEM-10 already paid for.

### Open — programs and multi-item prefixes

- **APP-FR-\*** — app-side feature requests from `aindy-apps-monolith`. **Next available: FR-23.** FR-1..13, 16..18, 20..22 and FR-19's runtime half shipped. **★ FR-22 found that `/apps/*` is NOT an ownership boundary — 35 such routes are served by the RUNTIME alone; the published inventory is `AINDY/route_inventory.json`.** Open: **FR-14** (`bootstrap-schema` refuses additive drift → crash-loop under `set -e` + `restart: unless-stopped`; branchable exit codes shipped #450 — **3** additive-reconcile *the only one safe to automate*, **4** offline-migration, **5** manual-repair. **★ STILL OPEN, the half that prevents recurrence: the upgrade path is never exercised against an EXISTING database** — CI builds a fresh one where `create_all` makes the columns, so no green check can see this class; the same blind spot hid FR-8), and **FR-6 items 2+3** (blocked structurally — the runtime ships no `email` connector). FR-18's retention half is `SYSEVENT-RETENTION-1`.
- **ECOGAP-\*** — ecosystem capability gaps (`ECOGAP-1..6`), roadmap gaps rather than classic debt. ECOGAP-2 is owned by C2/C3, ECOGAP-3 extends MEMORY-EMBEDDING-PROVIDER-1 — **don't double-track**. ECOGAP-1 Phases 1+2+2a and ECOGAP-4 G4b (MCP client + stdio server) shipped opt-in. **G4a remains built-but-INERT** — every guard vacuous until a policy is registered.
- **RTR-\*** — runtime roadmap (`RTR-1..8`). RTR-1/5/6 closed; RTR-2/3/4/7 harden-halves done, BUILD halves deferred (RTR-3 full AgentRun↔FlowRun unification; RTR-4 remaining = soak+flip `AINDY_DELEGATION_PRIVATE_MEMORY`). RTR-8 stale/closed. **RTR-4 gotcha: delegate writes take the deferred capture path, so `MemoryNodeDAO.save` is the write chokepoint, not the syscall.**
- **DOCS-\*** — docset findings. DOCS-BUCKET-A-1 and DOCS-STALE-1 closed; **`Runtime Docs Validation` now asserts `last_verified` is real and `>= 2026-05-17`** (it only checked key presence before). **DOCS-COVERAGE-CLAIM-1 half closed:** 6 docs cited 8 test files that never existed; all four areas now have suites (249 tests) *and* are made to actually run. **★ The pattern worth keeping: four separate docs mis-stated plugin-layer routes as runtime-owned. Check `APP_ROUTERS` + `ROUTE_OWNERSHIP_INVENTORY.md`, never file presence.** **★ Gotcha: `ResourceManager.can_execute` returns `(True, None)` unconditionally under `settings.is_testing`, so quota enforcement is vacuous in tests** — and `is_testing` is a pydantic *property*, so patch it on the class.
- **SYSCALL-STABILITY-\*** — `-1` fixed 2026-08-13. `SyscallEntry.stable` (advertised maturity) and `_STABLE_SYSCALLS` (rename guard) measure different things and may legitimately differ. **Two gotchas: the duplicate-registration guard is on `SyscallRegistry.__setitem__`, not `register_syscall`; and `stable` defaults to `True`, so an unset flag is not necessarily accidental.** Open app-side: the monolith defines `register_all_domain_handlers` twice.
- **AUDIT-INVARIANTS-VERIFIED-1** — **RECORD, not a defect.** The claimed guarantees were swept, not just the gaps; most held. **Two did not:** the boot-time route proof (→ ROUTE-AST-UNWIRED-1), and *"output validation is warn-only"* — **FALSE for `stable` syscalls**, which return an error envelope; only *experimental* ones warn. **★ Method note: verify the guarantees, not just the gaps — both errors were in "already covered" sections, the part of an audit least likely to be re-checked.**

### Recorded decisions — considered and declined, do not re-litigate

- **HOOK-PRECEDENCE-1** — *(ADK research)* first-non-`None`-wins hook semantics: **declined.** Our ~40 `register_*` hooks are either **keyed** (one handler per `route_prefix`/`entity_type` — the key disambiguates) or **run-all-and-collect** (nothing is discarded, so precedence is not a question). **★ The objection is substantive, not stylistic: first-wins makes a handler's effect depend on registration order relative to handlers it cannot see, so one plugin can silently suppress another and nothing records it.** **What would change the answer:** a genuine policy-arbitration point where exactly one handler must win and the key cannot express which — none exists today, and if one appears the right shape is an explicit declared arbiter, the same conclusion `DISPATCH-ADMISSION-1` reached. Not `CAPABILITY-PROVIDER-TIMEOUT-1`, which was a defect in a run-all path.
- **Kernel deterministic replay** — declined in `ECOGAP-1`'s Phase 3 reframe. **★ That entry now carries a three-way taxonomy, because six audits have cited "replay" at us meaning different things: (1) event-sourced state fold — SHIPPED as DUR-4; (2) deterministic code replay (Temporal) — DECLINED; (3) ordering replay — specified in `FLOW-PARALLEL-1`.** #2 stores non-deterministic *results* and re-runs the code with them injected so the world doesn't move; it is declined because determinism is a VM concern not a kernel one, because forward-resume never re-executes code so the problem doesn't arise, and because it is a constraint on every line of workflow code rather than a feature. **The honest residual is not "we lack replay" — it is "the single re-run node's un-mediated side effects."**

### Standing rule — not an item

- **★★ A TEST-MODE SHORT-CIRCUIT PLACED ABOVE THE REAL DECISION MAKES THE REAL PATH UNTESTABLE
  WHILE EVERY TEST PASSES.** Two instances, both found in `FR-15`'s own path within a fortnight,
  and both make a soak vacuous rather than failing:

  - `async_heavy_execution_enabled()` returns False under `TESTING`/`TEST_MODE` **before** reading
    its flag. `pytest.integration.ini` sets both, so **the ASYNC dispatch branch was unreachable
    from every test in this repo and always had been.**
  - `get_queue()` returns an `InMemoryQueueBackend` under the same two variables, **before**
    checking `REDIS_URL`. So **no test here can reach the Redis backend.** A soak written the
    obvious way enqueued and dequeued inside one process and passed 6/6 while proving nothing.

  **This is worse than an untested path, because the test that appears to cover it passes.** The
  second case was caught only by an unrelated hunch — asserting `backend_name == "redis"` on the
  strength of `QUEUE-DURABILITY-CLASS-1` — which then failed immediately and named the cause.

  **Rules:** when writing a soak, **assert the mechanism you think you are exercising is actually
  the one running** (the backend, the branch, the mode) before asserting anything about its
  behaviour. And when adding a test-mode guard, put it **below** the switch it is guarding, or
  give it an explicit opt-in that test mode cannot veto — `async_scheduler_dispatch_enabled()` is
  the worked example. Expect a third instance; grep for `TEST_MODE` above a decision, not after it.

- **★ A SOURCE-TEXT ASSERTION IS A SUPPLEMENT, NEVER THE COVERAGE — four failures in one
  fortnight.** A test that reads code as text cannot tell code from a comment, cannot tell
  presence from reachability, and cannot tell order from behaviour. Observed: an assertion
  matching its own explanatory comment that *quoted* the bad pattern; a `register_flows()` string
  match satisfied by `# register_flows()`; a two-line ordering check that passed with the branch
  disabled. **Prefer the AST when you must read source** (a comment cannot satisfy a `Call` node),
  and prefer driving the real entry point when you can. `ROUTE-GUARD-1` said this about routes; it
  generalises.

- **★ Module-import-time env reads are invisible to behavioural tests.** Three bugs share this shape: FR-10 (`settings = Settings()` at import crash-looped the container), `ResourceManager._get_backend()` (caches the Redis-vs-in-process choice on first call), and the `AINDY_REDIS_URL` alias in `rate_limiter.py` — which survived a cleanup that believed it had removed the alias everywhere, because **nothing about the running limiter differs when the alias is honoured**. **When auditing env-var handling, grep the source; do not trust a passing suite.**

### Closed — kept as one line because the rule still bites

- **TOOL-SEAM-ISOLATION-1** — **CLOSED 2026-08-19 (A+B+C1+C2):** revocable DB handle; `register_tool(..., isolation=<class>)` refused fail-closed; a return-contract counter that MEASURES rather than rejects (the effect has landed by then); a worker subprocess so a declared tool runs OUT OF PROCESS. **★★ C2 has NO FALLBACK — a crashed, timed-out or unstartable worker means the tool does not run. The opposite of the nodus adapter, which spills to a fresh subprocess: there both paths give the same guarantee; here falling back would run a tool that asked to be confined UNCONFINED.** **★ `db` is None in the worker (18/18 take it, 0 use it); a worker rebuilds `TOOL_REGISTRY` from the plugin stack, so an ad-hoc parent registration is invisible there.** **Gap by design: UNDECLARED tools run in-process.** Scope: `TOOL_SEAM_ISOLATION_SCOPE.md`.
- **IDEM-11** — **CLOSED 2026-08-19: `AINDY_SYSCALL_IDEMPOTENCY` defaults true** (`=0` disables). Dedups the 8 `EXACTLY_ONCE` syscalls on `(action_type, input, scope=execution unit id)` — a retry within ONE run replays; two calls in DIFFERENT runs are untouched, which made the flip safe. **★★ NOT exactly-once under contention — measured, 8 concurrent identical calls ran the handler TWICE** (strict at-most-once needs advisory locking). **★ Watch ALL labels of `aindy_effect_gate_outcomes_total`: THREE silent paths found and closed (#511, #516), the third being the COMMON one** — a loser that reads the committed `pending` row never races the insert, so it missed the only counted branch. **★ Does NOT close `IDEM-12`.** **★ `_durable` (DUR-2) engages the gate for ANY syscall, bypassing flag and declaration.**

- **AUTHORITY-VALUE-1** — **CLOSED 2026-08-19: the clamp is ON by default.** `child_context()` could WIDEN the grant the calling frame supplied; it now narrows only, dropping a widening with a WARNING (`AINDY_CHILD_CONTEXT_CLAMP=0` reverts). **★ It shipped opt-in on a conclusion right about the mechanic and wrong about the cost: clamping DOES intersect the app's `_dispatch_owner_syscall` to EMPTY — still pinned — but 18 of the 19 widening functions are NEVER REGISTERED, and the one live caller widens for an OPTIONAL lookup inside `try/except` with a full fallback. Count: 1 degradation, 0 outages.** **★ The shape to remember: an executable fact had a wrong inference layered on it and nobody re-measured for three months.**
- **NODUS-UPGRADE-2** — CLOSED 2026-08-19, `nodus-lang` 5.0.1 → 5.0.4. **★ Filed P3-routine; it was a SECURITY fix** — `<=5.0.2` bound `GLOBAL_MEMORY_STORE` at import, so every `NodusRuntime` in a process shared one guest memory dict, readable from any `.nd` script. **The rule: read the intervening release notes BEFORE assigning severity — a severity assigned from version distance is not an assessment.** **★ Upstream bugs invalidate downstream docstrings, and nothing greps for that:** `nodus_worker_pool.py` claimed a reused process *"never leaks state between runs"* — false, because `run_one` cannot reset a module global inside a dependency. Reproduce before believing either way.
- **ROUTE-EFFECT-BYPASS-1** — CLOSED 2026-08-16. **★ Trap: `memory.write` REPLACED the caller's `extra` rather than merging, so a naive rewire was silent data loss behind a 201.** **★ A syscall that adds a mediation hop and no authority granularity just relocates the same undifferentiated power** — hence `memory.link`, which `memory.write` does not grant. **Still bypassing (1, pinned by a test): `POST /nodes/search`** — rewiring it would change SEMANTICS under cover of a mediation fix.
- **CAPABILITY-PROVIDER-TIMEOUT-1** — FIXED 2026-08-16. Every tool capability check spawned a subprocess per provider — 10 lookups = 10 spawns / 56.4s before, 1 / 11.4s after. **★ It fails CLOSED, not open — established by running it, after the filing assumed the opposite.** **★ The cache lives ON THE PROVIDER OBJECT, not a module global** — a module-level latch must be added by hand to two registry-reset dicts, and forgetting either reintroduces the bug through its own fix.
- **ISOLATION-DOC-STATUS-1** — CLOSED 2026-08-16. `ISOLATION_MODEL_PLAN.md` said "no implementation has begun" at line 6 and "Scope B1 complete" at line 148. **It sits at the repo root, outside the `docs/runtime/` frontmatter checks — which is why nothing caught it.**
- **MEM-RECALL-N1-1** — CLOSED 2026-08-16. `recall()`'s scoring loop ran 3 queries per candidate to re-read 4 columns the originating SELECT already had; now carried on `_node_to_dict` with two grouped queries for the whole set. Performance-only.
- **SYSMAX-5** — CLOSED 2026-08-16, latent by construction: ~33 scheduler jobs on a default pool of 10, never sized deliberately. Failure mode is a **maintenance brownout** — recovery jobs stop running exactly when the condition they clean up is happening, and nothing raises. **★ Fixed by isolation, not capacity: raising `default` would have been the WRONG fix**, because scheduler threads share the DB connection budget with request handling, so more of them starve the API instead. A test pins total lane threads to half that budget so the trade cannot be made accidentally.
- **AGENT-HARDEN-1..10** — **all CLOSED 2026-07-05/06.** Cancel, HMAC tokens, compensating undo, simulation + virtual tools, LLM fallback chain, verifier, cassette contract tests, capability policy, secret broker, extension signing. MCP is not here — it is ECOGAP-4.
- **NODUS-UPGRADE-1** — bump `nodus-lang` AND `nodus-mcp` across **ALL THREE sites**: `pyproject.toml`, `AINDY/requirements.txt`, and the `Install MCP extra` CI step, which installs directly and so re-resolves a constraint fixed only in the first two. **★ `--no-deps` in CI means pyproject's pins are NEVER applied there** — the effective env is `requirements.txt`, which is how CI tested nodus 4.1.0 for four months while the wheel required 4.2.0. **★ `NodusRuntime.__init__` has NO `**kwargs`, so a renamed deny-flag raises rather than silently unconfining the guest** — that absence is now a test. **Distinguish cosmetic from real before touching the sandbox:** 4 confinement tests went red on 5.0.0 and none was a regression.
- **GUEST-CONFINE-1** — **FULLY CLOSED 2026-08-19** (escape 2026-08-15; residual by `EXEC-ENV-BIND-1` phase 2). The guest VM ran unconfined — a script reached subprocess/network/host env without touching the dispatcher, token, ledger, egress guard or tool registry. **Demonstrated, not inferred.** **★ The residual was a WRONG COMMENT, not a missing line: the source said `allowed_paths` defaults to the cwd and is therefore fine — true of nodus, false here, because NO spawn path sets the worker's cwd, so the guest inherited the SERVER's (`/home/aindy` in Docker, holding `alembic/`).** Now an explicit per-execution scratch root, which also makes `NODUS_ALLOWED_PATHS` inert.
- **RT-MEMTXN-LEAK-1** — CLOSED. **Rules: never hold an open DB transaction on a request-shared session across a slow external call — order the code so the external call precedes the DB work; never `rollback()` a shared session to free its connection; and a memory capture must never enqueue work whose own lifecycle events are capturable (capture → job → capture is a cycle).** **Gotcha: after a commit, touching an ORM attribute silently re-opens a transaction.** **Diagnostic: `xact_age_s == idle_s` cannot distinguish "held across a slow call" from "held by a frame that never returned" — only a stack dump does.**
- **CI-MARKER-1** — CLOSED 2026-08-15. `tests/unit/conftest.py` applies `runtime_only` by default, so a new unit file cannot silently run nowhere. **Everywhere else the old rule still bites: nothing marks a file outside `tests/unit/`, and `pytest.integration.ini` only reaches `tests/integration` — adding a test directory means giving it a job.** **Gotchas: `--collect-only -q` prints `<path>: <count>`, not node ids; exit code 5 is `EXIT_NOTESTSCOLLECTED`, not an error.**
- **FLAKY-1** — CLOSED 2026-08-15 (15 healthy runs across two trees). **Rules that outlived it: when chasing a flake, never pipe the run through `tail`** — three failures were destroyed at the moment they were produced — **and do not conclude from small samples** (this one produced three wrong readings before the fourth run refuted each).
- **MEM-DELETE-1** — core shipped. `sys.v1.memory.delete` is hard, syscall-only, tenant-scoped, irreversible, with its own `memory.delete` scope **not** granted by `memory.write`. **No SDK consumer — nothing calls it yet.** Four opt-in upgrades deferred (G1–G4).
- **NODUS-SYS-SURFACE-1** — CLOSED. Idiomatic `import "std:sys"` routes to nodus's own 4-syscall stub, **not** the AINDY dispatcher; only the bare `sys(...)` builtin reaches `dispatch_syscall`. It could not be aliased, so there is a fail-loud guard in `nodus_worker.py`.
- **MCP-BEHAVIOR-1** — `call_tool()` never raises; check `result.isError is True`. Full note in its own section below.
- **NATIVE-CI-1** — CLOSED. `Native Crate Build (Rust)` runs `cargo build --locked --release` every PR. **Encoded gotchas: `--locked` is the point; no `cargo test` (pyo3 `extension-module` omits libpython, so the harness fails to link); deliberately not path-filtered, since a `paths:` filter on a required check never reports and blocks forever.** ★ A **`push`**-triggered workflow does not run on the PR that adds it; a **`pull_request`**-triggered one does, from the merge ref — so a new workflow can be validated before merge if it is `pull_request`-triggered. Builds on Linux, not MSVC.
- **NATIVE-DISCOVERY-1** — CLOSED. Both crate consumers delegate to `AINDY/memory/native_bridge.py`. **★ Trip hazard: `cargo build` emits `libmemory_bridge_rs.so` / `memory_bridge_rs.dll` — Python imports neither; CI renames, a local build needs it by hand.** **And `sys.path.insert` in priority order puts the lowest-priority path first** — that inversion let a stale debug build shadow a fresh release one.
- **NATIVE-PARITY-1** — CLOSED. Native and Python scorers disagreed on negative `impact_score`. **Severity was defense-in-depth, not live** — `MemoryNodeDAO.save()` clamps at the universal write chokepoint. **★ The regression guard is native-independent on purpose** — parity tests skip without a built crate, so pinning the clamp only there would repeat DOCS-COVERAGE-CLAIM-1 in miniature.
- **EVENTBUS-PUBLISH-LATCH-1** — CLOSED. **Root cause was one field meaning two things:** `_enabled` was both the operator kill switch and the runtime give-up latch, which made a transient blip permanent *and* invisible. Now split: config vs. a `CircuitBreaker`. **Behaviour change: `/health/deep` reports the bus degraded during suspension rather than `ok`.**
- **EVENTBUS-COVERAGE-1** — CLOSED. **★ Mutation-tested 5/7 — the first draft scored 4, because a test asserting an *absence* passes when the wire is broken; it now runs a liveness control first.** **Placement: marked `redis`, NOT `integration`** (that marker trips the conftest skip guard). **Both race pitfalls were real: buses in one process share a hostname-derived `_instance_id`, and Redis pub/sub has no readiness signal — republish inside the polling loop, never a fixed sleep.**
- **ROUTE-GUARD-1** — CLOSED. Every `raise HTTPException` in three routers returned **500**; FR-12's reserved-namespace guard answered 500 instead of 409 for a full day. **★ Why nothing caught it: the tests assert on the route's *source text* and never call it. A route test must call the route** — the status code *is* the contract. **Flagged, not fixed — `ADMIN-PROMOTE-UUID-1`:** the promote route also 500s on a missing user, for an unrelated SQLite-harness-only reason.
- **KERNEL-INIT-DUPLICATE-1** — CLOSED. `AINDY/kernel/__init__.py` was a byte-identical copy of `tenant_context.py`, so two different `TenantContext` classes existed and `isinstance` was silently `False` across them. Nothing had broken because nothing imported it. **All 337 `.py` files under `AINDY/` were hashed — no byte-identical duplicates remain.**
- **TENANT-FROZEN-SHALLOW-1** — CLOSED. `frozen=True` does not deep-freeze; `capability_scope` is now a tuple. **Still open, adjacent: `TenantContext.validate_memory_path` rejects the exact tenant root while `memory_address_space.validate_tenant_path` accepts it — two tenant guards, two answers for one string.**
- **MAS-FLATTEN-1** — CLOSED. `flatten_tree` dropped every node that was a parent of another. **★ The invariant that would have caught it in one line: `len(flatten_tree(tree)) == len(tree)`.** Zero callers, but documented as usable, so fixed rather than deleted.
- **NODUS-WARMPOOL-1** — **CLOSED 2026-08-19: `AINDY_NODUS_WARM_POOL` defaults true** (`=0` restores fresh subprocesses). Any warm-path failure falls back to a fresh subprocess, so it cannot make execution worse than the path it replaces. **★ The prior "it's been soaking in CI" evidence was NOT what it looked like: the integration suite is SEQUENTIAL, and every pool test ran against FAKE processes while end-to-end was deferred to "app-side PG-tier integration" — a consumer that does not exercise it.** Soaked properly by `test_soak_warm_pool_contention.py` (6 concurrent callers vs a pool of 2, real workers, mutation-tested 4/4). **★ Flipping found that nothing asserted the warm path carries DUR-2b's durable-effects signal** — it does; there is now a test.
- **INFINITY-RUNTIME-1** — FULLY CLOSED. **Gotcha: adding a `SystemEventTypes` value trips the frozen-hash baseline — regenerate `tests/baselines/system_event_contract.json` in lockstep.** Remaining is flag-flip after soak, not build.
- **PYPI-PUBLISH-1** — CLOSED. **Release protocol (both halves bit us before): bump the Dockerfile builder-stage pin AND the CHANGELOG in one PR; after the tag publishes, append the `SANDBOX_ESCAPE_AUDIT.md` entry for the gate run.** `Boot Smoke` installs the pinned version from PyPI, so a bump PR skips-green until the tag exists.
- **PLANNER-SUBPROC-1 / INFINITY-COMPLETION-HOOK-BOUNDARY-1** — CLOSED. Stateful callback surfaces run in-process. Full mechanism is in the `_maybe_wrap_runtime_callback` section above — read that, not this line.
- **SDK-SYSCALL-GRANT-1** — CLOSED. Per-syscall least-privilege grants. **Two namespaces (`Scopes` vs capabilities) — do not conflate them.**

### Closed — no live rule; see `TECH_DEBT.md` if you need the history

`MEM-NODETYPE-1` · `LEASE-1` · `REPLAY-1` · `PROMETHEUS-PIN-1` ·
`OPER-DEFER-001` · `OPER-DEFER-002` · `IDEM-1..10` (IDEM-10 closed at the mechanism level
2026-07-11 via the MEB program; next available **IDEM-13**) · `DOCS-BUCKET-A-1` · `DOCS-STALE-1` ·
`C2` · `SYSCALL-STABILITY-1`

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
| **Comparative research index (8 systems: what each produced, what is settled, recurring errors)** | `docs/runtime/COMPARATIVE_RESEARCH_INDEX.md` |
| Runtime execution invariants | `docs/runtime/EXECUTION_INVARIANTS.md` |
| **`ExecutionEnvironmentSpec` design (EXEC-ENV-BIND-1) — design only, no code** | `docs/runtime/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md` |
| Architecture risk (complexity/blast-radius) | `docs/runtime/ARCHITECTURE_RISK.md` |
| Runtime security matrix | `docs/runtime/SECURITY_MATRIX.md` |
| Revocable tool DB handle (TOOL-SEAM-ISOLATION-1 step A) | `AINDY/agents/tool_session.py` |
| **Execution environment vocabulary (EXEC-ENV-BIND-1)** | `AINDY/core/execution_environment.py` — `ExecutionEnvironmentSpec`, `assurance_rank()`, `clamp_to_floor()`, `GUEST_FLOOR` |
| Out-of-process tool worker (TOOL-SEAM-ISOLATION-1 step C2) | `AINDY/agents/tool_worker.py` — one-shot, `db=None`, **no fallback** |
| Effect-gate outcome counter | `AINDY/kernel/effect_ledger.py` — `aindy_effect_gate_outcomes_total` |
| **Published route inventory (FR-22) — regenerate after ANY route change** | `AINDY/route_inventory.json`; `scripts/check_route_inventory.py [--check]`; pinned by `tests/unit/test_route_inventory.py` |
| Operator console panels adopted from the app SPA (FR-21) | `platform/src/components/platform/WebhooksPanel.jsx`, `DeadLetterQueuePanel.jsx`; paths staged in `platform/src/api/_routes.js` — `RUNTIME_ROUTES` |
| **Response envelope discriminator (FR-19)** | `AINDY/core/response_adapter.py` — `X-AINDY-Envelope: v1`, set on the envelope exit ONLY; exposed via `expose_headers` in `middleware.py` or a browser cannot read it |
| **Liveness-probe event digest (FR-18) — read before touching `/health` event payloads** | `AINDY/core/health_liveness_signal.py` — digest + emit-on-change; counter `aindy_health_liveness_events_total` |
| Async-job execution-boundary scope (FR-17) | `AINDY/platform_layer/async_execution_context.py` — `async_execution_scope()`; the contract gate's only exemption besides an active pipeline |
| **Tool seam isolation scope (TOOL-SEAM-ISOLATION-1) — read before acting on that entry** | `docs/runtime/TOOL_SEAM_ISOLATION_SCOPE.md` |
| **CLI as an execution surface — scope (CLI-EXEC-SURFACE-1)** | `docs/runtime/CLI_EXECUTION_SURFACE_SCOPE.md` |
| **Outcome-ambiguity design + the runtime answer (EFFECT-OUTCOME-UNKNOWN-1) — read §5.3, §7, §14 before acting** | `C:\dev\Coding Language\docs\design\v5\03-outcome-ambiguity.md` |
| Cross-repo compatibility policy | `docs/runtime/CROSS_REPO_COMPATIBILITY.md` |
| Runtime → SDK contract | `docs/runtime/SDK_CONTRACT.md` |
| Runtime → UI contract | `docs/runtime/UI_CONTRACT.md` |
| **Nodus-side A2A/MCP packaging handoff (name collision + caps) — all fixes are Nodus's, none are ours** | `docs/runtime/NODUS_HANDOFF_a2a_mcp_packaging.md` |
| Latest app-team handoff | `docs/runtime/APP_HANDOFF_v2.8.0.md` — **NOT a plain `pip install`**: §1 is the mandatory `bootstrap-schema --reconcile` (FR-14, exit 3), §2 the flow-topology quarantine, §3 why distributed dispatch is opt-in and not defaulted |
| Release verification checklist | `docs/runtime/RELEASE_CHECKLIST.md` |
| Cross-repo regression tests | `tests/unit/test_cross_repo_compatibility.py` |
| **Soak harness — concurrency + metric readback** | `tests/integration/soak_harness.py`; guarded by `tests/unit/test_soak_harness.py` |
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
