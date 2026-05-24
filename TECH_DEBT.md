# Technical Debt

## IDEM-6 — Multi-Instance Bootstrap Race

Status: Deferred — Low Priority

Source: `docs/runtime/IDEMPOTENCY_CONTRACT.md` Open Question #1.

First-ever blank-DB deploy with multiple runtime instances starting simultaneously can
race on `CREATE TABLE`. `checkfirst=True` in `create_all` mitigates but does not fully
eliminate the race. Fix is `pg_try_advisory_lock` around the bootstrap path in
`AINDY/db/database.py` (or whichever function calls `Base.metadata.create_all`).

Trigger: revisit before any multi-instance cold-start deployment in production.

---

## IDEM-7 — Syscall Registry Not-Ready Window

Status: Deferred — Low Priority

Source: `docs/runtime/IDEMPOTENCY_CONTRACT.md` Open Question #2.

Syscall registration is not complete until Phase 8 of startup. HTTP traffic that arrives
between DB-ready and syscall-registry-ready may dispatch against an incomplete registry
and receive spurious "syscall not found" errors. The health endpoint (`/health`) does not
currently assert registry completion, so load balancers may route traffic too early.

Fix is small: extend the health check to assert that `len(SYSCALL_REGISTRY) >= N` (where
N is the expected count of registered syscalls after full boot). See
`AINDY/kernel/syscall_registry.py` and whichever route/service exposes `/health`.

Trigger: revisit the next time the health endpoint is touched for any reason.

---

## IDEM-9 — EffectRecord Table Growth

Status: CLOSED (2026-05-24)

Note: IDEM-8 is already taken (APScheduler stub fix, closed 2026-05-23 — see IDEMPOTENCY_AUDIT.md).

Implemented: `_cleanup_expired_effect_records()` in `AINDY/platform_layer/scheduler_service.py`.
Runs every 24 hours. Deletes finalized rows (status ≠ `pending`, `completed_at IS NOT NULL`)
older than 90 days in batches of 10,000 rows per commit. Pending rows are never deleted.
Supporting index: `ix_effect_records_completed_at_status` (migration 0004).
`SCHEMA_CONTRACT_VERSION` bumped to "2026-05-24.1".

Remaining operational gap: row-count monitoring must still be set up manually. No automated
alert exists. Add a dashboard panel or startup log line that surfaces `effect_records` total
row count so unbounded growth is detected without polling.

---

## SDK Extraction

Status: COMPLETE (2026-05-23)

`aindy-sdk` extracted to standalone repo:
https://github.com/Masterplanner25/aindy-sdk-

First green CI run:
https://github.com/Masterplanner25/aindy-sdk-/actions/runs/26343161733

`AINDY/sdk/` removed from `aindy-runtime` in this commit.

47 SDK tests pass in the standalone repo.

`aindy-runtime` packaging config confirmed - no explicit sdk include
required removal. `pyproject.toml` already used `include = ["AINDY*"]`,
so removing the directory was sufficient.
