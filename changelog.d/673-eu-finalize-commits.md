### Fixed — a request's execution unit now actually reaches `completed` / `failed` (`EU-FINALIZE-UNCOMMITTED-1`, app FR-30) (#673)

Filed by the app team on 2026-09-15 while verifying 2.15.0. **Every route-sourced
`execution_units` row on a live stack has sat `executing` forever, since the table's first row**
(900+ on theirs, 19 more per Tutorial 2 pass), with `execution.completed` on every trace and
nothing logged. `_safe_finalize_eu` wrote the terminal status through
`ExecutionUnitService.update_status`, which only **flushes**; it was the last write on the request
session, the `execution.completed` emit before it had already committed, and `get_db` closes
without committing — so the status was rolled back on every request. The finalize returned True
and the envelope recorded `execution_unit.finalize.completed: ok`; the row disagreed.

- **`_safe_finalize_eu` commits after a successful `update_status`.** Terminal route units now
  read `completed` or `failed`; an operator console's `executing` count means *in flight*.
- **Rows already `executing` are not touched.** Retire them by hand if you count by status:
  `UPDATE execution_units SET status='failed' WHERE status='executing' AND source_type='route'
  AND created_at < '<upgrade time>';`
- **★ The `waiting_flow_runs` rehydration seed skips an id that is not a flow run** (FR-29's
  addendum). `rehydrate_waiting_eus` seeds with `run_id=eu_id` for every waiting unit, and a
  unit id is never a flow-run id — on Postgres that seed raised `ForeignKeyViolation` for every
  waiting unit on every boot since it was written, logged `[rehydrate] … seed failed (non-fatal)`.
  The `flow_runs`-exists guard now lives in the shared seed (both callers). Ten such lines per
  boot on the app's stack, from the units FR-29 leaked; zero now.
- **Correction to the 2.15.0 entry above:** it said FR-29 was "almost certainly the 105
  `job|route` / `flow|route` units left stuck" by the 2026-09-13 tutorial run. Wrong — those
  were `executing`, which is this defect; FR-29 leaks `waiting` rows. Two defects, two statuses.
- **Why no existing test could see it:** the shared test fixtures put the app's request session
  and the test's reading session on one connection inside one outer transaction, where a flushed
  UPDATE reads exactly like a committed one — the FR-29 route tests asserted `completed` and
  passed on the broken code. The new suite reads through a separate connection with no shared
  transaction and runs a liveness control first (a flush-then-close must read as rolled back).
  Mutation-checked: commit removed → 3/3 route tests fail; seed guard removed → its test fails.
  **Not re-run live.**
