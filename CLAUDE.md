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
