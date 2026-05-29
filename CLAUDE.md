# aindy-runtime — Claude Code Instructions

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

**`VITE_API_BASE_URL`** is the build-time base for all API calls (defaults to
`http://localhost:8000`). It is distinct from the removed `VITE_APP_BASE_URL`. Tracked
as PLATFORM-UI-ENV-1: the default bakes `localhost` into the bundle, which breaks on
remote hosts — do not silently fix; it interacts with the prod ports story.

---

## Platform UI — build chain

`@aindy/ui-kit` is published to npm (`registry.npmjs.org`) as `@aindy/ui-kit@1.0.1`.
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

## TECH_DEBT.md — IDEM-* numbering

Entries are numbered sequentially. Do not reuse a number.

- IDEM-1 through IDEM-7: open or closed idempotency audit findings.
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

## TECH_DEBT.md — prefix registry

- **IDEM-\*** — idempotency audit findings. Next available: **IDEM-10**.
- **CLI-1** — lazy settings getter / module-level import hazard (deferred post-1.0).
- **CLI-SANDBOX-FORMAT-\*** — `sandbox` subcommand UX findings. CLI-SANDBOX-FORMAT-1: raw JSON wall, deferred to 1.0.1.
- **C2, C3** — cross-platform sandbox tiers.
- **PACK-DEBT-\*** — packaging and dependency findings.
- **DEBT-COMPAT-\*, TENANT-\*, COMPAT-\*, DATA-\*, LOCAL-\*** — architectural gaps.
- **ALEMBIC-FRESH-DB-\*** — alembic migration blank-database safety. ALEMBIC-FRESH-DB-1: closed 2026-05-27.
- **COMPOSE-PGVECTOR-\*** — pgvector extension requirement. COMPOSE-PGVECTOR-1: closed 2026-05-27.
- **PACKAGING-DEP-\*** — pip --prefix bootstrap-package propagation gaps. PACKAGING-DEP-1: closed 2026-05-27.
- **COMPOSE-HOST-\*** — container host binding issues. COMPOSE-HOST-1: closed 2026-05-27.
- **EVENTBUS-REDIS-URL-\*** — Redis URL env var consolidation. EVENTBUS-REDIS-URL-CONSOLIDATION-1: open.
- **PYPI-PUBLISH-\*** — PyPI publish transition. PYPI-PUBLISH-1: open.
- **MONITORING-GRAFANA-\*** — Grafana monitoring profile gap. MONITORING-GRAFANA-1: open.
- **COMPOSE-PROD-PORTS-\*** — database ports exposed in prod. COMPOSE-PROD-PORTS-1: open.
- **PROMETHEUS-PIN-\*** — Prometheus image version pinning. PROMETHEUS-PIN-1: open.
- **PLATFORM-UI-ENV-\*** — Vite `VITE_API_BASE_URL` bakes localhost into bundle. PLATFORM-UI-ENV-1: open.
- **PLATFORM-AUTH-ACQUISITION-\*** — Platform SPA first-party auth. PLATFORM-AUTH-ACQUISITION-1: closed 2026-05-28.
- **PLATFORM-UI-KIT-\*** — ui-kit npm publish gap; local edits require manual rebuild chain. PLATFORM-UI-KIT-1: closed 2026-05-28.

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
