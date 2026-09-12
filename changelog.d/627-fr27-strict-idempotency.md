### Added — strict at-most-once under contention for the idempotency gate (FR-27) (#627)

**Default-off, opt-in via `AINDY_SYSCALL_IDEMPOTENCY_STRICT=1`. PostgreSQL only.**

The `EXACTLY_ONCE` gate is a replay cache for *sequential* duplicates; under *concurrent*
duplicates it degraded every loser of the insert race to `AT_LEAST_ONCE` and ran the handler —
N barrier-released callers of one action ran the handler up to **N** times (a *pending* row does
not protect anything until it is `success`). This was measured and is the gap MEB-1a's docstring
named.

- Strict mode takes a session-scoped `pg_advisory_lock` keyed on the `action_id`, on a dedicated
  connection (never `_gate_db`, never the handler's session), held across `reserve → handler →
  complete`. A concurrent duplicate **blocks** until the winner finishes and then **replays**.
  Measured: handler runs exactly once across 2/4/8/16-way contention; a winner whose handler
  fails is one reclaim+retry, not N.
- New env vars (read at call time, no restart hazard): `AINDY_SYSCALL_IDEMPOTENCY_STRICT`
  (default off) and `AINDY_SYSCALL_IDEMPOTENCY_STRICT_WAIT_SECONDS` (default `300`, the syscall
  wall-clock ceiling). A loser that waits longer than the wait degrades honestly under a new
  `aindy_effect_gate_outcomes_total{outcome="degraded_lock_timeout"}` label — distinct from
  `degraded` (now: lock not attempted — non-PG or flag off) and `degraded_gate_error`.
- **Not a guarantee:** exactly-once across a winner *process* crash (a `pending` row whose lock
  dropped on disconnect still degrades until the stale threshold). Explicitly out of scope.
- Non-PostgreSQL backends return `unsupported` and behave exactly as before (degrade under
  contention). Nothing changes with the flag off.

**Operator note:** turning strict mode on, a blocked duplicate holds one pooled connection while
it waits — size `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` for the expected duplicate fan-in. Design and
measured prototype: `docs/runtime/FR27_ADVISORY_LOCK_DESIGN.md`; contract: `IDEMPOTENCY_CONTRACT.md`.
