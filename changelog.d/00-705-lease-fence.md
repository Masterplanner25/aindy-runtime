### Changed — background lease fencing: a stale leader's maintenance write is refused (`LEASE-FENCE-1`, #705)

**★ Operators: this release changes the schema.** Alembic `0019` adds
`background_task_leases.fence BIGINT NOT NULL DEFAULT 0` (schema contract `2026-09-16`). It is
additive, so a bare `aindy-runtime bootstrap-schema` exits **3** (additive-reconcile-required) on
an existing deployment — run `bootstrap-schema --reconcile`, or branch on exit code 3 (`FR-14`).
Docker compose runs `alembic upgrade head` before the server and needs nothing else.

- The lease's expiry bounds how long two background leaders can coexist (a GC pause, a disk
  stall) and did nothing about what the stale one *wrote* in that window; a job already running
  when the lease was lost ran to completion as leader. The row now carries a monotonic `fence`
  — 1 on first claim, unchanged on renew, +1 on every takeover — and the two leader-only jobs
  whose re-run is not harmless (`recover_orphaned_approved_runs`, which re-dispatches
  `execute_run`; `deferred_async_job_retry`, which re-dispatches a handler) read it `FOR SHARE`
  inside their own transaction before writing. A takeover cannot commit under an open fenced
  job, and a takeover that already committed refuses the stale leader's write.
- **Behaviour change:** under a leadership split, a maintenance job on the *old* leader now
  logs a warning and does nothing instead of re-dispatching work the new leader also dispatches.
  The `single-instance` (in-process) profile holds no lease and is unaffected.
- New metric `aindy_lease_fence_refusals_total{job}` — the only evidence the fence ever fired.
- `leadership.claim_lease()` returns a `LeaseHold(owner_id, fence, expires_at)`;
  `try_acquire_lease()` keeps its boolean contract. `background_leader_fence()` and
  `assert_lease_fence()` are the API for any future leader-only job that is not idempotent.
- Decisions recorded: DEC-030 … DEC-033. Design: `docs/design/LEASE_FENCE_DESIGN.md`.
